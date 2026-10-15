"""A single-purpose MobaXterm session capture window."""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path
from PySide6.QtCore import QThread, QTimer, Qt, Signal, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QMessageBox, QFrame, QDialog, QStyle, QSizeGrip, QProgressBar)
import window_chrome
from ui_icons import icon as ui_icon
from typography import configure_platform_fonts, configure_typography, inspect_text, inspect_update_dialog
from capture import FileCapture
from session import bind_cursor, discover_logs, validate
from update import VERSION, check_update, download_and_install

CONFIG = Path(os.environ.get('APPDATA', str(Path.home()))) / 'LogCollector' / 'config.json'
ICON_PATH = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets' / 'icon.png'


class Worker(QThread):
    progress = Signal(object, object, str)
    def __init__(self, function, parent):
        super().__init__(parent)
        self.function = function
        self.result = (None, '')

    def run(self):
        try:
            self.result = (self.function(), '')
        except Exception as exc:
            self.result = (None, str(exc))


class UpdateDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.busy = False
        self.info = None
        self.setWindowTitle('版本与更新')
        # A separate window needs an explicit text policy, including its buttons.
        # Keep Chinese on a 16px grid instead of the old 15px compact body.
        self.setAttribute(Qt.WA_WindowPropagation, True)
        self.setFont(QApplication.font())
        self.setObjectName('updateDialog')
        self.setStyleSheet('''
            QDialog#updateDialog { background:#ffffff; }
            QDialog#updateDialog QLabel, QDialog#updateDialog QPushButton,
            QDialog#updateDialog QProgressBar { font-size:16px; font-weight:400; }
            QDialog#updateDialog QLabel { color:#303030; padding:2px 0; }
            QDialog#updateDialog QLabel#secondary { color:#505050; }
            QDialog#updateDialog QPushButton { min-height:24px; padding:8px 14px; }
            QDialog#updateDialog QPushButton#primary { font-size:16px; }
        ''')
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self.version = QLabel('当前版本  v' + VERSION)
        self.version.setObjectName('brand')
        layout.addWidget(self.version)
        self.message = QLabel('正在检查新版本…')
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        self.message.setMinimumHeight(56)
        layout.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.PlainText)
        self.detail.setObjectName('secondary')
        self.detail.hide()
        layout.addWidget(self.detail)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addStretch()
        self.check = QPushButton('重新检查')
        self.check.clicked.connect(parent.check_for_updates)
        row.addWidget(self.check)
        self.upgrade = QPushButton('升级并重启')
        self.upgrade.setObjectName('primary')
        self.upgrade.clicked.connect(parent.install_update)
        self.upgrade.hide()
        row.addWidget(self.upgrade)
        self.closebtn = QPushButton('关闭')
        self.closebtn.clicked.connect(self.reject)
        row.addWidget(self.closebtn)
        layout.addLayout(row)

    def set_busy(self, busy):
        self.busy = busy
        self.check.setEnabled(not busy)
        self.upgrade.setEnabled(not busy)
        self.closebtn.setEnabled(not busy)

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)

    def download_progress(self, done, total, stage):
        self.message.setText(stage + '，请稍候…')
        self.progress.show()
        self.detail.show()
        if total:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(done * 100 / total)))
            self.detail.setText(f'{done / 1048576:.1f} / {total / 1048576:.1f} MB')
        else:
            self.progress.setRange(0, 0)
            self.detail.setText(f'已下载 {done / 1048576:.1f} MB')


