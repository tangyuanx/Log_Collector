"""Small CJK UI text: native regular outlines, natural metrics and grayscale AA."""
import os
from PySide6.QtGui import QFont, QImage, QPainter, QTextLayout, QColor


def configure_platform_fonts():
    # Qt 6.8's DirectWrite fallback still produces LCD color masks for SimHei
    # despite NoSubpixelAntialias. Use Qt's built-in FreeType backend so all
    # script runs use the same outline / grayscale policy as the primary font.
    if os.name == 'nt':
        platform = os.environ.get('QT_QPA_PLATFORM', 'windows')
        if platform.split(':', 1)[0] == 'windows' and 'fontengine=' not in platform:
            os.environ['QT_QPA_PLATFORM'] = platform + (',' if ':' in platform else ':') + 'fontengine=freetype'


def configure_typography(app):
    font = QFont()
    font.setFamilies(['Times New Roman', 'Nimbus Roman', 'Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC', 'SimHei', '黑体', 'Noto Sans CJK SC'])
    font.setPointSize(11)
    font.setWeight(QFont.Normal)
    # FreeType full hinting aligns both axes to the physical pixel grid.
    # Vertical-only hinting left soft side bearings at 1x in v0.1.6.
    font.setStyleStrategy(QFont.ForceOutline | QFont.PreferAntialias |
                          QFont.NoSubpixelAntialias | QFont.ContextFontMerging)
    font.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(font)


def inspect_text(*labels):
    """Inspect actual rendered widgets and glyph fallback, only for build QA."""
    import base64
    from PySide6.QtCore import QBuffer, QIODevice
    results = []
    images = []
    for label in labels:
        grabbed = label.grab().toImage()
        # Header labels have transparent backgrounds; composite before counting
        # pixels so transparent black does not look like clipped glyph ink.
        image = QImage(grabbed.size(), QImage.Format_RGB32)
        image.setDevicePixelRatio(grabbed.devicePixelRatio())
        image.fill(QColor('white'))
        painter = QPainter(image)
        painter.drawImage(0, 0, grabbed)
        painter.end()
        image.setDevicePixelRatio(1)
        images.append(image)
        ink, colored = [], 0
        for y in range(image.height()):
            for x in range(image.width()):
                pixel = image.pixelColor(x, y)
                r, g, b = pixel.red(), pixel.green(), pixel.blue()
                if max(r, g, b) - min(r, g, b) > 2:
                    colored += 1
                if min(r, g, b) < 230:
                    ink.append((x, y))
        layout = QTextLayout(label.text(), label.font())
        layout.beginLayout()
        layout.createLine()
        layout.endLayout()
        runs = layout.glyphRuns()
        glyphs = [g for run in runs for g in run.glyphIndexes()]
        results.append({'text': label.text(), 'weight': int(label.font().weight()),
                        'strategy': int(label.font().styleStrategy().value),
                        'pixel_size': label.font().pixelSize(),
                        'hinting': label.font().hintingPreference().name,
                        'families_used': sorted({run.rawFont().familyName() for run in runs}),
                        'no_missing_glyphs': bool(glyphs) and all(g != 0 for g in glyphs),
                        'colored_pixels': colored, 'ink_pixels': len(ink),
                        'not_clipped': bool(ink) and min(y for x, y in ink) > 0
                                       and max(y for x, y in ink) < image.height() - 1})
    # Compact, synthetic-text-only PNG for visual review in the Windows logs.
    panel = QImage(max(im.width() for im in images) + 20, sum(im.height() for im in images) + 30, QImage.Format_RGB32)
    panel.fill(QColor('white'))
    painter = QPainter(panel)
    y = 10
    for im in images:
        painter.drawImage(10, y, im)
        y += im.height() + 10
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    panel.save(buffer, 'PNG')
    return {'labels': results, 'preview_png': base64.b64encode(bytes(buffer.data())).decode('ascii')}


def inspect_update_dialog(window):
    """Exercise the real popup without network/update side effects in build QA."""
    import base64
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtWidgets import QApplication
    dialog = window.update_dialog
    dialog.open()
    states = []
    for state in ('checking', 'available', 'downloading', 'failure', 'latest'):
        dialog.progress.hide()
        dialog.detail.hide()
        if state == 'checking':
            dialog.message.setText('正在检查新版本…')
            dialog.upgrade.hide()
            dialog.set_busy(True)
        elif state == 'available':
            window.update_checked({'version': 'v9.0.0', 'size': 1048576}, '')
        elif state == 'downloading':
            dialog.set_busy(True)
            dialog.download_progress(524288, 1048576, '下载中')
        elif state == 'failure':
            window.update_checked(None, '网络连接中断，请稍后重试。')
        else:
            window.update_checked(None, '')
        dialog.adjustSize()
        QApplication.processEvents()
        labels = [dialog.version, dialog.message]
        if dialog.detail.isVisible():
            labels.append(dialog.detail)
        typography = inspect_text(*labels)
        typography.pop('preview_png')
        buttons = []
        for button in (dialog.check, dialog.upgrade, dialog.closebtn):
            if not button.isVisible():
                continue
            font = button.font()
            layout = QTextLayout(button.text(), font)
            layout.beginLayout()
            line = layout.createLine()
            layout.endLayout()
            runs = layout.glyphRuns()
            glyphs = [g for run in runs for g in run.glyphIndexes()]
            buttons.append({'text': button.text(), 'pixel_size': font.pixelSize(),
                'weight': int(font.weight()), 'strategy': int(font.styleStrategy().value),
                'hinting': font.hintingPreference().name,
                'families_used': sorted({run.rawFont().familyName() for run in runs}),
                'no_missing_glyphs': bool(glyphs) and all(g != 0 for g in glyphs),
                'fits': line.naturalTextWidth() <= button.width() - 28
                        and line.height() <= button.height() - 16})
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        dialog.grab().save(buffer, 'PNG')
        states.append({'state': state, 'typography': typography, 'buttons': buttons,
            'preview_png': base64.b64encode(bytes(buffer.data())).decode('ascii')})
    dialog.reject()
    return states
