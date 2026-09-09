"""Offline regression checks. No QQ Music sessions or remote audio are accessed."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import batch_export
import music_download
import music_gui

SONG = dict(songid=1, songmid='test-mid', strMediaMid='test-media', songname='测试歌曲',
            albumname='测试专辑', singer=[{'name': '测试歌手'}], interval=2, sizeflac=1)


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = music_gui.MusicApp(self.root)

    def tearDown(self):
        self.app.close()

    def settle(self):
        deadline = time.monotonic() + 5
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.root.update()
        self.assertFalse(self.app.busy)

    def load_song(self):
        self.app.query.set('测试')
        second = dict(SONG, songid=2, songname='另一个版本')
        with patch.object(music_gui, 'search_songs', return_value=[SONG, second]):
            self.app.search()
            self.assertEqual(str(self.app.search_button['state']), 'disabled')
            self.settle()
        self.app.tree.selection_set('0')
        self.root.update()

    def detect(self):
        with patch.object(music_gui, 'audio_offers', return_value={'flac': {'uin': '0', 'key': ''},
                                                                 '128': {'uin': '0', 'key': ''}}):
            self.app.detect()
            self.settle()

    def test_flow_path_selection_and_song_invalidation(self):
        self.load_song()
        self.detect()
        self.assertEqual(str(self.app.download_button['state']), 'normal')
        self.app.quality_box.current(1)
        self.app.quality_changed()
        self.assertNotIn('flac', self.app.format_keys)
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / '含空格 新目录'
            with patch.object(music_gui.filedialog, 'askdirectory', return_value=str(destination)):
                self.app.browse()
            with patch.object(music_gui, 'download_selected', return_value=destination / 'song.mp3') as download:
                self.app.download()
                self.settle()
                self.assertEqual(download.call_args.args[1], '128')
                self.assertEqual(download.call_args.args[4], destination)
                self.assertTrue(destination.is_dir())
                self.assertEqual(float(self.app.progress['value']), 100)
                self.assertEqual(str(self.app.progress['mode']), 'determinate')
        self.app.tree.selection_set('1')
        self.root.update()
        self.assertFalse(self.app.offers)
        self.assertEqual(str(self.app.download_button['state']), 'disabled')

    def test_multiselect_queue_dedup_and_search_preserves_queue(self):
        self.load_song()
        self.app.select_all()
        self.root.update()
        self.assertEqual(len(self.app.selected_songs()), 2)
        self.app.add_queue()
        self.app.add_queue()
        self.assertEqual(len(self.app.queue_songs), 2)
        self.app.show_songs([dict(SONG, songid=3)])
        self.assertEqual(len(self.app.queue_songs), 2)
        self.app.queue_tree.selection_set('1')
        self.app.remove_queue()
        self.assertEqual([s['songid'] for s in self.app.queue_songs], [2])

    def test_batch_terminal_progress_and_retry_excludes_completed(self):
        self.load_song()
        self.app.select_all()
        self.root.update()
        self.app.add_queue()
        with tempfile.TemporaryDirectory() as directory:
            self.app.output.set(directory)
            def fake_run(songs, policy, fmt, folder, cancel, notify, **kwargs):
                notify('queue_item', dict(song_id=1, status='已完成'))
                notify('queue_item', dict(song_id=2, status='失败'))
                return dict(total=2, processed=2, success=1, failed=1, skipped=0, cancelled=False, report='report.json')
            with patch.object(music_gui, 'run_queue', side_effect=fake_run):
                self.app.download_batch()
                self.settle()
            self.assertEqual(float(self.app.progress['value']), 100)
            self.assertIn('失败 1', self.app.progress_text.get())
            with patch.object(music_gui, 'run_queue', return_value=dict(total=1, processed=0, success=0, failed=0, skipped=0, cancelled=True, report='report.json')) as worker:
                self.app.download_batch()
                self.settle()
            self.assertEqual([s['songid'] for s in worker.call_args.args[0]], [2])
            self.assertEqual(float(self.app.progress['value']), 0)
            self.assertIn('已停止', self.app.progress_text.get())

    def test_playlist_selects_all_and_uses_named_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            self.app.output.set(directory)
            self.app.playlist_url.set('123456')
            value = dict(id='123456', name='测试歌单', songs=[SONG], total=1, partial=False)
            with patch.object(music_gui, 'fetch_playlist', return_value=value):
                self.app.import_playlist()
                self.settle()
            self.assertEqual(len(self.app.selected_songs()), 1)
            self.assertEqual(Path(self.app.output.get()), Path(directory) / '测试歌单_123456')
            self.app.clear_queue()
            value = dict(value, id='123', name='第二个歌单')
            with patch.object(music_gui, 'fetch_playlist', return_value=value):
                self.app.import_playlist()
                self.settle()
            self.assertEqual(Path(self.app.output.get()), Path(directory) / '第二个歌单_123')

    def test_account_access_opt_in_invalidates_offers(self):
        self.assertFalse(self.app.use_account.get())
        self.load_song()
        self.detect()
        self.assertTrue(self.app.offers)
        self.app.use_account.set(True)
        self.app.account_changed()
        self.assertFalse(self.app.offers)
        self.assertEqual(str(self.app.download_button['state']), 'disabled')

    def test_empty_and_failed_search_recovers_without_logging_secrets(self):
        self.app.query.set('测试')
        with patch.object(music_gui, 'search_songs', return_value=[]):
            self.app.search()
            self.settle()
        self.assertFalse(self.app.tree.get_children())
        with patch.object(music_gui, 'search_songs', side_effect=OSError('signed-url-secret')):
            self.app.search()
            self.settle()
        self.assertEqual(str(self.app.search_button['state']), 'normal')
        self.assertNotIn('signed-url-secret', self.app.log.get('1.0', 'end'))

    def test_no_offers_and_invalid_path_block_download(self):
        self.load_song()
        with patch.object(music_gui, 'audio_offers', return_value={}):
            self.app.detect()
            self.settle()
        self.assertEqual(str(self.app.download_button['state']), 'disabled')
        self.detect()
        self.app.output.set('')
        with patch.object(music_gui, 'download_selected') as download:
            self.app.download()
            download.assert_not_called()


class BackendTests(unittest.TestCase):
    def test_offer_failure_keeps_other_qualities(self):
        with patch.object(music_download, 'client_pid', return_value=None), \
             patch.object(music_download, 'request_song', side_effect=[OSError('secret'), {'url': 'available'}, {'url': ''}]), \
             patch.object(music_download.time, 'sleep'):
            messages = []
            offers = music_download.audio_offers(SONG, progress=messages.append)
        self.assertEqual(list(offers), ['320'])
        self.assertNotIn('secret', ''.join(messages))

    def test_real_audio_export_conversion_shared_log_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            flac, mp3 = root / 'source.flac', root / 'source.mp3'
            batch_export.run_ffmpeg(['-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:a', 'flac', str(flac)])
            batch_export.run_ffmpeg(['-i', str(flac), '-c:a', 'libmp3lame', '-b:a', '128k', str(mp3)])
            offer = dict(url='offline-test', ekey='', request_code=0, result=0)
            output = root / '输出 空格'
            cases = [('128', 'original', mp3), ('128', 'original', mp3),
                     ('128', 'wav', mp3), ('flac', 'original', flac), ('flac', 'mp3', flac)]
            paths = []
            for quality, fmt, source in cases:
                with patch.object(batch_export, 'get_info', return_value=offer), \
                     patch.object(batch_export, 'download', side_effect=lambda url, target: shutil.copyfile(source, target)):
                    result = music_download.download_selected(SONG, quality, {'uin': '0', 'key': ''}, fmt, output)
                self.assertTrue(result.is_file())
                self.assertFalse(result.with_suffix(result.suffix + '.下载记录.json').exists())
                self.assertTrue(batch_export.pcm_decode(result, 16, strict=True))
                log = (output / '下载.log').read_text(encoding='utf-8')
                self.assertIn(result.name, log)
                self.assertIn('校验=通过', log)
                paths.append(result)
            self.assertEqual(len(set(paths)), len(cases))
            self.assertFalse(list(output.glob('*.part*')))
            self.assertFalse(list(output.glob('*.json')))
            self.assertEqual(len(list(output.glob('*.log'))), 1)
            self.assertEqual(log.count('[完成]'), len(cases))


if __name__ == '__main__':
    unittest.main()
