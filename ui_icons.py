"""Reuse the line glyphs from tangyuanx/cmd_send, rendered at native pixel density."""
import sys
from pathlib import Path
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets' / 'ui'


def icon(name, color='#626262', size=16):
    data = (ROOT / (name + '.svg')).read_bytes().replace(b'#626262', color.encode('ascii'))
    renderer = QSvgRenderer(QByteArray(data))
    result = QIcon()
    for scale in (1, 2, 3):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        result.addPixmap(pixmap)
    return result
