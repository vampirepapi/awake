"""Lucide icon rendering (bundled SVGs, recoloured at runtime) and brand artwork."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer

from awake.paths import asset_path


@lru_cache(maxsize=None)
def _svg_source(name: str) -> str:
    return asset_path("icons", "lucide", f"{name}.svg").read_text(encoding="utf-8")


def _renderer(name: str, color: QColor, stroke_width: float | None) -> QSvgRenderer:
    svg = _svg_source(name).replace("currentColor", color.name(QColor.NameFormat.HexRgb))
    if stroke_width is not None:
        svg = svg.replace('stroke-width="2"', f'stroke-width="{stroke_width}"')
    return QSvgRenderer(QByteArray(svg.encode("utf-8")))


def _dpr() -> float:
    screen = QGuiApplication.primaryScreen()
    return max(2.0, screen.devicePixelRatio()) if screen else 2.0


def render_icon(painter: QPainter, name: str, rect: QRectF, color: QColor, stroke_width: float | None = None) -> None:
    painter.save()
    painter.setOpacity(painter.opacity() * color.alphaF())
    _renderer(name, color, stroke_width).render(painter, rect)
    painter.restore()


def pixmap(name: str, color: QColor, size: int = 16, stroke_width: float | None = None) -> QPixmap:
    dpr = _dpr()
    image = QImage(int(size * dpr), int(size * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    render_icon(painter, name, QRectF(0, 0, image.width(), image.height()), color, stroke_width)
    painter.end()
    pm = QPixmap.fromImage(image)
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: QColor, size: int = 16, stroke_width: float | None = None) -> QIcon:
    return QIcon(pixmap(name, color, size, stroke_width))


# --------------------------------------------------------------------------- brand


def logo_image(size: int, top: str = "#9D8FFA", bottom: str = "#6F5CE9") -> QImage:
    """App logo: a soft-purple rounded square with a white sun glyph."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    inset = size * 0.0
    rect = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    radius = rect.width() * 0.26
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0.0, QColor(top))
    gradient.setColorAt(1.0, QColor(bottom))
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.fillPath(path, gradient)
    glyph = rect.width() * 0.58
    glyph_rect = QRectF(rect.center().x() - glyph / 2, rect.center().y() - glyph / 2, glyph, glyph)
    render_icon(p, "sun", glyph_rect, QColor("#FFFFFF"), stroke_width=2.2)
    p.end()
    return image


def logo_pixmap(size: int) -> QPixmap:
    dpr = _dpr()
    pm = QPixmap.fromImage(logo_image(int(size * dpr)))
    pm.setDevicePixelRatio(dpr)
    return pm


def app_icon() -> QIcon:
    icon_ = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon_.addPixmap(QPixmap.fromImage(logo_image(size)))
    return icon_


@lru_cache(maxsize=32)
def tray_icon(
    active: bool,
    *,
    mask: bool,
    dark_background: bool = True,
    label: str | None = None,
    infinite: bool = False,
) -> QIcon:
    """Menu-bar / notification-area icon. Sun = awake, moon = normal sleep.

    ``mask=True`` produces a monochrome template image that macOS tints for the
    current menu-bar appearance.

    ``label`` (e.g. ``"1h 12m"``) or ``infinite`` draws the remaining time / an
    infinity glyph to the right of the sun, as one wide image. macOS keeps the
    width of tray images and only fits their height to the menu bar; Windows
    forces a small square, so callers only pass these on macOS.
    """
    name = "sun" if active else "moon"
    if mask:
        color = QColor("#000000")
    elif active:
        color = QColor("#9D8FFA")
    else:
        color = QColor("#D6D6DE" if dark_background else "#4A4A55")
    result = QIcon()
    if active and (label or infinite):
        # Qt picks the tallest pixmap that fits the menu bar (bar height minus
        # 4 pt, at the screen's pixel ratio), so offer every plausible height.
        for height in (16, 18, 20, 22, 32, 36, 40, 44):
            result.addPixmap(QPixmap.fromImage(_labelled_image(color, height, label, infinite)))
    else:
        for size in (16, 18, 22, 32, 36, 44, 64):
            result.addPixmap(QPixmap.fromImage(_glyph_image(name, color, size, filled=active)))
    if mask:
        result.setIsMask(True)
    return result


def _label_font(pixel_size: int) -> QFont:
    font = QFont()  # the platform UI font (San Francisco on macOS)
    font.setPixelSize(pixel_size)
    font.setWeight(QFont.Weight.Medium)
    try:  # tabular digits so the width doesn't jitter as minutes change (Qt >= 6.7)
        font.setFeature(QFont.Tag("tnum"), 1)
    except (AttributeError, TypeError):
        pass
    return font


def _labelled_image(color: QColor, height: int, label: str | None, infinite: bool) -> QImage:
    """Sun glyph followed by a time label or an infinity glyph, ``height`` px tall."""
    gap = round(height * 0.18)
    font = _label_font(round(height * 0.75))
    metrics = QFontMetricsF(font)
    if infinite:
        trailing = height * 1.0
    else:
        trailing = metrics.horizontalAdvance(label or "")
    width = int(height + gap + trailing + 1)

    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.drawImage(0, 0, _glyph_image("sun", color, height, filled=True))
    x = height + gap
    if infinite:
        render_icon(p, "infinity", QRectF(x, (height - trailing) / 2, trailing, trailing), color, stroke_width=2.8)
    else:
        p.setFont(font)
        p.setPen(color)
        p.drawText(QRectF(x, 0, trailing + 1, height),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), label or "")
    p.end()
    return image


def _glyph_image(name: str, color: QColor, size: int, filled: bool) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if filled:
        # Solid core so the active state reads clearly even in monochrome.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        core = size * 0.36
        p.drawEllipse(QRectF(size / 2 - core / 2, size / 2 - core / 2, core, core))
    render_icon(p, name, QRectF(0, 0, size, size), color, stroke_width=2.6)
    p.end()
    return image
