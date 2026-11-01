"""Animated toggle switch and the labelled power-option row that uses it."""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QVBoxLayout, QWidget

from awake.ui.components.common import IconTile, label
from awake.ui.components.power_button import _mix
from awake.ui.styles.theme import theme, theme_manager


class ToggleSwitch(QAbstractButton):
    WIDTH, HEIGHT = 38, 22

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"position", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        theme_manager().theme_changed.connect(self.update)

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, value: float) -> None:
        self._pos = value
        self.update()

    position = Property(float, _get_pos, _set_pos)

    def set_checked_silently(self, checked: bool) -> None:
        """Update the visual state without emitting ``toggled``/``clicked``."""
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._anim.stop()
        self._set_pos(1.0 if checked else 0.0)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.WIDTH, self.HEIGHT)

    def paintEvent(self, _event) -> None:  # noqa: N802
        th = theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.WIDTH - 1, self.HEIGHT - 1)
        radius = rect.height() / 2
        track = _mix(th.color("border_strong"), th.color("accent"), self._pos)
        if not self.isEnabled():
            track.setAlphaF(track.alphaF() * 0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, track)
        knob_d = rect.height() - 4
        x = rect.x() + 2 + (rect.width() - knob_d - 4) * self._pos
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(th.color("shadow"))
        p.drawEllipse(QRectF(x, rect.y() + 2.6, knob_d, knob_d))
        p.setBrush(th.color("knob"))
        p.drawEllipse(QRectF(x, rect.y() + 2, knob_d, knob_d))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(th.color("accent_border"))
            p.drawRoundedRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5), radius, radius)


class PowerToggleRow(QFrame):
    """Icon tile, title + description, and a switch. Clicking anywhere toggles."""

    toggled = Signal(bool)

    def __init__(self, icon_name: str, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("rowHover", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(12)
        self.tile = IconTile(icon_name)
        row.addWidget(self.tile, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = label(title, "rowTitle")
        self.description = label(description, "rowDescription", wrap=True)
        text.addWidget(self.title)
        text.addWidget(self.description)
        row.addLayout(text, 1)
        self.switch = ToggleSwitch()
        self.switch.setAccessibleName(title)
        row.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.switch.clicked.connect(self._emit)

    def _emit(self, checked: bool) -> None:
        self.tile.set_active(checked)
        self.toggled.emit(checked)

    def set_checked(self, checked: bool) -> None:
        self.switch.set_checked_silently(checked)
        self.tile.set_active(checked)

    def is_checked(self) -> bool:
        return self.switch.isChecked()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.switch.click()
        super().mouseReleaseEvent(event)
