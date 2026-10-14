"""Small, practical Windows desktop UI for per-session MobaXterm log capture."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QTimer, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QFileDialog, QMessageBox, QGroupBox)
from PySide6.QtCore import QUrl
from capture import FileCapture, CaptureError, scan_logs
from update import VERSION, check_update, download_and_install

CONFIG = Path(os.environ.get('APPDATA', str(Path.home()))) / 'LogCollector' / 'config.json'

class UpdateWorker(QThread):
    complete = Signal(object, str)
    def __init__(self, release=None):
        super().__init__()
        self.release = release
    def run(self):
        try:
            info = self.release
            if info is None: info = check_update()
            else: download_and_install(info)
            self.complete.emit(info, '')
        except Exception as e:
            self.complete.emit(None, str(e))

class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.capture = FileCapture()
        self.worker = None
        self.setWindowTitle('Log Collector  ·  v' + VERSION)
        self.resize(730, 445)
        self.settings = self.load_settings()
        main = QWidget()
        self.setCentralWidget(main)
        root = QVBoxLayout(main)
        root.setContentsMargins(25, 23, 25, 20)
        root.setSpacing(15)
        title = QLabel('Log Collector')
        title.setObjectName('title')
        root.addWidget(title)
        root.addWidget(QLabel('按需记录 MobaXterm 会话输出 · 开始前的历史日志不会进入采集文件'))
        root.addSpacing(9)
        self.folder = QLineEdit(self.settings.get('source_folder', ''))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(300)
        self.combo.currentIndexChanged.connect(self.select_entry)
        self.source = QLineEdit(self.settings.get('source', ''))
        self.label = QLineEdit(self.settings.get('label', 'SSH-session'))
        self.output = QLineEdit(self.settings.get('output', str(Path.home() / 'Documents' / 'LogCollector')))
        root.addLayout(self.file_row('原始日志目录', self.folder, '浏览目录', self.choose_folder))
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel('发现日志'))
        toolbar.addWidget(self.combo, 1)
        refresh = QPushButton('刷新列表')
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(refresh)
        root.addLayout(toolbar)
        root.addLayout(self.file_row('绑定日志文件', self.source, '选择文件', self.choose_source))
        root.addLayout(self.file_row('会话名称', self.label))
        root.addLayout(self.file_row('采集保存到', self.output, '选择目录', self.choose_output))
        buttons = QHBoxLayout()
        self.startbtn = QPushButton('●  开始采集')
        self.startbtn.setObjectName('primary')
        self.startbtn.clicked.connect(self.toggle_capture)
        buttons.addWidget(self.startbtn, 1)
        updatebtn = QPushButton('检查更新')
        updatebtn.clicked.connect(self.check_for_updates)
        buttons.addWidget(updatebtn)
        root.addLayout(buttons)
        self.status = QLabel('准备就绪 · 请选择与 MobaXterm 对应会话匹配的原始日志')
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        root.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.read_new_data)
        self.refresh()
        self.setStyleSheet('''
            QWidget { background:#ffffff; color:#232a32; font-family:"Microsoft YaHei UI","Segoe UI"; font-size:13px; }
            QLabel#title { font-size:24px; font-weight:700; color:#151b23; }
            QLineEdit,QComboBox { background:#fafbfc; border:1px solid #d8dde4; border-radius:7px; padding:8px; min-height:19px; }
            QLineEdit:focus,QComboBox:focus { border:1px solid #7297d6; }
            QPushButton { background:#f2f4f7; border:1px solid #dde2e9; border-radius:7px; padding:9px 15px; }
            QPushButton:hover { background:#e8edf5; }
            QPushButton#primary { background:#247b52; color:white; border:0; font-weight:600; }
            QPushButton#primary:hover { background:#196440; }
        ''')
    @staticmethod
    def load_settings():
        try: return json.loads(CONFIG.read_text(encoding='utf-8'))
        except (OSError, ValueError): return {}
    def save_settings(self):
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps({'source_folder':self.folder.text(), 'source':self.source.text(),
                'label':self.label.text(), 'output':self.output.text()}, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError: pass
    def file_row(self, name, field, button=None, callback=None):
        row = QHBoxLayout()
        label = QLabel(name)
        label.setFixedWidth(90)
        row.addWidget(label)
        row.addWidget(field, 1)
        if button:
            b = QPushButton(button)
            b.clicked.connect(callback)
            row.addWidget(b)
        return row
    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择 MobaXterm 原始日志目录', self.folder.text())
        if path: self.folder.setText(path); self.refresh()
    def choose_source(self):
        path, _ = QFileDialog.getOpenFileName(self,'绑定原始日志',self.folder.text(),'日志 (*.log *.txt *.out);;全部文件 (*)')
        if path:
            self.source.setText(path)
            self.label.setText(Path(path).stem)
    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, '选择采集保存目录', self.output.text())
        if path: self.output.setText(path)
    def refresh(self):
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem('手动选择 / 当前绑定', '')
        for path in scan_logs(self.folder.text()):
            self.combo.addItem(f'{path.name}  ·  {path.parent.name}', str(path))
        self.combo.blockSignals(False)
        self.status.setText(f'发现 {self.combo.count()-1} 个候选日志 · 请确认日志与目标会话一一对应')
    def select_entry(self, index):
        if self.capture.active: return
        path = self.combo.itemData(index)
        if path:
            self.source.setText(path)
            self.label.setText(Path(path).stem)
    def toggle_capture(self):
        if self.capture.active:
            try:
                self.timer.stop()
                path = self.capture.stop()
                self.status.setText(f'已停止并保存：{path}')
            except Exception as exc:
                self.capture.abort()
                QMessageBox.warning(self, '采集不完整', f'采集已停止：{exc}')
            self.startbtn.setText('●  开始采集')
            self.save_settings()
            return
        try:
            if Path(self.source.text()).resolve() == Path(self.output.text()).resolve():
                raise CaptureError('原始日志与保存位置不能相同')
            dest = self.capture.start(self.source.text(), self.output.text(), self.label.text())
            self.save_settings()
            self.startbtn.setText('■  停止采集')
            self.status.setText(f'采集中 · 已采集 0 字节 · {dest.name}')
            self.timer.start()
        except Exception as exc: QMessageBox.warning(self,'无法开始采集', str(exc))
    def read_new_data(self):
        try:
            self.capture.poll()
            self.status.setText(f'采集中 · 已采集 {self.capture.count:,} 字节 · {self.capture.target}')
        except Exception as exc:
            self.timer.stop()
            self.capture.abort()
            self.startbtn.setText('●  开始采集')
            QMessageBox.warning(self, '采集异常', f'{exc}\n已经写入的日志文件会保留。')
    def check_for_updates(self):
        if self.worker is not None: return
        self.status.setText('正在检查 GitHub Releases...')
        self.worker = UpdateWorker()
        self.worker.complete.connect(self.update_checked)
        self.worker.start()
    def update_checked(self, info, error):
        self.worker = None
        if error:
            QMessageBox.warning(self,'检查更新失败',error)
            self.status.setText('检查更新失败')
        elif info is None:
            QMessageBox.information(self,'检查更新','已经是最新版本')
            self.status.setText('当前为最新版本')
        elif QMessageBox.question(self,'发现新版本',f"有新版本 {info['version']}，立即下载并更新吗？") == QMessageBox.Yes:
            if self.capture.active:
                QMessageBox.warning(self, '正在采集', '请先停止采集再更新')
                return
            self.worker = UpdateWorker(info)
            self.worker.complete.connect(self.update_installed)
            self.worker.start()
    def update_installed(self, info, error):
        self.worker = None
        if error: QMessageBox.warning(self,'更新失败',error)
        else: QApplication.quit()
    def closeEvent(self, event):
        if self.capture.active:
            reply = QMessageBox.question(self,'正在采集','退出会停止当前采集。确定退出吗？')
            if reply != QMessageBox.Yes: event.ignore(); return
            try: self.capture.stop()
            except Exception: self.capture.abort()
        self.save_settings()
        super().closeEvent(event)

def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Log Collector')
    window = Window()
    window.show()
    return app.exec()

if __name__ == '__main__': raise SystemExit(main())
