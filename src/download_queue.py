"""Sequential queue with explicit terminal states and one shared download log."""
from pathlib import Path
import tempfile

from music_download import audio_offers, download_selected, get_audio_sessions
from download_log import append_log, LOG_NAME

POLICIES = {'best': '无损优先，没有则 MP3', '320': 'MP3 优先 320，允许 128',
            'flac': '仅原生无损，其他跳过', '128': '仅 MP3 128 kbps'}
FORMATS = {'original': '保留源格式', 'mp3': '统一 MP3', 'wav': '统一 WAV（文件较大）'}


def pick_quality(offers, policy):
    order = {'best': ('flac', '320', '128'), '320': ('320', '128'), 'flac': ('flac',), '128': ('128',)}[policy]
    return next((q for q in order if q in offers), None)


def run_queue(songs, policy, fmt, folder, cancel, notify, use_account=False):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=folder):
        pass
    records = [dict(song_id=s['songid'], title=s['songname'], status='pending') for s in songs]
    result = dict(total=len(songs), processed=0, success=0, failed=0, skipped=0, cancelled=False,
                  policy=policy, output_format=fmt, records=records, report=str(folder / LOG_NAME), log_error=False)
    def log(event, **fields):
        if not append_log(folder, event, **fields):
            result['log_error'] = True
    log('批量开始', 总数=len(songs), 策略=POLICIES[policy], 格式=FORMATS[fmt])
    sessions = get_audio_sessions(progress=lambda text: notify('progress', text), use_account=use_account) if not cancel.is_set() else []
    for index, song in enumerate(songs):
        if cancel.is_set():
            break
        record = records[index]
        notify('queue_item', dict(song_id=song['songid'], status='检测音质'))
        try:
            offers = audio_offers(song, sessions=sessions, progress=lambda text: notify('progress', text))
            if cancel.is_set():
                break
            quality = pick_quality(offers, policy)
            if quality is None:
                record.update(status='skipped', reason='没有符合所选策略的可用音质')
                result['skipped'] += 1
            else:
                notify('queue_item', dict(song_id=song['songid'], status='下载 / 转换 / 校验', quality=quality))
                path = download_selected(song, quality, offers[quality], fmt, folder, write_log=False)
                record.update(status='complete', path=str(path), quality=quality)
                result['success'] += 1
        except Exception as error:
            # Exception strings may contain cookies or signed URLs.
            record.update(status='failed', error_type=type(error).__name__)
            result['failed'] += 1
        result['processed'] += 1
        notify('queue_item', dict(song_id=song['songid'], status={'complete': '已完成', 'failed': '失败', 'skipped': '已跳过'}[record['status']], quality=record.get('quality', '')))
        notify('queue_progress', {key: result[key] for key in ('processed', 'total', 'success', 'failed', 'skipped')})
        details = dict(歌曲=record['title'], ID=record['song_id'])
        if record['status'] == 'complete':
            details.update(音质=record['quality'], 文件=Path(record['path']).name, 校验='通过')
        elif record['status'] == 'failed':
            details['错误'] = record['error_type']
        else:
            details['原因'] = record['reason']
        log({'complete': '完成', 'failed': '失败', 'skipped': '跳过'}[record['status']], **details)
    result['cancelled'] = result['processed'] < result['total']
    for record in records:
        if record['status'] == 'pending':
            record['status'] = 'cancelled'
            notify('queue_item', dict(song_id=record['song_id'], status='未下载'))
    log('批量停止' if result['cancelled'] else '批量结束', 已处理=result['processed'], 总数=result['total'],
        成功=result['success'], 失败=result['failed'], 跳过=result['skipped'], 未下载=result['total']-result['processed'])
    return result
