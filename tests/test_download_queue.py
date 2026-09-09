import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import download_queue as dq
import qq_playlist as qp
from download_log import append_log

SONG = dict(songid=1, songmid='mid', strMediaMid='media', songname='歌曲', singer=[], albumname='', interval=120)


class PlaylistTests(unittest.TestCase):
    def test_links_and_invalid_input(self):
        for value in ('123456', 'https://y.qq.com/n/ryqq_v2/playlist/123456?ADTAG=x',
                      'https://y.qq.com/n/yqq/playlist/123456.html', 'https://y.qq.com/?disstid=123456'):
            self.assertEqual(qp.playlist_id(value), '123456')
        for value in ('0', '-1', 'https://example.com/playlist/123', 'https://y.qq.com/song/123'):
            with self.assertRaises(qp.PlaylistError):
                qp.playlist_id(value)

    def test_playlist_normalization_order_dedup_and_partial_warning(self):
        modern = dict(id=2, mid='two', title='另一首', file={'media_mid': 'media2'}, interval=150)
        payload = dict(code=0, cdlist=[dict(dissname='歌单', total_song_num=6, songlist=[SONG, modern, SONG, {}])])
        with patch.object(qp, 'urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
            value = qp.fetch_playlist('123')
        self.assertEqual([s['songid'] for s in value['songs']], [1, 2])
        self.assertTrue(value['partial'])
        self.assertEqual(value['duplicates'], 1)
        self.assertEqual(value['invalid'], 1)


class QueueTests(unittest.TestCase):
    def test_success_failure_skip_continue_and_safe_report(self):
        songs = [dict(SONG, songid=i) for i in range(1, 5)]
        events = []
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(dq, 'get_audio_sessions', return_value=[{}]) as sessions, \
             patch.object(dq, 'audio_offers', side_effect=[{'flac': {}}, {}, {'128': {}}, {'128': {}}]), \
             patch.object(dq, 'download_selected', side_effect=[Path(directory)/'one.flac', OSError('signed-secret'), Path(directory)/'four.mp3']):
            result = dq.run_queue(songs, 'best', 'original', directory, threading.Event(), lambda *e: events.append(e))
            self.assertEqual((result['success'], result['failed'], result['skipped'], result['processed']), (2, 1, 1, 4))
            self.assertFalse(result['cancelled'])
            self.assertNotIn('signed-secret', Path(result['report']).read_text(encoding='utf-8'))
            sessions.assert_called_once()
        self.assertEqual([value['processed'] for kind, value in events if kind == 'queue_progress'], [1, 2, 3, 4])

    def test_cancel_preserves_completed_and_does_not_start_next(self):
        cancel = threading.Event()
        def downloaded(*args, **kwargs):
            cancel.set()
            return Path('done.mp3')
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(dq, 'get_audio_sessions', return_value=[{}]), \
             patch.object(dq, 'audio_offers', return_value={'128': {}}), \
             patch.object(dq, 'download_selected', side_effect=downloaded) as download:
            result = dq.run_queue([SONG, dict(SONG, songid=2)], 'best', 'mp3', directory, cancel, lambda *e: None)
            self.assertEqual(result['processed'], 1)
            self.assertTrue(result['cancelled'])
            self.assertEqual(result['records'][1]['status'], 'cancelled')
            download.assert_called_once()

    def test_cancel_during_detection_does_not_download(self):
        cancel = threading.Event()
        def offers(*args, **kwargs):
            cancel.set()
            return {'128': {}}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(dq, 'get_audio_sessions', return_value=[{}]), \
             patch.object(dq, 'audio_offers', side_effect=offers), \
             patch.object(dq, 'download_selected') as download:
            result = dq.run_queue([SONG], 'best', 'original', directory, cancel, lambda *e: None)
            self.assertEqual(result['processed'], 0)
            download.assert_not_called()

    def test_policies_do_not_fake_lossless(self):
        self.assertEqual(dq.pick_quality({'128': {}, 'flac': {}}, 'best'), 'flac')
        self.assertEqual(dq.pick_quality({'128': {}}, 'best'), '128')
        self.assertIsNone(dq.pick_quality({'128': {}}, 'flac'))

    def test_repeated_batches_append_one_log_and_no_json(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(dq, 'get_audio_sessions', return_value=[{}]), \
             patch.object(dq, 'audio_offers', return_value={}):
            first = dq.run_queue([SONG], 'best', 'original', directory, threading.Event(), lambda *e: None)
            contents = Path(first['report']).read_text(encoding='utf-8')
            second = dq.run_queue([SONG], 'best', 'original', directory, threading.Event(), lambda *e: None)
            self.assertEqual(first['report'], second['report'])
            final = Path(second['report']).read_text(encoding='utf-8')
            self.assertTrue(final.startswith(contents))
            self.assertEqual(final.count('[批量结束]'), 2)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ['下载.log'])

    def test_log_failure_does_not_fail_verified_audio(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(dq, 'get_audio_sessions', return_value=[{}]), \
             patch.object(dq, 'audio_offers', return_value={'128': {}}), \
             patch.object(dq, 'download_selected', return_value=Path(directory)/'done.mp3'), \
             patch.object(dq, 'append_log', return_value=False):
            result = dq.run_queue([SONG], 'best', 'original', directory, threading.Event(), lambda *e: None)
            self.assertEqual(result['success'], 1)
            self.assertTrue(result['log_error'])

    def test_multiline_metadata_cannot_forge_log_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(append_log(directory, '完成', 歌曲='name\n[伪造完成]'))
            lines = (Path(directory)/'下载.log').read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn(r'\n', lines[0])


if __name__ == '__main__':
    unittest.main()
