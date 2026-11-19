"""A single-purpose MobaXterm session capture window."""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path
from PySide6.QtCore import QThread, QTimer, Qt, Signal, Slot, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QFontMetrics, QIcon, QTextLayout, QTextOption
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QMessageBox, QFrame, QDialog, QStyle, QSizeGrip, QProgressBar,
    QMenu, QSizePolicy, QStyleOptionButton, QToolTip)
import window_chrome
from ui_icons import icon as ui_icon
from appearance import STYLESHEET
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
        # Propagate the same font policy to this separate native window.
        self.setAttribute(Qt.WA_WindowPropagation, True)
        self.setFont(QApplication.font())
        self.setObjectName('updateDialog')
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        title = QLabel('版本与更新')
        title.setObjectName('dialogTitle')
        layout.addWidget(title)
        body = QFrame()
        body.setObjectName('dialogBody')
        content = QVBoxLayout(body)
        content.setContentsMargins(20, 18, 20, 18)
        content.setSpacing(12)
        layout.addWidget(body)
        self.version = QLabel('当前版本  v' + VERSION)
        self.version.setObjectName('brand')
        content.addWidget(self.version)
        self.message = QLabel('正在检查新版本…')
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        self.message.setMinimumHeight(56)
        content.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        content.addWidget(self.progress)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.PlainText)
        self.detail.setObjectName('secondary')
        self.detail.hide()
        content.addWidget(self.detail)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addStretch()
        self.check = QPushButton('重新检查')
        self.check.clicked.connect(parent.check_for_updates)
        row.addWidget(self.check)
        self.upgrade = QPushButton('升级并重启')
        self.upgrade.setObjectName('dialogPrimary')
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
        self.message.setText(stage + '…')
        self.progress.show()
        self.detail.show()
        if total:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(done * 100 / total)))
            self.detail.setText(f'{done / 1048576:.1f} / {total / 1048576:.1f} MB')
        else:
            self.progress.setRange(0, 0)
            self.detail.setText(f'已下载 {done / 1048576:.1f} MB')


