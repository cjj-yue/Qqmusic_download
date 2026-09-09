"""Resume a complete QQ Music playlist export: native FLAC, otherwise MP3.

Authentication and song audio keys stay in memory and are used only with QQ
Music. Each final FLAC must match the source STREAMINFO PCM hash and sample
count, and must pass strict full decoding after normalization and tagging.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from export_audio import first_bytes, key_variants, stream_info
from qq_api import request_song, session_candidates, refresh_song
from runtime_tools import find_tool

ROOT = Path(__file__).resolve().parent
API_LOCK = threading.Lock()
LOG_LOCK = threading.Lock()
LAST_API = 0.0


def atomic_json(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    # A reader on a Windows network share can briefly deny delete/rename.
    # Keep the old complete manifest until the atomic replacement succeeds.
    for attempt in range(40):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.25)


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', value)
    return re.sub(r'\s+', ' ', value).strip(' .')[:135] or '未命名'


def get_info(session, song, quality):
    global LAST_API
    with API_LOCK:
        pause = 0.45 - (time.monotonic() - LAST_API)
        if pause > 0:
            time.sleep(pause)
        LAST_API = time.monotonic()
    return request_song(session, song, quality)


def run_ffmpeg(arguments, input_data=None):
    result = subprocess.run([find_tool('ffmpeg'), '-nostdin', '-hide_banner', '-v', 'error', *arguments],
                            input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=240)
    if result.returncode:
        raise RuntimeError('audio_decoder_failed')
    return result


def pcm_decode(file, bits, strict=False):
    options = ['-xerror'] if strict else []
    codec = 'pcm_s24le' if bits == 24 else 'pcm_s32le' if bits == 32 else 'pcm_s16le'
    fmt = 's24le' if bits == 24 else 's32le' if bits == 32 else 's16le'
    result = run_ffmpeg([*options, '-i', str(file), '-map', '0:a:0', '-c:a', codec, '-f', fmt, '-'])
    if strict and result.stderr.strip():
        raise RuntimeError('strict_audio_decode_warning')
    return result.stdout


def download(url, target):
    request = urllib.request.Request(url, headers={'User-Agent': 'QQMusic/21', 'Referer': 'https://y.qq.com/'})
    with urllib.request.urlopen(request, timeout=40) as response, target.open('wb') as output:
        expected = int(response.headers.get('Content-Length', '0'))
        copied = 0
        while block := response.read(1024 * 1024):
            output.write(block)
            copied += len(block)
    if copied < 1024 or (expected and copied != expected):
        raise RuntimeError('incomplete_download')
    return copied


def _synchsafe(value):
    return bytes(((value >> 21) & 0x7f, (value >> 14) & 0x7f, (value >> 7) & 0x7f, value & 0x7f))


def _id3_text_frame(name, value):
    # ID3v2.3 text frames use a one-byte encoding indicator followed by UTF-16
    # with a BOM.  Creating only the tag avoids rewriting MP3 frames, which
    # preserves the original encoder delay and gapless playback metadata.
    payload = b'\x01' + value.encode('utf-16')
    return name.encode('ascii') + len(payload).to_bytes(4, 'big') + b'\0\0' + payload


def copy_mp3_with_tags(source, target, record, index):
    frames = b''.join((_id3_text_frame('TIT2', record['title']),
                       _id3_text_frame('TPE1', record['artist']),
                       _id3_text_frame('TALB', record['album']),
                       _id3_text_frame('TRCK', str(index))))
    tag = b'ID3\x03\0\0' + _synchsafe(len(frames)) + frames
    with source.open('rb') as original, target.open('xb') as output:
        header = original.read(10)
        if header[:3] == b'ID3' and len(header) == 10:
            size = ((header[6] & 0x7f) << 21) | ((header[7] & 0x7f) << 14) | ((header[8] & 0x7f) << 7) | (header[9] & 0x7f)
            original.seek(10 + size + (10 if header[5] & 0x10 else 0))
        else:
            original.seek(0)
        output.write(tag)
        shutil.copyfileobj(original, output, 1024 * 1024)


def checked_audio_key(ekey, header):
    for key in key_variants(ekey.encode('ascii')):
        if stream_info(first_bytes(key, header)):
            return key
    return None


def export_one(session, song, index, output, work, filename_base, qualities=('flac', '320', '128')):
    record = dict(index=index, song_id=song['songid'], song_mid=song['songmid'],
                  title=song['songname'], artist=' / '.join(s['name'] for s in song['singer']),
                  album=song['albumname'], status='pending')
    staging = work / str(song['songid'])
    staging.mkdir(parents=True, exist_ok=True)
    metadata = ['-metadata', 'title=' + record['title'], '-metadata', 'artist=' + record['artist'], '-metadata', 'album=' + record['album'], '-metadata', 'track=' + str(index)]
    info = None
    problems = []
    for quality in qualities:
        for attempt in range(3):
            try:
                offer = get_info(session, song, quality)
                if quality == 'flac' and attempt == 0 and not offer['url']:
                    refreshed = refresh_song(song)
                    if refreshed.get('strMediaMid') != song.get('strMediaMid'):
                        song = refreshed
                        offer = get_info(session, song, quality)
                    else:
                        song = refreshed
                if not offer['url']:
                    problems.append(dict(quality=quality, code=offer['request_code'], result=offer['result']))
                    break
                encrypted = staging / ('input.flac' if quality == 'flac' else 'input.mp3')
                download(offer['url'], encrypted)
                if quality == 'flac':
                    with encrypted.open('rb') as source:
                        header = source.read(128)
                    details = stream_info(header)
                    if details:
                        raw = encrypted
                    else:
                        if not offer['ekey']:
                            raise RuntimeError('missing_audio_key')
                        key = checked_audio_key(offer['ekey'], header)
                        if key is None:
                            raise RuntimeError('unsupported_audio_key')
                        details = stream_info(first_bytes(key, header))
                        raw = staging / 'decrypted.flac'
                        if raw.exists():
                            raw.unlink()
                        result = subprocess.run([find_tool('node'), str(ROOT / 'qmc_decrypt.cjs')],
                                                input=json.dumps(dict(source=str(encrypted), output=str(raw), key=base64.b64encode(key).decode())).encode(),
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW, timeout=120)
                        if result.returncode:
                            raise RuntimeError('audio_decryption_failed')
                    if abs(details['duration_seconds'] - song['interval']) > 5:
                        raise RuntimeError('song_duration_mismatch')
                    pcm = pcm_decode(raw, details['bits'])
                    samples = len(pcm) // (details['channels'] * (details['bits'] // 8))
                    md5 = hashlib.md5(pcm).hexdigest()
                    if samples != details['samples'] or md5 != details['pcm_md5']:
                        raise RuntimeError('original_audio_checksum_mismatch')
                    destination = output / (filename_base + '.flac')
                    partial = output / (filename_base + '.part.flac')
                    fmt = 's24le' if details['bits'] == 24 else 's32le' if details['bits'] == 32 else 's16le'
                    if partial.exists():
                        partial.unlink()
                    run_ffmpeg(['-xerror', '-n', '-f', fmt, '-ar', str(details['sample_rate']), '-ac', str(details['channels']), '-i', 'pipe:0', '-c:a', 'flac', '-compression_level', '5', *metadata, str(partial)], pcm)
                    validated = pcm_decode(partial, details['bits'], strict=True)
                    if len(validated) != len(pcm) or hashlib.md5(validated).hexdigest() != md5:
                        raise RuntimeError('final_audio_checksum_mismatch')
                    info = dict(format='FLAC', quality='lossless', **details, full_decode_verified=True, original_pcm_md5_matches=True)
                else:
                    # Decode the complete MP3 and retain the original compressed stream.
                    pcm = run_ffmpeg(['-xerror', '-i', str(encrypted), '-map', '0:a:0', '-ac', '2', '-ar', '44100', '-c:a', 'pcm_s16le', '-f', 's16le', '-']).stdout
                    duration = len(pcm) / (44100 * 4)
                    if abs(duration - song['interval']) > 5:
                        raise RuntimeError('mp3_song_duration_mismatch')
                    destination = output / (filename_base + '.mp3')
                    partial = output / (filename_base + '.part.mp3')
                    if partial.exists():
                        partial.unlink()
                    copy_mp3_with_tags(encrypted, partial, record, index)
                    validated = run_ffmpeg(['-xerror', '-i', str(partial), '-map', '0:a:0', '-ac', '2', '-ar', '44100', '-c:a', 'pcm_s16le', '-f', 's16le', '-']).stdout
                    if len(validated) != len(pcm) or hashlib.md5(validated).digest() != hashlib.md5(pcm).digest():
                        raise RuntimeError('mp3_remux_verification_failed')
                    info = dict(format='MP3', quality=quality + ' kbps', duration_seconds=round(duration, 6), full_decode_verified=True,
                                flac_unavailable=not bool(song.get('sizeflac')), flac_attempts=problems.copy())
                if destination.exists():
                    # A run interrupted just after the final rename can leave a
                    # fully valid file without its manifest entry. Reuse only an
                    # exact match; never overwrite an unrelated existing file.
                    if hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(partial.read_bytes()).digest():
                        raise RuntimeError('destination_exists_without_matching_resume_record')
                    partial.unlink()
                else:
                    partial.replace(destination)
                digest = hashlib.sha256()
                with destination.open('rb') as final:
                    while block := final.read(1024 * 1024):
                        digest.update(block)
                record.update(status='complete', filename=destination.name, bytes=destination.stat().st_size, sha256=digest.hexdigest(), **info)
                for file in staging.iterdir():
                    if file.is_file():
                        file.unlink()
                staging.rmdir()
                return record
            except urllib.error.HTTPError as error:
                category = 'http_' + str(error.code)
            except Exception as error:
                category = str(error) if isinstance(error, RuntimeError) else type(error).__name__
            if attempt == 2:
                problems.append(dict(quality=quality, error=category))
            else:
                time.sleep(2 * (attempt + 1))
        # A declared lossless track with a technical failure must not silently
        # become MP3. Retry it later so the user's lossless preference is met.
        if quality == 'flac' and song.get('sizeflac') and any(p.get('error') for p in problems):
            break
    record.update(status='failed', attempts=problems)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--playlist', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--work', type=Path, help='Optional local staging directory, separate from the final output.')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--song-id', type=int, help='Process one playlist entry for a focused validation run.')
    args = parser.parse_args()
    playlist = json.loads(args.playlist.read_text(encoding='utf-8'))['cdlist'][0]
    songs = playlist['songlist']
    if len({s['songid'] for s in songs}) != len(songs) or any(not isinstance(s['songid'], int) or s['songid'] <= 0 for s in songs):
        raise RuntimeError('Playlist must contain unique positive numeric song IDs.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = (args.work or (ROOT / 'batch-work')).resolve()
    work.mkdir(parents=True, exist_ok=True)
    playlist_id = str(playlist.get('disstid') or playlist.get('id') or playlist.get('dissid'))
    if not playlist_id:
        raise RuntimeError('Playlist does not expose a stable playlist ID.')
    manifest_path = output / '导出清单.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else dict(playlist_id=playlist_id, playlist_name=playlist['dissname'], expected_total=len(songs), tracks={})
    if str(manifest.get('playlist_id')) != playlist_id or manifest.get('expected_total') != len(songs):
        raise RuntimeError('Existing manifest belongs to a different playlist.')
    session = None
    for candidate in session_candidates(args.pid):
        try:
            check = get_info(candidate, songs[0], 'flac')
            if check['url'] and check['ekey']:
                session = candidate
                break
        except Exception:
            continue
    if session is None:
        raise RuntimeError('A valid current QQ Music session was not found.')
    names = {}
    tasks = []
    for index, song in enumerate(songs, 1):
        name = safe_name('、'.join(s['name'] for s in song['singer']) + ' - ' + song['songname'])
        if name.casefold() in names:
            name += ' [' + str(song['songid']) + ']'
        names[name.casefold()] = song['songid']
        if args.song_id and song['songid'] != args.song_id:
            continue
        previous = manifest['tracks'].get(str(song['songid']), {})
        file = output / previous.get('filename', '__missing__')
        if previous.get('status') == 'complete' and file.is_file() and file.stat().st_size == previous.get('bytes'):
            continue
        tasks.append((song, index, name))
    if args.limit:
        tasks = tasks[:args.limit]
    manifest.update(running=True, process_id=os.getpid(), started_at=time.strftime('%Y-%m-%d %H:%M:%S'), last_run_task_count=len(tasks))
    atomic_json(manifest_path, manifest)
    print(json.dumps(dict(event='started', pid=os.getpid(), playlist=playlist['dissname'], total=len(songs), queued=len(tasks), output=str(output)), ensure_ascii=False), flush=True)
    completed_this_run = 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            iterator = iter(tasks)
            futures = {}
            exhausted = False
            while futures or not exhausted:
                if (ROOT / 'batch.stop').exists():
                    exhausted = True
                while not exhausted and len(futures) < args.workers:
                    item = next(iterator, None)
                    if item is None:
                        exhausted = True
                        break
                    song, index, name = item
                    futures[pool.submit(export_one, session, song, index, output, work, name)] = song
                if not futures:
                    break
                ready, _ = wait(futures, timeout=10, return_when=FIRST_COMPLETED)
                for future in ready:
                    song = futures.pop(future)
                    try:
                        result = future.result()
                    except Exception as error:
                        result = dict(song_id=song['songid'], title=song['songname'], status='failed', error_type=type(error).__name__)
                    manifest['tracks'][str(song['songid'])] = result
                    completed_this_run += 1
                    done = sum(t.get('status') == 'complete' for t in manifest['tracks'].values())
                    manifest.update(completed=done, failed=sum(t.get('status') == 'failed' for t in manifest['tracks'].values()), updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
                    atomic_json(manifest_path, manifest)
                    print(json.dumps(dict(event='track', completed=done, total=len(songs), title=result.get('title'), status=result['status'], format=result.get('format'), attempts=result.get('attempts')), ensure_ascii=False), flush=True)
    finally:
        manifest.update(running=False, updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
        atomic_json(manifest_path, manifest)
        lines = ['#EXTM3U']
        for song in songs:
            track = manifest['tracks'].get(str(song['songid']), {})
            if track.get('status') == 'complete':
                lines += ['#EXTINF:' + str(song['interval']) + ',' + track.get('artist', '') + ' - ' + song['songname'], track['filename']]
        (output / '下载歌单.m3u8').write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
    print(json.dumps(dict(event='finished', completed=manifest.get('completed', 0), failed=manifest.get('failed', 0), total=len(songs)), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
