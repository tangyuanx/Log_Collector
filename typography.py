"""Small CJK UI text: native regular outlines, natural metrics and grayscale AA."""
from PySide6.QtGui import QFont, QImage, QPainter, QTextLayout, QColor


def configure_typography(app):
    font = QFont()
    font.setFamilies(['Times New Roman', 'Nimbus Roman', 'SimHei', '黑体', 'Heiti SC', 'Noto Sans CJK SC'])
    font.setPointSize(11)
    font.setWeight(QFont.Normal)
    # At 100% DPI Qt 6.8 defaults to full hinting / GDI_CLASSIC for <=16px.
    # Vertical hinting uses natural metrics; grayscale avoids RGB color fringes.
    font.setStyleStrategy(QFont.ForceOutline | QFont.PreferAntialias |
                          QFont.NoSubpixelAntialias | QFont.ContextFontMerging)
    font.setHintingPreference(QFont.PreferVerticalHinting)
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
