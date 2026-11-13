import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
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
        if self.app.platformName() == 'offscreen':
            # The offscreen plugin returns synthetic window IDs, not Win32 HWNDs.
            self.enterContext(patch('app.window_chrome.apply_frame'))
            self.enterContext(patch('app.window_chrome.set_topmost', return_value=True))
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
            self.assertFalse(self.window.session_picker.isEnabled())
            with source.open('ab') as stream:
                stream.write(b'new\r\n')
            self.window.stop_capture()
            self.assertEqual(self.window.last_file.read_bytes(), b'new\r\n')
            self.assertTrue(self.window.session_picker.isEnabled())
            self.assertEqual(self.window.actionbtn.text(), '开始采集')

    def test_waiting_and_live_line_count_are_visible(self):
        source = self.folder / 'session.log'
        source.write_bytes(b'old\n')
        with patch('app.validate'):
            self.window.begin_capture(source, '')
            self.assertEqual(self.window.line_value.text(), '0')
            self.assertIn('尚无新增日志', self.window.status.text())
            with source.open('ab') as stream: stream.write(b'first\r\nsecond')
            self.window.read_new_data()
            self.assertEqual(self.window.line_value.text(), '2')
            self.assertIn('最近写入', self.window.status.text())
            self.assertIn('末行写入中', self.window.status.text())
            with source.open('ab') as stream: stream.write(b'\r\n')
            self.window.read_new_data()
            self.assertEqual(self.window.line_value.text(), '2')
            self.assertNotIn('末行写入中', self.window.status.text())
            self.window.stop_capture()
            self.assertEqual(self.window.line_value.text(), '2')
            self.assertEqual(self.window.capture_state.text(), '采集已保存')

    def test_saved_log_click_and_context_menu_copy_the_requested_value(self):
        source = self.folder / 'session.log'
        source.write_bytes(b'old')
        self.window.session = Session(100, 10, 1.0, 'CMoTTY', 200, 20, 0.5, '串口 & COM3')
        self.window.show()
        with patch('app.validate'):
            self.window.begin_capture(source, '')
        button = self.window.saved_log
        self.assertFalse(button.isVisible())
        self.window.stop_capture()
        self.assertTrue(button.isVisible())
        target = self.window.last_file
        clipboard = self.app.clipboard()
        with patch('app.QToolTip.showText'):
            QTest.mouseClick(button, Qt.LeftButton)
            self.assertEqual(clipboard.text(), target.name)

            pos = button.rect().center()
            QApplication.sendEvent(button, QContextMenuEvent(QContextMenuEvent.Mouse, pos, button.mapToGlobal(pos)))
            menu = button.copy_menu
            self.assertTrue(menu.isVisible())
            path_action, name_action = menu.actions()
            QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(path_action).center())
            self.assertEqual(clipboard.text(), str(target))
            QApplication.sendEvent(button, QContextMenuEvent(QContextMenuEvent.Mouse, pos, button.mapToGlobal(pos)))
            QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(name_action).center())
            self.assertEqual(clipboard.text(), target.name)

    def test_output_path_click_copies_full_path_after_directory_change(self):
        selected = self.folder.joinpath(*(['长路径 & 日志目录'] * 14))
        with patch('app.QFileDialog.getExistingDirectory', return_value=str(selected)):
            self.window.choose_output()
        self.window.show()
        self.assertIn('…', self.window.output_label.text())
        with patch('app.QToolTip.showText'):
            QTest.mouseClick(self.window.output_label, Qt.LeftButton)
        self.assertEqual(self.app.clipboard().text(), str(selected))

    def test_drag_from_left_session_title_binds_and_escape_cancels(self):
        source = self.folder / 'session.log'
        self.window.show()
        self.app.processEvents()
        picker = self.window.session_picker
        title = self.window.target_title
        title_pos = title.mapTo(picker, title.rect().center())
        toolbar_pos = picker.mapTo(self.window.toolbar, title_pos)
        self.assertIs(self.window.toolbar.childAt(toolbar_pos), picker)
        before = self.window.status.text()
        with patch('app.bind_cursor', return_value=self.window.session) as bind, \
                patch('app.discover_logs', return_value=source):
            QTest.mouseClick(picker, Qt.LeftButton, pos=title_pos)
            self.assertEqual(self.window.status.text(), before)
            QTest.mousePress(picker, Qt.LeftButton, pos=title_pos)
            self.assertTrue(picker.picking)
            QTest.keyClick(picker, Qt.Key_Escape)
            QTest.mouseRelease(picker, Qt.LeftButton, pos=QPoint(-20, 20))
            bind.assert_not_called()
            self.assertFalse(picker.picking)
            self.assertEqual(self.window.status.text(), before)

            QTest.mousePress(picker, Qt.LeftButton, pos=title_pos)
            QTest.mouseMove(picker, pos=QPoint(-20, 20))
            QTest.mouseRelease(picker, Qt.LeftButton, pos=QPoint(-20, 20))
            bind.assert_called_once_with()
            self.wait_job()
        self.assertFalse(picker.picking)
        self.assertEqual(self.window.source, source)
        self.assertTrue(self.window.actionbtn.isEnabled())

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
        self.assertEqual(self.window.saved_log.path, self.window.last_file)
        self.assertFalse(self.window.actionbtn.isEnabled())
        self.assertIn('已关闭', self.window.status.text())

    def test_update_worker_is_retained_until_finished(self):
        self.window.run_job(lambda: None, self.window.update_checked)
        self.assertIsNotNone(self.window.worker)
        self.assertFalse(self.window.session_picker.isEnabled())
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
        self.assertTrue(self.window.session_picker.isEnabled())
        dialog.reject()


    def test_pin_changes_persist_without_stopping_capture(self):
        import json
        source = self.folder / 'session.log'
        source.write_bytes(b'old\n')
        with patch('app.validate'), patch('app.window_chrome.set_topmost', return_value=True):
            self.window.begin_capture(source, '')
            self.window.pinbtn.click()
            self.assertTrue(self.window.always_on_top)
            self.assertTrue(self.window.pinbtn.isChecked())
            self.assertEqual(self.window.pinbtn.toolTip(), '取消置顶')
            self.assertTrue(self.window.timer.isActive())
            with source.open('ab') as stream:
                stream.write(b'first\nsecond\n')
            self.window.read_new_data()
            self.assertEqual(self.window.line_value.text(), '2')
            settings = json.loads((self.folder / 'config.json').read_text(encoding='utf-8'))
            self.assertTrue(settings['always_on_top'])
            restored = Window()
            self.assertTrue(restored.always_on_top)
            self.assertTrue(restored.pinbtn.isChecked())
            restored.close()
            self.window.pinbtn.click()
            self.assertFalse(self.window.always_on_top)
            self.assertTrue(self.window.capture.active)
            self.window.stop_capture()
            self.assertEqual(self.window.last_file.read_bytes(), b'first\nsecond\n')
            self.assertFalse(json.loads((self.folder / 'config.json').read_text())['always_on_top'])
