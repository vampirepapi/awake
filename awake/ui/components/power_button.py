"""The large circular power button on the dashboard."""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QAbstractButton, QWidget

from awake.ui import icons
from awake.ui.styles.theme import ANIMATION_MS, theme, theme_manager


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor.fromRgbF(
        a.redF() + (b.redF() - a.redF()) * t,
        a.greenF() + (b.greenF() - a.greenF()) * t,
        a.blueF() + (b.blueF() - a.blueF()) * t,
        a.alphaF() + (b.alphaF() - a.alphaF()) * t,
    )


class PowerButton(QAbstractButton):
    """Neutral when off, solid purple with a soft glow when on.

    ``set_active`` reflects the *confirmed* session state; clicking only emits
    ``clicked`` — the owner decides what happens and calls ``set_active`` once
    the native request has succeeded.
    """

    DIAMETER = 148
    MARGIN = 34  # room for the progress ring and glow

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._t = 0.0          # 0 = off, 1 = on (animated)
        self._hover = 0.0      # animated hover amount
        self._progress: float | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName("Toggle Awake")
        size = self.DIAMETER + 2 * self.MARGIN
        self.setFixedSize(size, size)

        self._state_anim = QPropertyAnimation(self, b"amount", self)
        self._state_anim.setDuration(ANIMATION_MS + 40)
        self._state_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim = QPropertyAnimation(self, b"hoverAmount", self)
        self._hover_anim.setDuration(150)
        theme_manager().theme_changed.connect(self.update)

    # ------------------------------------------------------------------ animated properties

    def _get_amount(self) -> float:
        return self._t

    def _set_amount(self, value: float) -> None:
        self._t = value
        self.update()

    amount = Property(float, _get_amount, _set_amount)

    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hoverAmount = Property(float, _get_hover, _set_hover)  # noqa: N815 - Qt property name

    # ------------------------------------------------------------------ API

    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool, animate: bool = True) -> None:
        target = 1.0 if active else 0.0
        if active == self._active and (animate or self._t == target):
            return
        self._active = active
        self.setAccessibleDescription("Awake is on" if active else "Awake is off")
        if animate and self.isVisible():
            self._state_anim.stop()
            self._state_anim.setStartValue(self._t)
            self._state_anim.setEndValue(target)
            self._state_anim.start()
        else:
            self._state_anim.stop()
            self._set_amount(target)

    def set_progress(self, progress: float | None) -> None:
        """Fraction elapsed for timed sessions; ``None`` hides the arc."""
        if progress != self._progress:
            self._progress = progress
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()

    # ------------------------------------------------------------------ events

    def enterEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def _animate_hover(self, target: float) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(target)
        self._hover_anim.start()

    def hitButton(self, pos) -> bool:  # noqa: N802
        center = QPointF(self.width() / 2, self.height() / 2)
        delta = QPointF(pos) - center
        return (delta.x() ** 2 + delta.y() ** 2) ** 0.5 <= self.DIAMETER / 2

    def paintEvent(self, _event) -> None:  # noqa: N802
        th = theme()
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        r = self.DIAMETER / 2
        if self.isDown():
            r *= 0.975

        # Glow (only when on)
        if t > 0.01:
            glow = QRadialGradient(center, r + self.MARGIN)
            accent = th.color("accent")
            accent.setAlphaF(0.28 * t if th.is_dark else 0.22 * t)
            glow.setColorAt(0.55, accent)
            fade = th.color("accent")
            fade.setAlphaF(0.0)
            glow.setColorAt(1.0, fade)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(center, r + self.MARGIN, r + self.MARGIN)

        # Outer ring track + progress arc
        ring_r = r + 14
        ring_rect = QRectF(center.x() - ring_r, center.y() - ring_r, 2 * ring_r, 2 * ring_r)
        track = _mix(th.color("border"), th.color("accent", 0.25), t)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(track, 2))
        p.drawEllipse(ring_rect)
        if self._progress is not None and t > 0.01:
            remaining = 1.0 - self._progress
            pen = QPen(th.color("accent", t), 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(ring_rect, 90 * 16, int(-360 * 16 * remaining))

        # Body
        off_fill = _mix(th.color("surface_alt"), th.color("surface_hover"), self._hover)
        on_fill = _mix(th.color("accent"), th.color("accent_hover"), self._hover)
        fill = _mix(off_fill, on_fill, t)
        border = _mix(_mix(th.color("border_strong"), th.color("text_muted"), self._hover * 0.5),
                      th.color("accent_hover"), t)
        p.setPen(QPen(border, 1.5))
        p.setBrush(fill)
        p.drawEllipse(center, r, r)

        # Glyph
        glyph_color = _mix(_mix(th.color("text_secondary"), th.color("text"), self._hover), th.color("accent_text"), t)
        glyph = r * 0.78
        icons.render_icon(
            p, "power", QRectF(center.x() - glyph / 2, center.y() - glyph / 2, glyph, glyph), glyph_color,
            stroke_width=1.6,
        )

        # Keyboard focus ring
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(th.color("accent_border"), 2))
            p.drawEllipse(center, r + 5, r + 5)
