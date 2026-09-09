"""Read public playlist metadata from QQ Music; never substitute recordings."""
import html
import json
import re
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


class PlaylistError(ValueError):
    """Only fixed, user-safe messages may be passed to this exception."""


def playlist_id(value):
    value = value.strip()
    if re.fullmatch(r'[1-9][0-9]{0,19}', value):
        return value
    parsed = urlparse(value)
    if parsed.scheme not in ('https', 'http') or parsed.hostname not in ('y.qq.com', 'c.y.qq.com', 'c6.y.qq.com'):
        raise PlaylistError('请输入 QQ 音乐歌单完整链接或数字歌单 ID。')
    path = re.search(r'/playlist/([1-9][0-9]*)', parsed.path)
    query = parse_qs(parsed.query)
    candidate = path[1] if path else next((query[k][0] for k in ('disstid', 'id', 'tid') if query.get(k)), '')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', candidate):
        raise PlaylistError('链接中没有歌单 ID；请复制歌单网页完整链接（含 playlist/数字），或直接输入 ID。')
    return candidate


def normalize_song(raw):
    media = raw.get('file') or {}
    album = raw.get('album') or {}
    return dict(songid=int(raw.get('songid') or raw.get('id') or 0),
                songmid=raw.get('songmid') or raw.get('mid') or '',
                strMediaMid=raw.get('strMediaMid') or media.get('media_mid') or '',
                songname=html.unescape(raw.get('songname') or raw.get('title') or raw.get('name') or '未命名歌曲'),
                singer=raw.get('singer') or [], albumname=html.unescape(raw.get('albumname') or album.get('name') or ''),
                interval=int(raw.get('interval') or 0),
                sizeflac=raw.get('sizeflac') or media.get('size_flac') or 0,
                size320=raw.get('size320') or media.get('size_320mp3') or 0,
                size128=raw.get('size128') or media.get('size_128mp3') or 0)


def fetch_playlist(value):
    pid = playlist_id(value)
    query = urlencode(dict(type=1, json=1, utf8=1, onlysong=0, disstid=pid, format='json'))
    request = Request('https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg?' + query,
                      headers={'Referer': 'https://y.qq.com/', 'User-Agent': 'QQMusic/21'})
    with urlopen(request, timeout=30) as response:
        data = json.load(response)
    lists = data.get('cdlist') or []
    if data.get('code', 0) != 0 or not lists:
        raise PlaylistError('未能读取歌单。请检查歌单 ID、公开状态和网络连接。')
    source = lists[0]
    songs, seen = [], set()
    invalid = duplicates = 0
    for raw in source.get('songlist') or []:
        try:
            song = normalize_song(raw)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if song['songid'] <= 0 or not song['songmid'] or not song['strMediaMid'] or not song['interval']:
            invalid += 1
            continue
        if song['songid'] in seen:
            duplicates += 1
            continue
        seen.add(song['songid'])
        songs.append(song)
    total = int(source.get('total_song_num') or source.get('songnum') or len(source.get('songlist') or []))
    return dict(id=pid, name=html.unescape(source.get('dissname') or '未命名歌单'), songs=songs,
                total=total, invalid=invalid, duplicates=duplicates,
                partial=total > len(source.get('songlist') or []) or bool(invalid))