class PickButton(QPushButton):
    released_at_target = Signal()
    picking_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__('拖选会话', parent)
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
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.window().toggle_maximized()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

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
        self.resize(740, 390)
        self.setMinimumSize(660, 370)
        settings = self.load_settings()
        self.always_on_top = settings.get('always_on_top') is True
        self.chrome_ready = False
        self.output_dir = Path(settings.get('output') or Path.home() / 'Documents' / 'LogCollector')
        main = QFrame()
        main.setObjectName('windowFrame')
        main.setAttribute(Qt.WA_StyledBackground)
        self.setCentralWidget(main)
        outer = QVBoxLayout(main)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.header = Header()
        self.header.setObjectName('header')
        self.header.setFixedHeight(46)
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(19, 0, 0, 0)
        header_row.setSpacing(9)
        self.brand_icon = QLabel()
        self.brand_icon.setPixmap(self.windowIcon().pixmap(23, 23))
        self.brand_icon.setFixedSize(23, 23)
        self.brand_icon.setAccessibleName('日志采集图标')
        self.brand_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        header_row.addWidget(self.brand_icon)
        title = QLabel('日志采集')
        self.brand_label = title
        title.setObjectName('brand')
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        header_row.addWidget(title)
        en = QLabel('Log Collector')
        en.setObjectName('brandEnglish')
        en.setAttribute(Qt.WA_TransparentForMouseEvents)
        header_row.addWidget(en)
        header_row.addStretch()
        self.updatebtn = QPushButton('v' + VERSION)
        self.updatebtn.setToolTip('查看版本并检查更新')
        self.updatebtn.setObjectName('updateButton')
        self.updatebtn.clicked.connect(self.check_for_updates)
        header_row.addWidget(self.updatebtn)
        self.helpbtn = QPushButton()
        self.helpbtn.setIcon(ui_icon('help'))
        self.helpbtn.setObjectName('iconButton')
        self.helpbtn.setFixedSize(30, 30)
        self.helpbtn.setToolTip('使用帮助')
        self.helpbtn.setAccessibleName('使用帮助')
        self.helpbtn.clicked.connect(self.toggle_help)
        header_row.addWidget(self.helpbtn)
        for name, title, callback in (
            ('minimize', '最小化', self.showMinimized),
            ('maximize', '最大化 / 还原', self.toggle_maximized),
            ('close', '关闭', self.close)):
            button = QPushButton()
            button.setObjectName('windowClose' if name == 'close' else 'windowControl')
            button.setIcon(ui_icon(name))
            button.setIconSize(QSize(14, 14))
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.setFixedSize(42, 44)
            button.clicked.connect(callback)
            header_row.addWidget(button)
            if name == 'maximize':
                self.maxbtn = button
        outer.addWidget(self.header)
        title_separator = QFrame()
        title_separator.setObjectName('titleSeparator')
        title_separator.setFixedHeight(1)
        outer.addWidget(title_separator)

        self.toolbar = QFrame()
        self.toolbar.setObjectName('toolbar')
        toolbar_row = QHBoxLayout(self.toolbar)
        toolbar_row.setContentsMargins(20, 10, 15, 10)
        toolbar_row.setSpacing(12)
        target_icon = QLabel()
        target_icon.setPixmap(ui_icon('crosshair').pixmap(17, 17))
        toolbar_row.addWidget(target_icon)
        target_text = QVBoxLayout()
        target_text.setSpacing(3)
        self.target_title = QLabel('尚未选择目标会话')
        self.target_title.setObjectName('targetTitle')
        self.target_title.setTextFormat(Qt.PlainText)
        self.target_title.setWordWrap(True)
        target_text.addWidget(self.target_title)
        self.target_detail = QLabel('按住“拖选会话”，拖到终端文字区域后松开。')
        self.target_detail.setObjectName('secondary')
        self.target_detail.setTextFormat(Qt.PlainText)
        self.target_detail.setWordWrap(True)
        target_text.addWidget(self.target_detail)
        toolbar_row.addLayout(target_text, 1)
        self.pickbtn = PickButton()
        self.pickbtn.setObjectName('targetButton')
        self.pickbtn.released_at_target.connect(self.pick_target)
        self.pickbtn.picking_changed.connect(self.picking_changed)
        toolbar_row.addWidget(self.pickbtn)
        divider = QFrame()
        divider.setObjectName('toolbarDivider')
        divider.setFixedSize(1, 20)
        toolbar_row.addWidget(divider)
        self.pinbtn = QPushButton()
        self.pinbtn.setObjectName('pinButton')
        self.pinbtn.setIcon(ui_icon('pin'))
        self.pinbtn.setIconSize(QSize(17, 17))
        self.pinbtn.setFixedSize(32, 32)
        self.pinbtn.setCheckable(True)
        self.pinbtn.setChecked(self.always_on_top)
        self.pinbtn.clicked.connect(self.set_always_on_top)
        self.refresh_pin_button()
        toolbar_row.addWidget(self.pinbtn)
        outer.addWidget(self.toolbar)

        workspace = QWidget()
        work = QHBoxLayout(workspace)
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(0)
        outer.addWidget(workspace, 1)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(26, 23, 26, 20)
        root.setSpacing(12)
        work.addWidget(content, 1)
        self.capture_state = QLabel('本次采集')
        self.capture_state.setObjectName('secondary')
        root.addWidget(self.capture_state)
        count_row = QHBoxLayout()
        count_row.setSpacing(10)
        self.line_value = QLabel('0')
        self.line_value.setObjectName('lineValue')
        count_row.addWidget(self.line_value, alignment=Qt.AlignBaseline)
        unit = QLabel('行日志')
        unit.setObjectName('secondary')
        count_row.addWidget(unit, alignment=Qt.AlignBaseline)
        count_row.addStretch()
        root.addLayout(count_row)
        metrics = QHBoxLayout()
        metrics.setSpacing(24)
        self.bytes_value = QLabel('大小  0 B')
        self.bytes_value.setObjectName('secondary')
        self.time_value = QLabel('时长  00:00')
        self.time_value.setObjectName('secondary')
        metrics.addWidget(self.bytes_value)
        metrics.addWidget(self.time_value)
        metrics.addStretch()
        root.addLayout(metrics)
        root.addSpacing(4)
        self.status = QLabel('等待选择目标会话')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setObjectName('captureStatus')
        self.status.setWordWrap(True)
        self.line_value.setToolTip('按文本行统计，CRLF 只计一行；非空末行计一行，继续写入不会重复累计。')
        root.addWidget(self.status)
        root.addStretch()
        self.retrybtn = QPushButton('重新识别日志')
        self.retrybtn.setObjectName('textButton')
        self.retrybtn.clicked.connect(self.retry_logs)
        self.retrybtn.hide()
        root.addWidget(self.retrybtn, alignment=Qt.AlignLeft)

        rail = QFrame()
        rail.setObjectName('actionRail')
        rail.setFixedWidth(172)
        actions = QVBoxLayout(rail)
        actions.setContentsMargins(16, 20, 16, 17)
        actions.setSpacing(11)
        self.actionbtn = QPushButton('开始采集')
        self.actionbtn.setObjectName('primary')
        self.actionbtn.setMinimumHeight(42)
        self.actionbtn.setIcon(ui_icon('play', '#ffffff'))
        self.actionbtn.clicked.connect(self.toggle_capture)
        actions.addWidget(self.actionbtn)
        self.openbtn = QPushButton('打开日志')
        self.openbtn.setIcon(ui_icon('file'))
        self.openbtn.clicked.connect(self.open_last_file)
        self.openbtn.hide()
        actions.addWidget(self.openbtn)
        action_hint = QLabel('只保存开始到停止之间的新增日志。')
        action_hint.setObjectName('secondary')
        action_hint.setAlignment(Qt.AlignHCenter)
        action_hint.setWordWrap(True)
        actions.addWidget(action_hint)
        actions.addStretch()
        rail_note = QLabel('切换标签页后，\n仍采集绑定的会话。')
        rail_note.setObjectName('secondary')
        rail_note.setWordWrap(True)
        actions.addWidget(rail_note)
        work.addWidget(rail)

        footer = QFrame()
        footer.setObjectName('footer')
        save_row = QHBoxLayout(footer)
        save_row.setContentsMargins(20, 0, 8, 0)
        save_row.setSpacing(10)
        folder_icon = QLabel()
        folder_icon.setPixmap(ui_icon('folder').pixmap(15, 15))
        save_row.addWidget(folder_icon)
        save_label = QLabel('保存到')
        save_label.setObjectName('secondary')
        save_row.addWidget(save_label)
        self.output_label = QLabel()
        self.output_label.setObjectName('secondary')
        self.output_label.setMinimumWidth(0)
        save_row.addWidget(self.output_label, 1)
        self.changebtn = QPushButton('更改')
        self.changebtn.setObjectName('textButton')
        self.changebtn.clicked.connect(self.choose_output)
        save_row.addWidget(self.changebtn)
        grip = QSizeGrip(self)
        grip.setFixedSize(12, 12)
        save_row.addWidget(grip, alignment=Qt.AlignBottom)
        footer.setMinimumHeight(42)
        outer.addWidget(footer)

        self.update_dialog = UpdateDialog(self)
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
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.read_new_data)
        self.setStyleSheet("""
            QWidget { background:#ffffff; color:#303030; font-size:15px; }
            QFrame#windowFrame { border:1px solid #d8d8d8; background:#ffffff; }
            QFrame#toolbar { border-bottom:1px solid #d8d8d8; }
            QFrame#titleSeparator { background:#d8d8d8; border:0; }
            QFrame#header QLabel { background:transparent; }
            QLabel#brand, QLabel#targetTitle { font-size:16px; font-weight:400; padding:2px 0; }
            QLabel#brandEnglish { font-size:14px; color:#505050; }
            QLabel#secondary { color:#505050; font-size:14px; }
            QLabel#lineValue { font-size:42px; font-weight:400; }
            QLabel#captureStatus { font-size:14px; color:#505050; }
            QFrame#toolbarDivider { background:#d8d8d8; border:0; }
            QFrame#actionRail { border-left:1px solid #d8d8d8; }
            QFrame#footer { border-top:1px solid #d8d8d8; }
            QPushButton { background:#ffffff; border:1px solid #d8d8d8; border-radius:6px; padding:7px 10px; }
            QPushButton:hover { background:#ededed; }
            QPushButton:focus { border:1px solid #818181; }
            QPushButton:disabled { background:#f5f5f5; color:#818181; border-color:#e9e9e9; }
            QPushButton#primary { background:#353535; color:#ffffff; border:0; font-weight:400; font-size:15px; }
            QPushButton#primary:hover { background:#494949; }
            QPushButton#primary:focus { border:1px solid #818181; }
            QPushButton#primary:disabled { background:#f5f5f5; color:#818181; }
            QPushButton#targetButton { font-size:14px; padding:5px 9px; }
            QPushButton#textButton { border:0; padding:5px 8px; color:#505050; font-size:14px; }
            QPushButton#textButton:hover { color:#303030; background:#ededed; }
            QPushButton#updateButton { font-size:14px; padding:3px 6px; border-radius:4px; color:#505050; }
            QPushButton#iconButton, QPushButton#pinButton { border:0; padding:0; border-radius:5px; }
            QPushButton#pinButton:checked { background:#ededed; }
            QPushButton#iconButton:focus, QPushButton#pinButton:focus { border:1px solid #818181; }
            QPushButton#windowControl, QPushButton#windowClose { border:0; border-radius:0; padding:0; }
            QPushButton#windowClose:hover { background:#e5e5e5; }
            QProgressBar { border:1px solid #d8d8d8; border-radius:4px; background:#f5f5f5; text-align:center; min-height:22px; }
            QProgressBar::chunk { background:#a6a6a6; border-radius:3px; }
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
        height = max(370, height, self.centralWidget().minimumSizeHint().height())
        self.setMinimumSize(660, height)
        if self.height() < height:
            self.resize(self.width(), height)

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def refresh_pin_button(self):
        self.pinbtn.setChecked(self.always_on_top)
        self.pinbtn.setToolTip('取消置顶' if self.always_on_top else '置顶显示')
        self.pinbtn.setAccessibleName('取消置顶' if self.always_on_top else '置顶显示')

    def set_always_on_top(self, enabled):
        enabled = bool(enabled)
        previous = self.always_on_top
        try:
            if not window_chrome.set_topmost(int(self.winId()), enabled):
                visible = self.isVisible()
                geometry, state = self.geometry(), self.windowState()
                self.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)
                self.setGeometry(geometry)
                self.setWindowState(state)
                if visible:
                    self.show()
            self.always_on_top = enabled
            self.save_settings()
        except OSError as exc:
            self.always_on_top = previous
            QMessageBox.warning(self, '无法更改置顶状态', str(exc))
        self.refresh_pin_button()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.chrome_ready:
            self.chrome_ready = True
            window_chrome.apply_frame(int(self.winId()))
            self.set_always_on_top(self.always_on_top)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'output_label'):
            self.refresh_output_label()

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, 'maxbtn'):
            self.maxbtn.setIcon(ui_icon('restore' if self.isMaximized() else 'maximize'))

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
            CONFIG.write_text(json.dumps({'output': str(self.output_dir), 'always_on_top': self.always_on_top}, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError:
            pass

    def refresh_output_label(self):
        full = str(self.output_dir)
        self.output_label.setToolTip(full)
        self.output_label.setText(self.output_label.fontMetrics().elidedText(full, Qt.ElideMiddle, max(80, self.width() - 200)))

    def refresh_controls(self):
        busy = self.worker is not None
        active = self.capture.active
        self.pickbtn.setEnabled(not active and not busy)
        self.changebtn.setEnabled(not active and not busy)
        self.retrybtn.setEnabled(not active and not busy)
        self.updatebtn.setEnabled(not active and not busy)
        self.actionbtn.setEnabled(active or (not busy and self.session is not None and self.source is not None))
        self.actionbtn.setText('停止并保存' if active else '开始采集')
        self.actionbtn.setIcon(ui_icon('stop' if active else 'play', '#ffffff' if self.actionbtn.isEnabled() else '#818181'))

    def run_job(self, function, callback, progress_callback=None):
        if self.worker is not None:
            return
        self.worker = Worker(function, self)
        self.job_callback = callback
        if progress_callback is not None:
            self.worker.progress.connect(progress_callback)
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
        self.pinbtn.setEnabled(not picking)
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
            self.refresh_controls()
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
        self.capture_state.setText('采集中')
        self.refresh_metrics(seconds)
        self.status.setText(activity)

    def refresh_metrics(self, seconds=None):
        self.line_value.setText(f'{self.capture.line_count:,}')
        size = self.capture.count
        size_text = f'{size / 1048576:.1f} MB' if size >= 1048576 else f'{size / 1024:.1f} KB' if size >= 1024 else f'{size:,} B'
        self.bytes_value.setText('大小  ' + size_text)
        self.bytes_value.setToolTip(f'{size:,} 字节')
        if seconds is None:
            seconds = max(0, int(time.monotonic() - self.begin_time))
        self.time_value.setText(f'时长  {seconds // 60:02}:{seconds % 60:02}')


    def stop_capture(self, reason=''):
        self.timer.stop()
        target = self.capture.target
        try:
            self.capture.stop()
        except Exception as exc:
            reason = (reason + '；' if reason else '') + str(exc)
            self.capture.abort()
        self.last_file = target
        self.refresh_metrics()
        self.capture_state.setText('采集已停止' if reason else '采集已保存')
        self.openbtn.setVisible(target is not None)
        if reason:
            self.source = None
            self.retrybtn.show()
            self.status.setText(f'采集已停止：{reason}\n已保留 {self.capture.line_count:,} 行日志 · '
                f'{self.capture.count:,} 字节\n日志文件：{target}')
        else:
            self.status.setText('已保存：' + target.name + ('\n本次未收到新增日志' if not self.capture.count else ''))
        self.refresh_controls()
        self.fit_content()

    def open_last_file(self):
        if self.last_file:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_file)))

    def check_for_updates(self):
        dialog = self.update_dialog
        if self.worker is not None or self.capture.active:
            return
        dialog.open()
        dialog.info = None
        dialog.progress.hide()
        dialog.detail.hide()
        dialog.upgrade.hide()
        dialog.message.setText('正在检查新版本…')
        dialog.set_busy(True)
        self.run_job(check_update, self.update_checked)

    def update_checked(self, info, error):
        dialog = self.update_dialog
        dialog.set_busy(False)
        dialog.info = info if not error else None
        dialog.upgrade.setVisible(info is not None and not error)
        if error:
            dialog.message.setText('检查失败，可重新检查：' + error)
        elif info is None:
            dialog.message.setText('当前已是最新版本。')
        else:
            dialog.message.setText(f"发现新版本 {info['version']}。点击升级后将下载、校验并自动重启。")

    def install_update(self):
        dialog = self.update_dialog
        if not dialog.info or self.worker is not None or self.capture.active:
            return
        dialog.set_busy(True)
        dialog.download_progress(0, dialog.info.get('size', 0), '下载中')
        # Resolve the signal on the GUI thread; the worker retains it until finished.
        self.run_job(lambda: download_and_install(dialog.info, self.worker.progress.emit),
                     self.update_installed, dialog.download_progress)

    def update_installed(self, result, error):
        dialog = self.update_dialog
        dialog.set_busy(False)
        if error:
            dialog.message.setText('升级失败，可重试：' + error)
        else:
            dialog.message.setText('校验完成，正在重启…')
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
    configure_platform_fonts()
    app = QApplication(sys.argv)
    app.setApplicationName('Log Collector')
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    if len(sys.argv) == 3 and sys.argv[1] == '--startup-check':
        # CI reproduces the user's legacy SimHei font without redistributing it.
        fixture = os.environ.get('LOGCOLLECTOR_TEST_FONT')
        if fixture:
            from PySide6.QtGui import QFontDatabase
            if QFontDatabase.addApplicationFont(fixture) < 0:
                raise RuntimeError('Could not load the typography test font')
    configure_typography(app)
    window = Window()
    window.show()
    if len(sys.argv) == 3 and sys.argv[1] == '--startup-check':
        report = Path(sys.argv[2])
        def confirm_startup():
            from PySide6.QtCore import qVersion
            original_hwnd = int(window.winId())
            original_geometry = window.geometry()
            original_pin = window.always_on_top
            window.set_always_on_top(True)
            pinned_native = window_chrome.is_topmost(int(window.winId()))
            window.set_always_on_top(False)
            unpinned_native = window_chrome.is_topmost(int(window.winId()))
            pin_preserved_window = original_hwnd == int(window.winId()) and original_geometry == window.geometry()
            window.set_always_on_top(original_pin)
            native_frame = window_chrome.frame_report(int(window.winId()))
            text_report = inspect_text(window.brand_label, window.target_title, window.target_detail, window.status, window.bytes_value)
            update_report = inspect_update_dialog(window)

            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({'version': VERSION, 'python': sys.version.split()[0],
                'qt': qVersion(), 'visible': window.isVisible(),
                'help_at_top': window.helpbtn.parentWidget() is window.header,
                'update_at_top': window.updatebtn.parentWidget() is window.header,
                'icon_loaded': not window.windowIcon().isNull(),
                'version_button': window.updatebtn.text(),
                'typography': text_report,
                'update_dialog': update_report,
                'pin_native_enabled': pinned_native, 'pin_native_disabled': unpinned_native is False,
                'pin_preserved_window': pin_preserved_window, 'native_frame': native_frame,
                'ui_icons_loaded': not window.pinbtn.icon().isNull() and not window.helpbtn.icon().isNull(),
                'runtime_path': str(getattr(sys, '_MEIPASS', '')),
                'launcher_path': os.environ.get('LOGCOLLECTOR_LAUNCHER', ''),
                'border_pixel': window.grab().toImage().pixelColor(0, 0).name()}), encoding='utf-8')
            app.quit()
        QTimer.singleShot(300, confirm_startup)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())

