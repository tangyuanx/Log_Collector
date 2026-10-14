"""A single-purpose MobaXterm session capture window."""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path
from PySide6.QtCore import QThread, QTimer, Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QMessageBox, QFrame, QDialog, QStyle, QSizeGrip)
from capture import FileCapture
from session import bind_cursor, discover_logs, validate
from update import VERSION, check_update, download_and_install

CONFIG = Path(os.environ.get('APPDATA', str(Path.home()))) / 'LogCollector' / 'config.json'
ICON_PATH = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets' / 'icon.png'


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


class Header(QFrame):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
        else:
            super().mousePressEvent(event)


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
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.resize(640, 410)
        self.setMinimumSize(540, 370)
        settings = self.load_settings()
        self.output_dir = Path(settings.get('output') or Path.home() / 'Documents' / 'LogCollector')
        main = QFrame()
        main.setObjectName('windowFrame')
        main.setAttribute(Qt.WA_StyledBackground)
        self.setCentralWidget(main)
        outer = QVBoxLayout(main)
        # Reserve the border on the central frame itself: child backgrounds
        # previously painted over the stylesheet border on QMainWindow.
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.header = Header()
        self.header.setObjectName('header')
        self.header.setFixedHeight(48)
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(20, 0, 0, 0)
        header_row.setSpacing(9)
        self.brand_icon = QLabel()
        self.brand_icon.setPixmap(self.windowIcon().pixmap(24, 24))
        self.brand_icon.setFixedSize(24, 24)
        self.brand_icon.setAccessibleName('日志采集图标')
        self.brand_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        header_row.addWidget(self.brand_icon)
        title = QLabel('日志采集')
        title.setObjectName('brand')
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        header_row.addWidget(title)
        en = QLabel('Log Collector')
        en.setObjectName('secondary')
        en.setAttribute(Qt.WA_TransparentForMouseEvents)
        header_row.addWidget(en)
        header_row.addStretch()
        self.updatebtn = QPushButton('检查更新')
        self.updatebtn.setObjectName('updateButton')
        self.updatebtn.clicked.connect(self.check_for_updates)
        header_row.addWidget(self.updatebtn)
        self.helpbtn = QPushButton('使用帮助')
        self.helpbtn.setObjectName('textButton')
        self.helpbtn.clicked.connect(self.toggle_help)
        header_row.addWidget(self.helpbtn)
        for icon, title, callback in (
            (QStyle.SP_TitleBarMinButton, '最小化', self.showMinimized),
            (QStyle.SP_TitleBarMaxButton, '最大化 / 还原', self.toggle_maximized),
            (QStyle.SP_TitleBarCloseButton, '关闭', self.close)):
            button = QPushButton()
            button.setObjectName('windowControl')
            button.setIcon(self.style().standardIcon(icon))
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.setFixedSize(42, 47)
            button.clicked.connect(callback)
            header_row.addWidget(button)
        outer.addWidget(self.header)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(24, 24, 24, 18)
        root.setSpacing(18)
        outer.addWidget(body, 1)
        subtitle = QLabel('选择 MobaXterm 会话，采集开始到停止之间的日志。')
        subtitle.setObjectName('secondary')
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        target = QFrame()
        target.setObjectName('target')
        target.setMinimumHeight(94)
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
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setToolTip('日志行数按换行统计：CRLF 只计一行，非空的末行先计一行；末行继续写入不会重复累计。只有已写入会话日志文件的新内容会更新统计。')
        root.addWidget(self.status)
        self.actionbtn = QPushButton('开始采集')
        self.actionbtn.setObjectName('primary')
        self.actionbtn.setMinimumSize(128, 38)
        self.actionbtn.clicked.connect(self.toggle_capture)
        action_row = QHBoxLayout()
        action_row.addStretch()
        action_row.addWidget(self.actionbtn)
        root.addLayout(action_row)
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

        self.retrybtn = QPushButton('重新识别日志')
        self.retrybtn.setObjectName('textButton')
        self.retrybtn.clicked.connect(self.retry_logs)
        self.retrybtn.hide()
        root.addWidget(self.retrybtn, alignment=Qt.AlignLeft)
        self.help_dialog = QDialog(self)
        self.help_dialog.setWindowTitle('使用帮助')
        self.help_dialog.resize(480, 360)
        self.help_dialog.setMinimumSize(480, 360)
        help_layout = QVBoxLayout(self.help_dialog)
        help_layout.setContentsMargins(24, 24, 24, 20)
        help_layout.setSpacing(18)
        help_title = QLabel('采集指定会话的日志')
        help_title.setObjectName('brand')
        help_layout.addWidget(help_title)
        self.help = QLabel('首次使用：在 MobaXterm 的 Settings → Configuration → Terminal 中开启 '
            '“Log terminal output to the following directory”，然后重新连接会话。\n'
            '之后只需拖选会话、开始采集、停止保存，无需在这里选择原始日志文件。\n'
            '自动识别依赖会话日志句柄；若当前版本无法识别，会明确提示，不会猜选其他会话。')
        self.help.setObjectName('help')
        self.help.setWordWrap(True)
        self.help.setMinimumHeight(230)
        help_layout.addWidget(self.help)
        close_help = QPushButton('知道了')
        close_help.clicked.connect(self.help_dialog.accept)
        help_layout.addWidget(close_help, alignment=Qt.AlignRight)
        root.addStretch()
        grip_row = QHBoxLayout()
        grip_row.addStretch()
        grip_row.addWidget(QSizeGrip(self))
        root.addLayout(grip_row)
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.read_new_data)
        self.setStyleSheet("""
            QWidget { background:#ffffff; color:#303030; font-size:15px; }
            QFrame#windowFrame { border:1px solid #919191; background:#ffffff; }
            QFrame#header { border-bottom:1px solid #d8d8d8; }
            QFrame#header QLabel { background:transparent; }
            QLabel#brand { font-size:16px; font-weight:500; }
            QLabel#targetTitle { font-size:16px; font-weight:500; }
            QLabel#secondary { color:#626262; }
            QFrame#target { background:#ffffff; border:1px solid #d8d8d8; border-radius:6px; }
            QFrame#target QLabel { background:transparent; border:0; }
            QPushButton { background:#ffffff; border:1px solid #d8d8d8; border-radius:6px; padding:7px 12px; }
            QPushButton:hover { background:#ededed; }
            QPushButton:focus { border:2px solid #626262; padding:6px 11px; }
            QPushButton:disabled { background:#f5f5f5; color:#818181; border-color:#e9e9e9; }
            QPushButton#primary { background:#353535; color:#ffffff; border:0; font-weight:500; font-size:15px; }
            QPushButton#primary:hover { background:#494949; }
            QPushButton#primary:focus { border:2px solid #626262; }
            QPushButton#primary:disabled { background:#f5f5f5; color:#818181; }
            QPushButton#textButton { border:0; padding:5px 8px; color:#626262; font-size:13px; }
            QPushButton#textButton:hover { color:#303030; background:#ededed; }
            QPushButton#updateButton { font-size:13px; padding:4px 7px; border-radius:4px; color:#626262; }
            QPushButton#windowControl { border:0; border-radius:0; padding:0; }
            QLabel#help { color:#626262; background:#f5f5f5; padding:12px; border-radius:6px; }
        """)
        self.refresh_output_label()
        self.refresh_controls()
        self.fit_content()

    def fit_content(self):
        layout = self.centralWidget().layout()
        layout.invalidate()
        layout.activate()
        height = layout.totalHeightForWidth(self.width())
        height = max(410, height, self.centralWidget().minimumSizeHint().height())
        self.setMinimumSize(540, height)
        if self.height() < height:
            self.resize(self.width(), height)

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

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
        else:
            self.status.setText('准备就绪 · 点击开始后，仅保存新增输出')
        self.fit_content()

    def toggle_help(self):
        self.help_dialog.open()

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
            self.refresh_capture_status()
            self.fit_content()
            self.timer.start()
        except Exception as exc:
            self.capture.abort()
            self.status.setText('无法开始采集：' + str(exc))

    def read_new_data(self):
        try:
            validate(self.session)
            self.capture.poll()
            self.refresh_capture_status()
        except Exception as exc:
            self.stop_capture(str(exc))

    def refresh_capture_status(self):
        seconds = int(time.monotonic() - self.begin_time)
        if self.capture.last_write_at is None:
            activity = '等待会话日志写入 · 尚无新增日志'
        else:
            age = max(0, int(time.monotonic() - self.capture.last_write_clock))
            activity = f'最近写入 {self.capture.last_write_at:%H:%M:%S} · {age} 秒前'
            if self.capture.lines.pending:
                activity += ' · 末行写入中'
        self.status.setText(f'采集中 · {seconds // 60:02}:{seconds % 60:02}\n'
            f'已采集 {self.capture.line_count:,} 行日志 · {self.capture.count:,} 字节\n{activity}')

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
            self.status.setText(f'采集已停止：{reason}\n已保留 {self.capture.line_count:,} 行日志 · '
                f'{self.capture.count:,} 字节\n日志文件：{target}')
        else:
            self.status.setText(f'已保存 {self.capture.line_count:,} 行日志 · {self.capture.count:,} 字节\n'
                + ('本次未收到新增日志\n' if not self.capture.count else '') + target.name)
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
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        set_app_id = ctypes.WinDLL('shell32').SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [wintypes.LPCWSTR]
        set_app_id.restype = ctypes.c_long
        set_app_id('Tangyuan.LogCollector')
    app = QApplication(sys.argv)
    app.setApplicationName('Log Collector')
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    font = QFont()
    font.setFamilies(['Times New Roman', 'Nimbus Roman', 'SimHei', '黑体', 'Heiti SC', 'Noto Sans CJK SC'])
    font.setPointSize(11)
    app.setFont(font)
    window = Window()
    window.show()
    if len(sys.argv) == 3 and sys.argv[1] == '--startup-check':
        report = Path(sys.argv[2])
        def confirm_startup():
            from PySide6.QtCore import qVersion
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({'version': VERSION, 'python': sys.version.split()[0],
                'qt': qVersion(), 'visible': window.isVisible(),
                'help_at_top': window.helpbtn.parentWidget() is window.header,
                'update_at_top': window.updatebtn.parentWidget() is window.header,
                'icon_loaded': not window.windowIcon().isNull(),
                'border_pixel': window.grab().toImage().pixelColor(0, 0).name()}), encoding='utf-8')
            app.quit()
        QTimer.singleShot(300, confirm_startup)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
