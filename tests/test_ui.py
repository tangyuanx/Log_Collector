import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from app import Window
from session import Session


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.config_patch = patch('app.CONFIG', self.folder / 'config.json')
        self.config_patch.start()
        self.window = Window()
        self.window.output_dir = self.folder / 'out'
        self.window.session = Session(100, 10, 1.0, 'CMoTTY', 200, 20, 0.5, 'test')

    def tearDown(self):
        self.wait_job()
        self.window.timer.stop()
        self.window.capture.abort()
        self.window.close()
        self.config_patch.stop()
        self.temp.cleanup()

    def wait_job(self):
        end = time.monotonic() + 5
        while self.window.worker is not None and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.window.worker, 'Background operation did not finish')

    def test_no_capture_until_log_is_resolved(self):
        self.window.logs_ready(None, '未开启日志')
        self.assertFalse(self.window.actionbtn.isEnabled())
        self.assertFalse(self.window.capture.active)

    def test_start_stop_saves_only_new_output_and_unlocks_binding(self):
        source = self.folder / 'session.log'
        source.write_bytes(b'old\r\n')
        with patch('app.validate'), patch('app.discover_logs', return_value=source):
            self.window.logs_ready(source, '')
            self.window.refresh_controls()
            self.assertTrue(self.window.actionbtn.isEnabled())
            self.window.toggle_capture()
            self.wait_job()
            self.assertTrue(self.window.capture.active)
            self.assertFalse(self.window.pickbtn.isEnabled())
            with source.open('ab') as stream:
                stream.write(b'new\r\n')
            self.window.stop_capture()
            self.assertEqual(self.window.last_file.read_bytes(), b'new\r\n')
            self.assertTrue(self.window.pickbtn.isEnabled())
            self.assertEqual(self.window.actionbtn.text(), '开始采集')

    def test_waiting_and_live_line_count_are_visible(self):
        source = self.folder / 'session.log'
        source.write_bytes(b'old\n')
        with patch('app.validate'):
            self.window.begin_capture(source, '')
            self.assertIn('0 行日志', self.window.status.text())
            self.assertIn('尚无新增日志', self.window.status.text())
            with source.open('ab') as stream: stream.write(b'first\r\nsecond')
            self.window.read_new_data()
            self.assertIn('2 行日志', self.window.status.text())
            self.assertIn('最近写入', self.window.status.text())
            self.assertIn('末行写入中', self.window.status.text())
            with source.open('ab') as stream: stream.write(b'\r\n')
            self.window.read_new_data()
            self.assertIn('2 行日志', self.window.status.text())
            self.assertNotIn('末行写入中', self.window.status.text())
            self.window.stop_capture()
            self.assertIn('已保存 2 行日志', self.window.status.text())

    def test_closed_target_stops_and_preserves_saved_data(self):
        source = self.folder / 'session.log'
        source.write_bytes(b'old')
        with patch('app.validate'):
            self.window.begin_capture(source, '')
        with source.open('ab') as stream:
            stream.write(b'new')
        with patch('app.validate', side_effect=RuntimeError('目标会话已关闭')):
            self.window.read_new_data()
        self.assertFalse(self.window.capture.active)
        self.assertEqual(self.window.last_file.read_bytes(), b'new')
        self.assertFalse(self.window.actionbtn.isEnabled())
        self.assertIn('已关闭', self.window.status.text())

    def test_update_worker_is_retained_until_finished(self):
        self.window.run_job(lambda: None, self.window.update_checked)
        self.assertIsNotNone(self.window.worker)
        self.assertFalse(self.window.pickbtn.isEnabled())
        self.wait_job()
        self.assertIn('最新版本', self.window.update_dialog.message.text())

    def test_version_dialog_check_new_release_and_failure(self):
        before = self.window.status.text()
        info = {'version': 'v9.0.0', 'size': 1048576}
        with patch('app.check_update', return_value=info):
            self.window.check_for_updates()
            self.assertTrue(self.window.update_dialog.isVisible())
            self.assertTrue(self.window.update_dialog.busy)
            self.wait_job()
        dialog = self.window.update_dialog
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.upgrade.isVisible())
        self.assertIn('v9.0.0', dialog.message.text())
        self.assertEqual(before, self.window.status.text())
        self.window.update_checked(None, '网络错误')
        self.assertFalse(dialog.upgrade.isVisible())
        self.assertIn('网络错误', dialog.message.text())
        dialog.reject()

    def test_update_progress_worker_and_retry_on_error(self):
        self.window.update_dialog.info = {'version': 'v9.0.0', 'size': 1048576}
        self.window.update_dialog.open()
        def download(info, progress):
            progress(524288, 1048576, '下载中')
            raise RuntimeError('下载中断')
        with patch('app.download_and_install', side_effect=download):
            self.window.install_update()
            self.window.update_dialog.reject()
            self.assertTrue(self.window.update_dialog.isVisible())
            self.wait_job()
        dialog = self.window.update_dialog
        self.assertEqual(dialog.progress.value(), 50)
        self.assertEqual(dialog.detail.text(), '0.5 / 1.0 MB')
        self.assertIn('下载中断', dialog.message.text())
        self.assertTrue(dialog.upgrade.isEnabled())
        self.assertTrue(self.window.pickbtn.isEnabled())
        dialog.reject()
