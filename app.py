"""A single-purpose MobaXterm session capture window."""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path
from PySide6.QtCore import QThread, QTimer, Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QMessageBox, QFrame)
from capture import FileCapture
from session import bind_cursor, discover_logs, validate
from update import VERSION, check_update, download_and_install

CONFIG = Path(os.environ.get('APPDATA', str(Path.home()))) / 'LogCollector' / 'config.json'


class Worker(QThread):
    def __init__(self, function, parent):
        super().__init__(parent)
        self.function = function
        self.result = (None, '')

    def run(self):
        try:
            self.result = (self.function(), '')
        except Exception as exc:
            self.result = (None, str(exc))


class PickButton(QPushButton):
    released_at_target = Signal()
    picking_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__('按住拖到终端', parent)
        self.picking = False
        self.setToolTip('按住左键，拖到 MobaXterm 目标会话的终端文字区域后松开；Esc 取消')

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.picking = True
            self.setFocus()
            self.grabMouse(Qt.CrossCursor)
            self.picking_changed.emit(True)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.picking and event.button() == Qt.LeftButton:
            self.picking = False
            self.releaseMouse()
            self.picking_changed.emit(False)
            # bind_cursor reads the system cursor, including scaled displays.
            self.released_at_target.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.picking and event.key() == Qt.Key_Escape:
            self.picking = False
            self.releaseMouse()
            self.picking_changed.emit(False)
            event.accept()
        else:
            super().keyPressEvent(event)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.capture = FileCapture()
        self.session = None
        self.source = None
        self.worker = None
        self.job_callback = None
        self.last_file = None
        self.begin_time = 0
        self.setWindowTitle('日志采集 · v' + VERSION)
        self.resize(600, 450)
        self.setMinimumSize(540, 370)
        settings = self.load_settings()
        self.output_dir = Path(settings.get('output') or Path.home() / 'Documents' / 'LogCollector')
        main = QWidget()
        self.setCentralWidget(main)
        root = QVBoxLayout(main)
        root.setContentsMargins(26, 24, 26, 22)
        root.setSpacing(18)

        title = QLabel('日志采集')
        title.setObjectName('title')
        root.addWidget(title)
        subtitle = QLabel('选择一个 MobaXterm 会话，记录开始到停止之间的输出。')
        subtitle.setObjectName('secondary')
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        target = QFrame()
        target.setObjectName('target')
        target.setMinimumHeight(100)
        box = QVBoxLayout(target)
        box.setContentsMargins(16, 15, 16, 15)
        box.setSpacing(8)
        target_row = QHBoxLayout()
        self.target_title = QLabel('尚未选择目标会话')
        self.target_title.setObjectName('targetTitle')
        self.target_title.setWordWrap(True)
        target_row.addWidget(self.target_title, 1)
        self.pickbtn = PickButton()
        self.pickbtn.released_at_target.connect(self.pick_target)
        self.pickbtn.picking_changed.connect(self.picking_changed)
        target_row.addWidget(self.pickbtn)
        box.addLayout(target_row)
        self.target_detail = QLabel('按住右侧按钮，拖到终端文字区域后松开。')
        self.target_detail.setObjectName('secondary')
        self.target_detail.setWordWrap(True)
        box.addWidget(self.target_detail)
        root.addWidget(target)

        self.status = QLabel('等待选择目标')
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.actionbtn = QPushButton('开始采集')
        self.actionbtn.setObjectName('primary')
        self.actionbtn.setMinimumHeight(46)
        self.actionbtn.clicked.connect(self.toggle_capture)
        root.addWidget(self.actionbtn)
        self.openbtn = QPushButton('打开刚保存的日志')
        self.openbtn.clicked.connect(self.open_last_file)
        self.openbtn.hide()
        root.addWidget(self.openbtn)

        save_row = QHBoxLayout()
        save_row.addWidget(QLabel('保存到'))
        self.output_label = QLabel()
        self.output_label.setObjectName('secondary')
        save_row.addWidget(self.output_label, 1)
        self.changebtn = QPushButton('更改')
        self.changebtn.clicked.connect(self.choose_output)
        save_row.addWidget(self.changebtn)
        root.addLayout(save_row)

        footer = QHBoxLayout()
        self.helpbtn = QPushButton('首次使用帮助')
        self.helpbtn.setObjectName('textButton')
        self.helpbtn.clicked.connect(self.toggle_help)
        footer.addWidget(self.helpbtn)
        self.retrybtn = QPushButton('重新识别日志')
        self.retrybtn.setObjectName('textButton')
        self.retrybtn.clicked.connect(self.retry_logs)
        self.retrybtn.hide()
        footer.addWidget(self.retrybtn)
        footer.addStretch()
        self.updatebtn = QPushButton('检查更新')
        self.updatebtn.setObjectName('textButton')
        self.updatebtn.clicked.connect(self.check_for_updates)
        footer.addWidget(self.updatebtn)
        root.addLayout(footer)
        self.help = QLabel('首次使用：在 MobaXterm 的 Settings → Configuration → Terminal 中开启 '
            '“Log terminal output to the following directory”，然后重新连接会话。\n'
            '之后只需拖选会话、开始采集、停止保存，无需在这里选择原始日志文件。\n'
            '自动识别依赖会话日志句柄；若当前版本无法识别，会明确提示，不会猜选其他会话。')
        self.help.setObjectName('help')
        self.help.setWordWrap(True)
        self.help.hide()
        root.addWidget(self.help)
        root.addStretch()
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.read_new_data)
        self.setStyleSheet("""
            QWidget { background:#ffffff; color:#202620; font-size:14px; }
            QLabel#title { font-size:24px; font-weight:600; }
            QLabel#targetTitle { font-size:16px; font-weight:600; }
            QLabel#secondary { color:#58615b; }
            QFrame#target { background:#f7f9f7; border:1px solid #d8dfda; border-radius:8px; }
            QFrame#target QLabel { background:transparent; border:0; }
            QPushButton { background:#ffffff; border:1px solid #ccd5ce; border-radius:6px; padding:8px 12px; }
            QPushButton:hover { background:#f0f5f1; border-color:#247b52; }
            QPushButton:focus { border:2px solid #247b52; padding:7px 11px; }
            QPushButton:disabled { background:#f0f2f0; color:#747e77; border-color:#e0e5e1; }
            QPushButton#primary { background:#247b52; color:#ffffff; border:0; font-weight:600; font-size:16px; }
            QPushButton#primary:hover { background:#196440; }
            QPushButton#primary:focus { border:2px solid #153f2b; }
            QPushButton#primary:disabled { background:#e8eee9; color:#56665c; }
            QPushButton#textButton { border:0; padding:4px 0; color:#476151; }
            QPushButton#textButton:hover { color:#196440; background:transparent; }
            QLabel#help { color:#46524a; background:#f7f9f7; padding:12px; border-radius:6px; }
        """)
        self.refresh_output_label()
        self.refresh_controls()
        self.fit_content()

    def fit_content(self):
        layout = self.centralWidget().layout()
        layout.invalidate()
        layout.activate()
        height = layout.totalHeightForWidth(self.width())
        height = max(450, height, self.centralWidget().minimumSizeHint().height())
        self.setMinimumSize(540, height)
        if self.height() < height:
            self.resize(self.width(), height)

    @staticmethod
    def load_settings():
        try:
            value = json.loads(CONFIG.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps({'output': str(self.output_dir)}, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError:
            pass

    def refresh_output_label(self):
        full = str(self.output_dir)
        self.output_label.setToolTip(full)
        self.output_label.setText(self.output_label.fontMetrics().elidedText(full, Qt.ElideMiddle, 330))

    def refresh_controls(self):
        busy = self.worker is not None
        active = self.capture.active
        self.pickbtn.setEnabled(not active and not busy)
        self.changebtn.setEnabled(not active and not busy)
        self.retrybtn.setEnabled(not active and not busy)
        self.updatebtn.setEnabled(not active and not busy)
        self.actionbtn.setEnabled(active or (not busy and self.session is not None and self.source is not None))
        self.actionbtn.setText('停止并保存' if active else '开始采集')

    def run_job(self, function, callback):
        if self.worker is not None:
            return
        self.worker = Worker(function, self)
        self.job_callback = callback
        self.worker.finished.connect(self.job_finished)
        self.refresh_controls()
        self.worker.start()

    def job_finished(self):
        worker = self.worker
        result, error = worker.result
        callback = self.job_callback
        self.worker = self.job_callback = None
        worker.deleteLater()
        callback(result, error)
        self.refresh_controls()

    def picking_changed(self, picking):
        if picking:
            self.status.setText('将指针拖到目标会话的终端文字区域，松开即可绑定；Esc 取消。')
        else:
            self.status.setText('可以重新选择目标' if self.session else '等待选择目标')

    def pick_target(self):
        # Capture the HWND before a worker runs or the user moves the cursor.
        self.status.setText('正在识别目标会话…')
        try:
            session = bind_cursor()
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.session, self.source = session, None
        self.target_title.setText('MobaXterm · ' + session.title)
        self.target_detail.setText(f'已绑定独立会话 · 切换标签页不会改变目标 · 会话 {session.pid}')
        self.run_job(lambda: discover_logs(session), self.logs_ready)

    def retry_logs(self):
        if self.session is None:
            return
        self.status.setText('正在重新识别该会话的日志…')
        session = self.session
        self.source = None
        self.run_job(lambda: discover_logs(session), self.logs_ready)

    def logs_ready(self, source, error):
        self.source = source if not error else None
        self.retrybtn.setVisible(bool(error))
        if error:
            self.status.setText(error)
            self.help.show()
        else:
            self.status.setText('准备就绪 · 点击开始后，仅保存新增输出')
            self.help.hide()
        self.fit_content()

    def toggle_help(self):
        self.help.setVisible(not self.help.isVisible())
        self.fit_content()
        self.adjustSize()

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, '选择日志保存位置', str(self.output_dir))
        if path:
            self.output_dir = Path(path)
            self.refresh_output_label()
            self.save_settings()

    def toggle_capture(self):
        if self.capture.active:
            self.stop_capture()
        elif self.session is not None and self.worker is None:
            self.status.setText('正在确认目标会话…')
            session = self.session
            self.run_job(lambda: discover_logs(session), self.begin_capture)

    def begin_capture(self, source, error):
        if error:
            self.logs_ready(None, error)
            return
        try:
            validate(self.session)
            self.source = source
            self.capture.start(source, self.output_dir, self.session.title)
            # Validate again before reporting success.
            validate(self.session)
            self.begin_time = time.monotonic()
            self.openbtn.hide()
            self.retrybtn.hide()
            self.status.setText('采集中 · 00:00 · 已保存 0 字节')
            self.timer.start()
        except Exception as exc:
            self.capture.abort()
            self.status.setText('无法开始采集：' + str(exc))

    def read_new_data(self):
        try:
            validate(self.session)
            self.capture.poll()
            seconds = int(time.monotonic() - self.begin_time)
            self.status.setText(f'采集中 · {seconds // 60:02}:{seconds % 60:02} · 已保存 {self.capture.count:,} 字节')
        except Exception as exc:
            self.stop_capture(str(exc))

    def stop_capture(self, reason=''):
        self.timer.stop()
        target = self.capture.target
        try:
            self.capture.stop()
        except Exception as exc:
            reason = (reason + '；' if reason else '') + str(exc)
            self.capture.abort()
        self.last_file = target
        self.openbtn.setVisible(target is not None)
        if reason:
            self.source = None
            self.retrybtn.show()
            self.status.setText(f'采集已停止：{reason}\n已写入的日志保留在：{target}')
        else:
            self.status.setText(f'已保存 {self.capture.count:,} 字节 · {target.name}')
        self.refresh_controls()
        self.fit_content()

    def open_last_file(self):
        if self.last_file:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_file)))

    def check_for_updates(self):
        self.status.setText('正在检查更新…')
        self.run_job(check_update, self.update_checked)

    def update_checked(self, info, error):
        if error:
            self.status.setText('检查更新失败：' + error)
        elif info is None:
            self.status.setText('当前已经是最新版本')
        elif QMessageBox.question(self, '发现新版本', f"发现 {info['version']}，立即下载并更新吗？") == QMessageBox.Yes:
            self.status.setText('正在下载更新…')
            self.run_job(lambda: download_and_install(info), self.update_installed)

    def update_installed(self, result, error):
        if error:
            self.status.setText('更新失败：' + error)
        else:
            QApplication.quit()

    def closeEvent(self, event):
        if self.worker is not None:
            self.status.setText('后台操作尚未结束，请稍后关闭。')
            event.ignore()
            return
        if self.capture.active:
            reply = QMessageBox.question(self, '正在采集', '退出将停止采集并保存日志，确定退出吗？')
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.stop_capture()
        if self.pickbtn.picking:
            self.pickbtn.releaseMouse()
        self.save_settings()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Log Collector')
    app.setFont(QFont('Microsoft YaHei UI' if os.name == 'nt' else 'Noto Sans CJK SC', 10))
    window = Window()
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
