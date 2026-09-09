"""QQ Music API access using the current QQMusic.exe session, kept in RAM only."""
import ctypes as ct
from ctypes import wintypes as wt
import json
import os
from pathlib import Path
import re
import time
import urllib.request
import urllib.error


class NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'credential_redirect_refused', headers, fp)


def active_account_uin():
    """Only match the account currently selected in the desktop client."""
    appdata = os.environ.get('APPDATA')
    if not appdata:
        return None
    path = Path(appdata) / 'Tencent/QQMusic/QQMusicServiceConfig.ini'
    try:
        text = path.read_bytes().decode('utf-8-sig', errors='replace')
    except OSError:
        return None
    section = re.search(r'(?ms)^\[Account\]\s*\n(.*?)(?=^\[|\Z)', text)
    match = re.search(r'(?m)^Uin\s*=\s*(\d+)\s*$', section[1]) if section else None
    return match[1] if match and match[1] != '0' else None


def session_candidates(pid, seconds=20):
    current = active_account_uin()
    if not current:
        return
    kernel = ct.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)]
    kernel.ReadProcessMemory.argtypes = [wt.HANDLE, ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.POINTER(ct.c_size_t)]
    class MBI(ct.Structure):
        _fields_ = [('BaseAddress', ct.c_void_p), ('AllocationBase', ct.c_void_p), ('AllocationProtect', wt.DWORD), ('PartitionId', wt.WORD), ('RegionSize', ct.c_size_t), ('State', wt.DWORD), ('Protect', wt.DWORD), ('Type', wt.DWORD)]
    kernel.VirtualQueryEx.argtypes = [wt.HANDLE, ct.c_void_p, ct.POINTER(MBI), ct.c_size_t]
    kernel.VirtualQueryEx.restype = ct.c_size_t
    handle = kernel.OpenProcess(0x0410, False, pid)
    if not handle:
        raise RuntimeError('QQ Music session is unavailable.')
    found = set()
    try:
        name, size = ct.create_unicode_buffer(32768), wt.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(handle, 0, name, ct.byref(size)) or Path(name.value).name.lower() != 'qqmusic.exe':
            raise RuntimeError('The selected process is not QQMusic.exe.')
        address, deadline = 0, time.monotonic() + seconds
        while time.monotonic() < deadline:
            info = MBI()
            if not kernel.VirtualQueryEx(handle, address, ct.byref(info), ct.sizeof(info)):
                break
            base, length = info.BaseAddress or 0, info.RegionSize
            address = base + length
            if not length or address <= base:
                break
            if info.State != 0x1000 or info.Type != 0x20000 or info.Protect & 0x100 or (info.Protect & 0xff) not in (2, 4, 8, 0x20, 0x40, 0x80):
                continue
            for offset in range(0, length, 2 * 1024 * 1024):
                if time.monotonic() >= deadline:
                    return
                n = min(2 * 1024 * 1024 + 2048, length - offset)
                buf, got = ct.create_string_buffer(n), ct.c_size_t()
                kernel.ReadProcessMemory(handle, base + offset, buf, n, ct.byref(got))
                data = buf.raw[:got.value]
                for match in re.finditer(rb'qqmusic_key=', data):
                    nearby = data[max(0, match.start() - 512):match.start() + 2048]
                    token = re.search(rb'qqmusic_key=([A-Za-z0-9._%+-]{10,512})', nearby)
                    account = re.search(rb'qqmusic_uin=([0-9]{4,20})', nearby)
                    if token and account:
                        pair = (account[1].decode('ascii'), token[1].decode('ascii'))
                        if pair[0] == current and pair not in found:
                            if active_account_uin() != current:
                                return
                            found.add(pair)
                            yield dict(uin=pair[0], key=pair[1])
    finally:
        kernel.CloseHandle(handle)


def request_song(session, song, quality='flac'):
    prefix, suffix = {'flac': ('F0M0', '.mflac'), '320': ('M800', '.mp3'), '128': ('M500', '.mp3'), 'ogg': ('O8M0', '.mgg')}[quality]
    media = song.get('strMediaMid') or song.get('file', {}).get('media_mid')
    mid = song.get('songmid') or song.get('mid')
    filename = prefix + media + suffix
    logged_in = bool(session and session.get('uin') != '0' and session.get('key'))
    uin = session['uin'] if logged_in else '0'
    body = {'comm': {'cv': 4747474, 'ct': 24, 'format': 'json', 'uin': int(uin), 'g_tk': 5381},
            'req_1': {'module': 'vkey.GetVkeyServer', 'method': 'CgiGetVkey',
                      'param': {'filename': [filename], 'guid': '10000', 'songmid': [mid], 'songtype': [0], 'uin': uin, 'loginflag': int(logged_in), 'platform': '20'}}}
    headers = {'Content-Type': 'application/json', 'Referer': 'https://y.qq.com/', 'User-Agent': 'QQMusic/21'}
    if logged_in:
        headers['Cookie'] = 'qqmusic_key=' + session['key'] + '; qqmusic_uin=' + uin
    request = urllib.request.Request('https://u.y.qq.com/cgi-bin/musicu.fcg', data=json.dumps(body).encode(),
                                     headers=headers, method='POST')
    opener = urllib.request.build_opener(NoCredentialRedirect()) if logged_in else None
    response_context = opener.open(request, timeout=20) if opener else urllib.request.urlopen(request, timeout=20)
    with response_context as response:
        result = json.load(response)
    data = result.get('req_1', {}).get('data', {})
    entries = data.get('midurlinfo', [])
    entry = entries[0] if entries else {}
    purl = entry.get('purl') or ''
    sip = data.get('sip') or []
    url = sip[0] + purl if sip and purl else ''
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    return dict(url=url, ekey=entry.get('ekey') or '', filename=filename,
                code=result.get('code'), request_code=result.get('req_1', {}).get('code'), result=entry.get('result'), quality=quality)


def refresh_song(song):
    """Refresh the same recording's media identifier; never substitute a song."""
    url = 'https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg?songid=' + str(song['songid']) + '&tpl=yqq_song_detail&format=json'
    with urllib.request.urlopen(url, timeout=15) as response:
        result = json.load(response)
    entries = result.get('data') or []
    if not entries or entries[0].get('id') != song['songid']:
        return song
    current = entries[0]
    updated = song.copy()
    media = current.get('file', {})
    updated.update(strMediaMid=media.get('media_mid') or song['strMediaMid'],
                   sizeflac=media.get('size_flac', song.get('sizeflac', 0)),
                   size320=media.get('size_320mp3', song.get('size320', 0)),
                   size128=media.get('size_128mp3', song.get('size128', 0)),
                   interval=current.get('interval') or song['interval'])
    return updated


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--playlist', type=Path, required=True)
    args = parser.parse_args()
    song = json.loads(args.playlist.read_text(encoding='utf-8'))['cdlist'][0]['songlist'][0]
    total = 0
    for session in session_candidates(args.pid):
        total += 1
        try:
            info = request_song(session, song)
            print(json.dumps(dict(candidate=total, code=info['code'], request_code=info['request_code'], result=info['result'], audio_available=bool(info['url']), audio_key_available=bool(info['ekey'])), ensure_ascii=False), flush=True)
            if info['url'] and info['ekey']:
                break
        except Exception as error:
            print(json.dumps(dict(candidate=total, error_type=type(error).__name__)), flush=True)
        if total >= 8:
            break
    if not total:
        print(json.dumps(dict(status='current_qqmusic_session_not_found')))
