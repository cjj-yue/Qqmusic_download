"""Interactive official song search, audio selection, export and conversion."""
import argparse
import csv
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

from batch_export import export_one, pcm_decode, run_ffmpeg, safe_name
from qq_api import request_song, session_candidates
from qq_search import search_songs
from download_log import append_log

QUALITY_NAMES = {'flac': 'FLAC 原生无损', '320': 'MP3 320 kbps', '128': 'MP3 128 kbps'}


def choose(prompt, options):
    for index, (_, label) in enumerate(options, 1):
        print(f'  {index}. {label}')
    print('  0. 返回搜索')
    while True:
        answer = input(prompt).strip()
        if answer == '0':
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        print('请输入列表中的序号。')


def client_pid():
    result = subprocess.run(['tasklist.exe', '/FI', 'IMAGENAME eq QQMusic.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
    for row in csv.reader(io.StringIO(result.stdout.decode(errors='replace'))):
        if len(row) > 1 and row[0].casefold() == 'qqmusic.exe' and row[1].isdigit():
            return int(row[1])
    return None


def get_audio_sessions(pid=None, progress=None, use_account=False):
    """Collect once per operation, keeping credentials in RAM only."""
    report = progress or print
    anonymous = {'uin': '0', 'key': ''}
    sessions = [anonymous]
    if not use_account:
        report('正在检查公开资源。')
        return sessions
    pid = pid or client_pid()
    if pid:
        report('正在读取本机 QQ 音乐会话，随后检测所选歌曲音质……')
        try:
            for session in session_candidates(pid):
                sessions.append(session)
                if len(sessions) >= 5:
                    break
        except (OSError, RuntimeError):
            report('本机会话读取失败，继续检查已取得的会话和公开资源。')
    else:
        report('未发现桌面 QQ 音乐进程，正在检查公开资源。')
    return sessions


def audio_offers(song, pid=None, progress=None, sessions=None, use_account=False):
    """Keep credentials and signed offers in RAM; never serialize them."""
    report = progress or print
    available = {}
    sessions = get_audio_sessions(pid, report, use_account) if sessions is None else sessions
    for session in sessions:
        for quality in QUALITY_NAMES:
            if quality in available:
                continue
            report('正在检测：' + QUALITY_NAMES[quality])
            try:
                response = request_song(session, song, quality)
            except (OSError, ValueError, KeyError):
                report('一次音质查询失败，继续检查其余音质；可稍后重新检测。')
                continue
            if response['url']:
                available[quality] = session
            time.sleep(0.2)
        if len(available) == len(QUALITY_NAMES):
            break
    return available


def output_options(quality):
    options = [('original', '保留源格式（不再转换）'), ('mp3', 'MP3（无损源转换为 320 kbps）')]
    if quality == 'flac':
        options.append(('flac', 'FLAC（保留无损）'))
    options.append(('wav', 'WAV（文件较大）'))
    return options


def convert_audio(source, record, chosen_format, directory):
    source_format = record['format'].lower()
    target_format = source_format if chosen_format == 'original' else chosen_format
    if target_format == 'flac' and source_format != 'flac':
        raise RuntimeError('当前源是有损 MP3，不能生成原生无损 FLAC。')
    if target_format == source_format:
        return source, dict(output_format=target_format.upper(), conversion='none', output_full_decode_verified=True)
    target = directory / ('converted.' + target_format)
    if target_format == 'wav':
        bits = record.get('bits', 16)
        codec = {16: 'pcm_s16le', 24: 'pcm_s24le', 32: 'pcm_s32le'}[bits]
        run_ffmpeg(['-xerror', '-n', '-i', str(source), '-map', '0:a:0', '-c:a', codec, str(target)])
        before = pcm_decode(source, bits, strict=True)
        after = pcm_decode(target, bits, strict=True)
        if len(before) != len(after) or hashlib.md5(before).digest() != hashlib.md5(after).digest():
            raise RuntimeError('WAV 转换后的音频校验未通过。')
    elif target_format == 'mp3':
        run_ffmpeg(['-xerror', '-n', '-i', str(source), '-map', '0:a:0', '-c:a', 'libmp3lame', '-b:a', '320k',
                    '-id3v2_version', '3', str(target)])
        result = run_ffmpeg(['-xerror', '-i', str(target), '-map', '0:a:0', '-ac', '2', '-ar', '44100',
                             '-c:a', 'pcm_s16le', '-f', 's16le', '-'])
        duration = len(result.stdout) / (44100 * 4)
        if result.stderr.strip() or abs(duration - record['duration_seconds']) > 0.15:
            raise RuntimeError('MP3 转换后的完整性校验未通过。')
    else:
        raise RuntimeError('不支持的输出格式。')
    return target, dict(output_format=target_format.upper(), conversion=source_format + ' -> ' + target_format,
                        output_full_decode_verified=True)


def publish(source, output, stem):
    """Read back network output and atomically publish without replacing files."""
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / ('.' + uuid.uuid4().hex + '.part' + source.suffix)
    digest = hashlib.sha256()
    try:
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            while block := incoming.read(1024 * 1024):
                digest.update(block)
                outgoing.write(block)
        with temporary.open('rb') as delivered:
            if hashlib.file_digest(delivered, 'sha256').hexdigest() != digest.hexdigest():
                raise RuntimeError('目标目录中的文件校验不一致，未发布成品。')
        for number in range(10000):
            suffix = '' if number == 0 else f' ({number + 1})'
            destination = output / (stem + suffix + source.suffix)
            if destination.exists():
                continue
            try:
                # Windows rename refuses to overwrite an existing destination.
                temporary.rename(destination)
                return destination, digest.hexdigest()
            except FileExistsError:
                continue
        raise RuntimeError('同名文件过多，请选择其他保存目录。')
    finally:
        if temporary.exists():
            temporary.unlink()


def download_selected(song, quality, session, chosen_format, output, *, write_log=True):
    try:
        destination = _download_selected(song, quality, session, chosen_format, output)
    except Exception as error:
        if write_log:
            append_log(output, '失败', 歌曲=song['songname'], ID=song['songid'], 错误=type(error).__name__)
        raise
    if write_log:
        if not append_log(output, '完成', 歌曲=song['songname'], ID=song['songid'], 音质=QUALITY_NAMES[quality],
                          文件=destination.name, 校验='通过'):
            print('音频已完成，但日志未能写入。')
    return destination


def _download_selected(song, quality, session, chosen_format, output):
    with tempfile.TemporaryDirectory(prefix='qqmusic-interactive-') as folder:
        root = Path(folder)
        stage = root / 'source'
        stage.mkdir()
        record = export_one(session, song, 1, stage, root / 'work', 'audio', qualities=(quality,))
        if record.get('status') != 'complete':
            raise RuntimeError('所选音质无法下载或未通过整首校验，请选择其他音质或重新搜索。')
        source, converted = convert_audio(stage / record['filename'], record, chosen_format, root)
        artist = '、'.join(item['name'] for item in song['singer'])
        stem = safe_name(f'{artist} - {song["songname"]}') + f' [{song["songid"]}-{quality}]'
        destination, digest = publish(source, output, stem)
        return destination


def run_query(query, artist, output, pid=None, search_only=False, use_account=False):
    print('正在检索 QQ 音乐官方歌曲信息……')
    songs = search_songs(query, artist=artist)
    if not songs:
        print('没有取得官方搜索结果。可以缩短歌名，或在歌名后加上歌手再试。')
        return
    print('\n官方搜索结果（搜索建议可能只返回少量结果）：')
    options = []
    for song in songs:
        performers = ' / '.join(item['name'] for item in song['singer'])
        duration = f'{song["interval"] // 60}:{song["interval"] % 60:02d}'
        options.append((song, f'{song["songname"]} — {performers} | {song["albumname"]} | {duration} | 编号 {song["songid"]}'))
    if search_only:
        for i, (_, label) in enumerate(options, 1):
            print(f'{i}. {label}')
        return
    song = choose('选择要下载的歌曲版本：', options)
    if song is None:
        return
    offers = audio_offers(song, pid, use_account=use_account)
    if not offers:
        print('该版本未返回可下载资源。请检查 QQ 音乐登录状态、账号权限，或选择其他版本。')
        return
    print('\n当前可取得的源音质：')
    quality = choose('选择源音质：', [(q, QUALITY_NAMES[q]) for q in QUALITY_NAMES if q in offers])
    if quality is None:
        return
    print('\n输出格式（转换不会提高原始音质）：')
    chosen_format = choose('选择输出格式：', output_options(quality))
    if chosen_format is None:
        return
    print('正在下载、转换并校验整首音频，请稍候……')
    destination = download_selected(song, quality, offers[quality], chosen_format, output)
    print('\n已完成：' + str(destination))


def main():
    parser = argparse.ArgumentParser(description='输入歌名，选择官方版本、源音质和输出格式后下载。')
    parser.add_argument('--query', help='歌名；省略时交互输入')
    parser.add_argument('--artist', default='', help='可选歌手名，用于细化搜索')
    parser.add_argument('--output', type=Path, default=Path.home() / 'Music' / 'QQmusic', help='保存目录')
    parser.add_argument('--use-account', action='store_true', help='允许读取本机 QQMusic.exe 当前账号会话')
    parser.add_argument('--pid', type=int, help='可选 QQMusic.exe 进程编号，通常自动识别')
    parser.add_argument('--search-only', action='store_true', help='只检索，不读取会话或下载')
    args = parser.parse_args()
    print('QQ 音乐歌曲下载\n保存位置：' + str(args.output))
    print('可用版本与音质由官方资源及当前账号权限决定。\n')
    while True:
        try:
            query = args.query or input('输入歌名（可带歌手，直接回车退出）：').strip()
            if not query:
                break
            run_query(query, args.artist, args.output, args.pid, args.search_only, args.use_account)
        except (EOFError, KeyboardInterrupt):
            print('\n已退出。')
            break
        except RuntimeError as error:
            # Export internals emit only category codes, never signed URLs.
            print('处理失败：' + str(error))
        except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError):
            print('处理失败：网络、目录或运行环境不可用。请检查连接、QQ 音乐登录和保存目录后重试。')
        if args.query:
            break


if __name__ == '__main__':
    main()
