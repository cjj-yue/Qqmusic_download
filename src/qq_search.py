"""Public QQ Music song search, used only to identify an allowed alternate recording."""
import json
import html
import urllib.parse
import urllib.request


def desktop_search(query, count=20):
    body = {
        'comm': {'ct': 24, 'cv': 0, 'uin': 0, 'format': 'json'},
        'req_0': {
            'module': 'music.search.SearchCgiService',
            'method': 'DoSearchForQQMusicDesktop',
            'param': {'query': query, 'page_num': 1, 'num_per_page': count, 'search_type': 0},
        },
    }
    request = urllib.request.Request(
        'https://u.y.qq.com/cgi-bin/musicu.fcg',
        data=json.dumps(body).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'Referer': 'https://y.qq.com/', 'User-Agent': 'QQMusic/21'},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    data = result.get('req_0', {}).get('data', {})
    body = data.get('body', {})
    return (body.get('song', {}) or {}).get('list', [])


def read_public(url):
    request = urllib.request.Request(url, headers={'Referer': 'https://y.qq.com/', 'User-Agent': 'QQMusic/21'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def song_details(song_id):
    data = read_public('https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg?' +
                       urllib.parse.urlencode({'songid': song_id, 'tpl': 'yqq_song_detail', 'format': 'json'}))
    songs = data.get('data') or []
    if not songs or songs[0].get('id') != int(song_id):
        return None
    raw = songs[0]
    media = raw.get('file') or {}
    title = raw.get('title') or raw.get('name') or ''
    subtitle = raw.get('subtitle') or ''
    if subtitle and subtitle.casefold() not in title.casefold():
        title += ' (' + subtitle + ')'
    return dict(songid=raw['id'], songmid=raw['mid'], strMediaMid=media.get('media_mid') or '',
                songname=html.unescape(title), albumname=html.unescape((raw.get('album') or {}).get('name') or ''),
                singer=raw.get('singer') or [], interval=raw.get('interval') or 0,
                sizeflac=media.get('size_flac', 0), size320=media.get('size_320mp3', 0), size128=media.get('size_128mp3', 0))


def search_songs(query, count=20, artist=''):
    """Return verified official details; the suggestion fallback is not exhaustive."""
    queries = list(dict.fromkeys([query + ' ' + artist, query])) if artist else [query]
    identifiers = []
    for term in queries:
        try:
            found = desktop_search(term.strip(), count)
        except (OSError, ValueError):
            found = []
        if not found:
            data = read_public('https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg?' +
                               urllib.parse.urlencode({'format': 'json', 'key': term.strip()}))
            found = ((data.get('data') or {}).get('song') or {}).get('itemlist') or []
        for item in found:
            sid = item.get('id') or item.get('songid')
            if sid and int(sid) not in identifiers:
                identifiers.append(int(sid))
    results = []
    for sid in identifiers[:count]:
        try:
            song = song_details(sid)
        except (OSError, ValueError, KeyError):
            continue
        if song and song['strMediaMid'] and song['interval']:
            results.append(song)
    if artist:
        results.sort(key=lambda song: artist.casefold() not in ' / '.join(s['name'] for s in song['singer']).casefold())
    return results