class SessionTitle(QLabel):
    """Wrap a session name to three lines, keeping the full name available."""
    def __init__(self, text):
        super().__init__()
        self.full_text = ''
        self.setIndent(0)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        self.setText(text)

    def setText(self, text):
        self.full_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self.refresh_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_text()

    def refresh_text(self):
        width = max(1, self.contentsRect().width() - 4)
        layout = QTextLayout(self.full_text, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        lines = []
        for index in range(3):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            start = line.textStart()
            if index == 2:
                lines.append(self.fontMetrics().elidedText(self.full_text[start:], Qt.ElideRight, width))
            else:
                lines.append(self.full_text[start:start + line.textLength()].rstrip())
        layout.endLayout()
        super().setText('\n'.join(lines))


class SessionPicker(QFrame):
    released_at_target = Signal()
    picking_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.picking = False
        self.setFocusPolicy(Qt.ClickFocus)
        self.setAccessibleName('拖选会话')
        self.setToolTip('拖选会话 · Esc 取消')

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
            self.cancel_pick()
            if not self.rect().contains(event.position().toPoint()):
                # bind_cursor reads the system cursor, including scaled displays.
                self.released_at_target.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.picking and event.key() == Qt.Key_Escape:
            self.cancel_pick()
            event.accept()
        else:
            super().keyPressEvent(event)

    def cancel_pick(self):
        if self.picking:
            self.picking = False
            self.releaseMouse()
            self.picking_changed.emit(False)


class CopyPathButton(QPushButton):
    def __init__(self, show_name=False):
        super().__init__()
        self.path = None
        self.show_name = show_name
        self.setObjectName('copyPath')
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.copy_menu = QMenu(self)
        self.copy_menu.addAction('复制绝对路径', self.copy_path)
        if show_name:
            self.copy_menu.addAction('仅复制文件名', self.copy_name)
        self.clicked.connect(self.copy_name if show_name else self.copy_path)

    def set_path(self, path):
        self.path = path.expanduser().absolute()
        value = self.path.name if self.show_name else str(self.path)
        action = '复制文件名' if self.show_name else '复制保存路径'
        self.setAccessibleName(f'{action}：{value}')
        self.setToolTip(str(self.path))
        self.refresh_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_text()

    def refresh_text(self):
        if self.path is None:
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        bounds = self.style().subElementRect(QStyle.SE_PushButtonContents, option, self)
        text = self.path.name if self.show_name else str(self.path)
        elided = option.fontMetrics.elidedText(text, Qt.ElideMiddle, bounds.width())
        self.setText(elided.replace('&', '&&'))

    def contextMenuEvent(self, event):
        self.copy_menu.popup(event.globalPos())

    def copy_path(self):
        if self.path is not None:
            self.copy_text(str(self.path), '已复制绝对路径')

    def copy_name(self):
        if self.path is not None:
            self.copy_text(self.path.name, '已复制文件名')

    def copy_text(self, text, confirmation):
        QApplication.clipboard().setText(text)
        QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()), confirmation, self, self.rect(), 1200)


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
        self.status_before_pick = ''
        self.begin_time = 0
        self.setWindowTitle('日志采集 · v' + VERSION)
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.resize(800, 460)
        self.setMinimumSize(680, 440)
        self.setStyleSheet(STYLESHEET)
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
        self.header.setFixedHeight(52)
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(20, 0, 0, 0)
        header_row.setSpacing(9)
        self.brand_icon = QLabel()
        self.brand_icon.setPixmap(self.windowIcon().pixmap(20, 20))
        self.brand_icon.setFixedSize(24, 24)
        self.brand_icon.setAccessibleName('日志采集图标')
        self.brand_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        header_row.addWidget(self.brand_icon)
        title = QLabel('Log Collector')
        self.brand_label = title
        title.setObjectName('brand')
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        header_row.addWidget(title)
        header_row.addStretch()
        self.updatebtn = QPushButton('v' + VERSION)
        self.updatebtn.setToolTip('检查更新')
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
        self.pinbtn = QPushButton()
        self.pinbtn.setObjectName('pinButton')
        self.pinbtn.setIcon(ui_icon('pin'))
        self.pinbtn.setIconSize(QSize(17, 17))
        self.pinbtn.setFixedSize(30, 30)
        self.pinbtn.setCheckable(True)
        self.pinbtn.setChecked(self.always_on_top)
        self.pinbtn.clicked.connect(self.set_always_on_top)
        self.refresh_pin_button()
        header_row.addWidget(self.pinbtn)
        header_row.addSpacing(8)
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
            button.setFixedSize(42, 50)
            button.clicked.connect(callback)
            header_row.addWidget(button)
            if name == 'maximize':
                self.maxbtn = button
        outer.addWidget(self.header)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 12, 12)
        body.setSpacing(0)
        outer.addLayout(body, 1)

        self.toolbar = QFrame()
        self.toolbar.setObjectName('toolbar')
        self.toolbar.setFixedWidth(236)
        sidebar = QVBoxLayout(self.toolbar)
        sidebar.setContentsMargins(16, 12, 16, 4)
        sidebar.setSpacing(12)
        self.session_picker = SessionPicker()
        self.session_picker.setObjectName('sessionPicker')
        self.session_picker.released_at_target.connect(self.pick_target)
        self.session_picker.picking_changed.connect(self.picking_changed)
        picker = QVBoxLayout(self.session_picker)
        picker.setContentsMargins(14, 14, 14, 14)
        picker.setSpacing(10)
        picker_heading = QHBoxLayout()
        picker_heading.setSpacing(8)
        target_icon = QLabel()
        target_icon.setPixmap(ui_icon('crosshair').pixmap(17, 17))
        target_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        picker_heading.addWidget(target_icon)
        picker_label = QLabel('目标会话')
        picker_label.setObjectName('secondary')
        picker_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        picker_heading.addWidget(picker_label)
        picker_heading.addStretch()
        picker.addLayout(picker_heading)
        self.target_title = SessionTitle('拖选会话')
        self.target_title.setObjectName('targetTitle')
        self.target_title.setTextFormat(Qt.PlainText)
        self.target_title.setAttribute(Qt.WA_TransparentForMouseEvents)
        picker.addWidget(self.target_title)
        sidebar.addWidget(self.session_picker)
        sidebar.addStretch()
        destination_row = QHBoxLayout()
        destination_row.setSpacing(8)
        folder_icon = QLabel()
        folder_icon.setPixmap(ui_icon('folder').pixmap(15, 15))
        destination_row.addWidget(folder_icon)
        save_label = QLabel('保存到')
        save_label.setObjectName('secondary')
        destination_row.addWidget(save_label)
        destination_row.addStretch()
        self.changebtn = QPushButton('更改')
        self.changebtn.setObjectName('textButton')
        self.changebtn.clicked.connect(self.choose_output)
        destination_row.addWidget(self.changebtn)
        sidebar.addLayout(destination_row)
        self.output_label = CopyPathButton()
        sidebar.addWidget(self.output_label)
        body.addWidget(self.toolbar)

        workspace = QFrame()
        workspace.setObjectName('workspace')
        workspace.setAttribute(Qt.WA_StyledBackground)
        root = QVBoxLayout(workspace)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(0)
        body.addWidget(workspace, 1)

        capture_heading = QHBoxLayout()
        capture_heading.setSpacing(12)
        self.capture_state = QLabel('等待采集')
        self.capture_state.setObjectName('captureState')
        capture_heading.addWidget(self.capture_state)
        capture_heading.addStretch()
        self.actionbtn = QPushButton('开始采集')
        self.actionbtn.setObjectName('primary')
        self.actionbtn.setMinimumHeight(24)
        self.actionbtn.setIcon(ui_icon('play', '#ffffff'))
        self.actionbtn.clicked.connect(self.toggle_capture)
        capture_heading.addWidget(self.actionbtn)
        root.addLayout(capture_heading)
        root.addSpacing(20)

        metrics = QFrame()
        metrics.setObjectName('metricStrip')
        metrics_row = QHBoxLayout(metrics)
        metrics_row.setContentsMargins(0, 16, 0, 16)
        metrics_row.setSpacing(12)
        self.line_value = QLabel('0')
        self.bytes_value = QLabel('0 B')
        self.time_value = QLabel('00:00')
        for index, (value, caption) in enumerate((
            (self.line_value, '日志行数'),
            (self.bytes_value, '日志大小'),
            (self.time_value, '采集时长'),
        )):
            if index:
                divider = QFrame()
                divider.setObjectName('metricDivider')
                divider.setFixedSize(1, 40)
                metrics_row.addWidget(divider)
            metric = QVBoxLayout()
            metric.setSpacing(6)
            value.setObjectName('metricValue')
            value.setIndent(0)
            value.setMinimumWidth(0)
            value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            metric.addWidget(value)
            label = QLabel(caption)
            label.setObjectName('metricCaption')
            metric.addWidget(label)
            metrics_row.addLayout(metric, 1)
        root.addWidget(metrics)
        root.addSpacing(16)

        self.status = QLabel()
        self.status.setObjectName('captureStatus')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(48)
        self.status.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.status.hide()
        root.addWidget(self.status)
        self.retrybtn = QPushButton('重新识别日志')
        self.retrybtn.setObjectName('textButton')
        self.retrybtn.clicked.connect(self.retry_logs)
        self.retrybtn.hide()
        root.addWidget(self.retrybtn, alignment=Qt.AlignLeft)

        self.saved_file = QFrame()
        self.saved_file.setObjectName('savedFile')
        saved_row = QHBoxLayout(self.saved_file)
        saved_row.setContentsMargins(12, 8, 8, 8)
        saved_row.setSpacing(10)
        saved_icon = QLabel()
        saved_icon.setPixmap(ui_icon('file').pixmap(16, 16))
        saved_row.addWidget(saved_icon)
        self.saved_log = CopyPathButton(show_name=True)
        self.saved_log.hide()
        saved_row.addWidget(self.saved_log, 1)
        self.openbtn = QPushButton('打开日志')
        self.openbtn.setObjectName('textButton')
        self.openbtn.clicked.connect(self.open_last_file)
        self.openbtn.hide()
        saved_row.addWidget(self.openbtn)
        self.saved_file.hide()
        root.addWidget(self.saved_file)
        root.addStretch()

        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addStretch()
        grip = QSizeGrip(self)
        grip.setFixedSize(12, 12)
        foot.addWidget(grip, alignment=Qt.AlignBottom)
        root.addLayout(foot)

        self.update_dialog = UpdateDialog(self)
        self.help_dialog = QDialog(self)
        self.help_dialog.setWindowTitle('使用帮助')
        self.help_dialog.setMinimumWidth(500)
        self.help_dialog.setAttribute(Qt.WA_WindowPropagation, True)
        self.help_dialog.setFont(QApplication.font())
        help_layout = QVBoxLayout(self.help_dialog)
        help_layout.setContentsMargins(24, 24, 24, 24)
        help_layout.setSpacing(20)
        help_title = QLabel('采集指定会话的日志')
        help_title.setObjectName('dialogTitle')
        help_layout.addWidget(help_title)
        instructions = QFrame()
        instructions.setObjectName('dialogBody')
        instruction_layout = QVBoxLayout(instructions)
        instruction_layout.setContentsMargins(20, 16, 20, 16)
        instruction_layout.setSpacing(18)
        for title, description in (
            ('开启会话日志', '在 MobaXterm 的 Settings → Configuration → Terminal 中开启 '
             '“Log terminal output to the following directory”，然后重新连接会话。'),
            ('拖选目标会话', '按住主界面左侧的会话区域，拖到目标会话的显示区或输入区后松开。按 Esc 取消。'),
            ('复制日志信息', '单击日志名称复制文件名，右键可复制绝对路径。'),
        ):
            section = QVBoxLayout()
            section.setSpacing(4)
            heading = QLabel(title)
            section.addWidget(heading)
            description_label = QLabel(description)
            description_label.setObjectName('secondary')
            description_label.setWordWrap(True)
            section.addWidget(description_label)
            instruction_layout.addLayout(section)
        help_layout.addWidget(instructions)
        close_help = QPushButton('知道了')
        close_help.clicked.connect(self.help_dialog.accept)
        help_layout.addWidget(close_help, alignment=Qt.AlignRight)
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.read_new_data)
        self.refresh_output_label()
        self.refresh_controls()
        self.fit_content()

    def fit_content(self):
        layout = self.centralWidget().layout()
        layout.invalidate()
        layout.activate()
        picker_layout = self.session_picker.layout()
        picker_layout.invalidate()
        self.session_picker.setMinimumHeight(picker_layout.totalHeightForWidth(self.session_picker.width()))
        layout.invalidate()
        layout.activate()
        height = layout.totalHeightForWidth(self.width())
        sidebar_height = self.toolbar.layout().totalHeightForWidth(self.toolbar.width()) + self.header.height() + 14
        height = max(440, height, sidebar_height, self.centralWidget().minimumSizeHint().height())
        self.setMinimumSize(680, height)
        if self.height() < height:
            self.resize(self.width(), height)
        self.fit_metrics()

    def fit_metrics(self):
        for label in (self.line_value, self.bytes_value, self.time_value):
            font = label.font()
            font.setPixelSize(24)
            text_width = QFontMetrics(font).horizontalAdvance(label.text())
            available = max(1, label.contentsRect().width() - 8)
            size = max(14, min(24, int(24 * available / max(1, text_width))))
            if label.font().pixelSize() != size:
                label.setStyleSheet(f'font-size:{size}px;')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'time_value'):
            self.fit_metrics()

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
        self.output_label.set_path(self.output_dir)

    def refresh_controls(self):
        busy = self.worker is not None
        active = self.capture.active
        can_pick = not active and not busy
        if not can_pick:
            self.session_picker.cancel_pick()
        self.session_picker.setEnabled(can_pick)
        self.session_picker.setCursor(Qt.OpenHandCursor if can_pick else Qt.ArrowCursor)
        self.changebtn.setEnabled(not active and not busy)
        self.retrybtn.setEnabled(not active and not busy)
        self.updatebtn.setEnabled(not active and not busy)
        self.actionbtn.setEnabled(active or (not busy and self.session is not None and self.source is not None))
        self.actionbtn.setText('停止并保存' if active else '开始采集')
        self.actionbtn.setIcon(ui_icon('stop' if active else 'play', '#ffffff' if self.actionbtn.isEnabled() else '#818181'))
        self.fit_content()

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

    @Slot()
    def job_finished(self):
        worker = self.worker
        # The finished signal can arrive before the native thread finishes cleanup.
        worker.wait()
        result, error = worker.result
        callback = self.job_callback
        self.worker = self.job_callback = None
        # Let the last Python reference release the wrapper on the GUI thread.
        worker.setParent(None)
        callback(result, error)
        self.refresh_controls()

    def picking_changed(self, picking):
        self.pinbtn.setEnabled(not picking)
        if picking:
            self.status_before_pick = self.status.text()
            self.set_status('松开绑定 · Esc 取消')
        else:
            self.set_status(self.status_before_pick)

    def pick_target(self):
        # Capture the HWND before a worker runs or the user moves the cursor.
        self.set_status('正在识别会话…')
        try:
            session = bind_cursor()
        except Exception as exc:
            self.set_status(str(exc))
            return
        self.session, self.source = session, None
        self.target_title.setText(session.title)
        self.run_job(lambda: discover_logs(session), self.logs_ready)

    def retry_logs(self):
        if self.session is None:
            return
        self.set_status('正在识别日志…')
        session = self.session
        self.source = None
        self.run_job(lambda: discover_logs(session), self.logs_ready)

    def logs_ready(self, source, error):
        self.source = source if not error else None
        self.retrybtn.setVisible(bool(error))
        if error:
            self.capture_state.setText('等待会话日志')
            self.set_status(error)
        else:
            self.capture_state.setText('准备就绪')
            self.set_status('')
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
            self.set_status('正在确认会话…')
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
            self.saved_log.hide()
            self.saved_file.hide()
            self.retrybtn.hide()
            self.refresh_capture_status()
            self.refresh_controls()
            self.fit_content()
            self.timer.start()
        except Exception as exc:
            self.capture.abort()
            self.capture_state.setText('采集未开始')
            self.set_status('无法开始采集：' + str(exc))

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
            activity = '等待日志写入'
        else:
            activity = f'最近写入 {self.capture.last_write_at:%H:%M:%S}'
            if self.capture.lines.pending:
                activity += ' · 末行写入中'
        self.capture_state.setText('采集中')
        self.refresh_metrics(seconds)
        self.set_status(activity)

    def set_status(self, text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def refresh_metrics(self, seconds=None):
        self.line_value.setText(f'{self.capture.line_count:,}')
        size = self.capture.count
        size_text = f'{size / 1048576:.1f} MB' if size >= 1048576 else f'{size / 1024:.1f} KB' if size >= 1024 else f'{size:,} B'
        self.bytes_value.setText(size_text)
        self.bytes_value.setToolTip(f'{size:,} 字节')
        if seconds is None:
            seconds = max(0, int(time.monotonic() - self.begin_time))
        self.time_value.setText(f'{seconds // 60:02}:{seconds % 60:02}')
        self.fit_metrics()


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
        self.saved_file.setVisible(target is not None)
        self.openbtn.setVisible(target is not None)
        if target is not None:
            self.saved_log.set_path(target)
        self.saved_log.setVisible(target is not None)
        if reason:
            self.source = None
            self.retrybtn.show()
            self.set_status(reason)
        else:
            self.set_status('')
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
            dialog.message.setText('检查失败：' + error)
        elif info is None:
            dialog.message.setText('当前已是最新版本。')
        else:
            dialog.message.setText(f"发现新版本 {info['version']}")

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
            dialog.message.setText('升级失败：' + error)
        else:
            dialog.message.setText('校验完成，正在重启…')
            QApplication.quit()

    def closeEvent(self, event):
        if self.worker is not None:
            self.set_status('操作完成后可关闭')
            event.ignore()
            return
        if self.capture.active:
            reply = QMessageBox.question(self, '正在采集', '退出将停止采集并保存日志，确定退出吗？')
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.stop_capture()
        self.session_picker.cancel_pick()
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
            text_report = inspect_text(window.brand_label, window.target_title, window.capture_state, window.line_value, window.bytes_value)
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


