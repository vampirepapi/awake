"""Small shared building blocks: cards, labels, icon tiles, dividers, progress bar."""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from awake.ui import icons
from awake.ui.styles.theme import ANIMATION_MS, theme, theme_manager


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def label(text: str = "", role: str | None = None, *, wrap: bool = False, parent: QWidget | None = None) -> QLabel:
    widget = QLabel(text, parent)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, padding: int = 20, spacing: int = 12) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)


class Divider(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("divider", True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class IconLabel(QLabel):
    """A QLabel showing a Lucide icon in a theme colour; re-renders on theme change."""

    def __init__(self, name: str, size: int = 16, color_key: str = "text_secondary",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name, self._size, self._color_key = name, size, color_key
        self.setFixedSize(size, size)
        self.refresh()
        theme_manager().theme_changed.connect(self.refresh)

    def set_icon(self, name: str | None = None, color_key: str | None = None) -> None:
        self._name = name or self._name
        self._color_key = color_key or self._color_key
        self.refresh()

    def refresh(self, *_: object) -> None:
        self.setPixmap(icons.pixmap(self._name, theme().color(self._color_key), self._size))


class IconTile(QFrame):
    """Rounded square holding an icon, used at the start of list rows."""

    def __init__(self, name: str, size: int = 32, icon_size: int = 16, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("iconTile", True)
        self.setFixedSize(size, size)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.icon = IconLabel(name, icon_size)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignCenter)

    def set_active(self, active: bool) -> None:
        if self.property("active") != active:
            self.setProperty("active", active)
            repolish(self)
        self.icon.set_icon(color_key="accent" if active else "text_secondary")


class SectionHeader(QWidget):
    """Card title row: icon + title on the left, optional widget on the right."""

    def __init__(self, title: str, icon_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(8)
        if icon_name:
            self.row.addWidget(IconLabel(icon_name, 16, "text_muted"))
        self.title = label(title, "cardTitle")
        self.row.addWidget(self.title)
        self.row.addStretch(1)

    def add_right(self, widget: QWidget) -> None:
        self.row.addWidget(widget)


class ProgressBar(QWidget):
    """Thin rounded progress bar with an animated value."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._indeterminate = False
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(ANIMATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        theme_manager().theme_changed.connect(self.update)

    def _get_value(self) -> float:
        return self._value

    def _set_value(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        self.update()

    value = Property(float, _get_value, _set_value)

    def set_progress(self, value: float, animate: bool = True) -> None:
        self._indeterminate = False
        if animate and abs(value - self._value) > 0.0005:
            self._anim.stop()
            self._anim.setStartValue(self._value)
            self._anim.setEndValue(value)
            self._anim.start()
        else:
            self._set_value(value)

    def set_indeterminate(self) -> None:
        """Static full-width soft bar (used for indefinite sessions — no animation loop)."""
        self._anim.stop()
        self._indeterminate = True
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, 6)

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        track = QPainterPath()
        track.addRoundedRect(rect, radius, radius)
        p.fillPath(track, t.color("surface_alt"))
        if self._indeterminate:
            p.fillPath(track, t.color("accent", 0.35))
            return
        if self._value > 0:
            fill = QRectF(rect.x(), rect.y(), max(rect.height(), rect.width() * self._value), rect.height())
            path = QPainterPath()
            path.addRoundedRect(fill, radius, radius)
            p.fillPath(path, t.color("accent"))
