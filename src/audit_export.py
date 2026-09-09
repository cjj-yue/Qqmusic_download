"""Verify delivered files against the full playlist and recorded audio checks."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import time


def save(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(40):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.25)


def load_json(path):
    for attempt in range(40):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.25)


def audit(playlist, output, cache):
    manifest = load_json(output / '导出清单.json')
    songs = playlist['songlist']
    problems, verified, seen = [], [], set()
    for index, song in enumerate(songs, 1):
        sid = str(song['songid'])
        record = manifest['tracks'].get(sid, {})
        issue = None
        if record.get('status') != 'complete':
            issue = '尚未完成' if record.get('status') != 'failed' else '未能获取或校验原曲'
        else:
            name = record.get('filename', '')
            file = output / name
            if Path(name).name != name or not name or name.casefold() in seen:
                issue = '文件名或曲目对应异常'
            else:
                seen.add(name.casefold())
                try:
                    stat = file.stat()
                    stamp = dict(filename=name, bytes=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256=record['sha256'])
                    if stat.st_size != record.get('bytes'):
                        issue = '文件大小不匹配'
                    elif not record.get('full_decode_verified') or (record['format'] == 'FLAC' and not record.get('original_pcm_md5_matches')):
                        issue = '缺少整首音频校验'
                    elif cache.get(sid) != stamp:
                        digest = hashlib.sha256()
                        with file.open('rb') as source:
                            while block := source.read(1024 * 1024):
                                digest.update(block)
                        if digest.hexdigest() != record['sha256']:
                            issue = '文件校验值不匹配'
                        else:
                            cache[sid] = stamp
                except OSError:
                    issue = '文件无法读取'
            if issue is None:
                verified.append(record)
        if issue:
            problems.append(dict(index=index, song_id=song['songid'], title=song['songname'], artist=' / '.join(s['name'] for s in song['singer']), reason=issue, attempts=record.get('attempts', [])))
    report = dict(playlist_id=str(manifest.get('playlist_id')), expected=len(songs), verified=len(verified), flac=sum(t['format'] == 'FLAC' for t in verified), mp3=sum(t['format'] == 'MP3' for t in verified), bytes=sum(t['bytes'] for t in verified), unresolved=len(problems), batch_running=manifest.get('running', False), all_complete=len(verified) == len(songs), checked_at=time.strftime('%Y-%m-%d %H:%M:%S'), problems=problems)
    save(output / '校验记录.json', cache)
    save(output / '验收报告.json', report)
    if not report['batch_running']:
        with (output / '未下载清单.csv').open('w', encoding='utf-8-sig', newline='') as target:
            writer = csv.DictWriter(target, fieldnames=['序号', '歌曲编号', '歌名', '歌手', '原因'])
            writer.writeheader()
            for item in problems:
                writer.writerow(dict(zip(writer.fieldnames, (item['index'], item['song_id'], item['title'], item['artist'], item['reason']))))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--playlist', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    playlist = json.loads(args.playlist.read_text(encoding='utf-8'))['cdlist'][0]
    output = args.output.resolve()
    cache_file = output / '校验记录.json'
    cache = load_json(cache_file) if cache_file.exists() else {}
    last = -100
    while True:
        report = audit(playlist, output, cache)
        if report['verified'] - last >= 100 or not report['batch_running']:
            print(json.dumps({k:v for k,v in report.items() if k != 'problems'}, ensure_ascii=False), flush=True)
            last = report['verified']
        if not args.watch or not report['batch_running']:
            break
        time.sleep(20)


if __name__ == '__main__':
    main()
