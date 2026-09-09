import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import music_download
import qq_api
import runtime_tools

SONG = dict(songmid='test', strMediaMid='media')


class ReleaseSafetyTests(unittest.TestCase):
    def test_default_access_never_discovers_or_scans_client(self):
        with patch.object(music_download, 'client_pid') as pid, patch.object(music_download, 'session_candidates') as scan:
            sessions = music_download.get_audio_sessions(progress=lambda _: None)
        self.assertEqual(sessions, [{'uin': '0', 'key': ''}])
        pid.assert_not_called()
        scan.assert_not_called()

    def test_anonymous_request_has_no_cookie_or_login_flag(self):
        with patch.object(qq_api.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"code":0}')) as send:
            qq_api.request_song({'uin': '0', 'key': ''}, SONG)
        request = send.call_args.args[0]
        self.assertFalse(request.has_header('Cookie'))
        body = json.loads(request.data)
        self.assertEqual(body['req_1']['param']['loginflag'], 0)

    def test_credentials_are_not_followed_on_redirect(self):
        request = urllib.request.Request('https://u.y.qq.com/cgi-bin/musicu.fcg')
        with self.assertRaises(urllib.error.HTTPError):
            qq_api.NoCredentialRedirect().redirect_request(request, None, 302, 'Found', {}, 'https://example.org/')

    def test_current_account_config_and_missing_account(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, APPDATA=directory):
            self.assertIsNone(qq_api.active_account_uin())
            self.assertEqual(list(qq_api.session_candidates(0)), [])
            config = Path(directory) / 'Tencent/QQMusic/QQMusicServiceConfig.ini'
            config.parent.mkdir(parents=True)
            config.write_text('[Account]\nUin=12345\n[Other]\nUin=99999\n')
            self.assertEqual(qq_api.active_account_uin(), '12345')
            config.write_text('[Account]\nUin=0\n')
            self.assertIsNone(qq_api.active_account_uin())

    def test_runtime_tools_explicit_path_and_unavailable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'ffmpeg.exe'
            binary.write_bytes(b'test-placeholder')
            with patch.dict(os.environ, QQMUSIC_FFMPEG=str(binary)):
                self.assertEqual(runtime_tools.find_tool('ffmpeg'), str(binary.resolve()))
            with patch.dict(os.environ, QQMUSIC_FFMPEG=str(binary) + '.missing'):
                with self.assertRaisesRegex(RuntimeError, 'configured_ffmpeg_not_found'):
                    runtime_tools.find_tool('ffmpeg')


if __name__ == '__main__':
    unittest.main()
