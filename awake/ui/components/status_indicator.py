"""Active/idle indicator dot and the status pill shown in page headers."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget

from awake.ui.components.common import label, repolish
from awake.ui.styles.theme import theme, theme_manager


class StatusDot(QWidget):
    def __init__(self, size: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._size = size
        self.setFixedSize(size + 8, size + 8)  # room for the halo
        theme_manager().theme_changed.connect(self.update)

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()

    def paintEvent(self, _event) -> None:  # noqa: N802
        th = theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        center = QPointF(self.width() / 2, self.height() / 2)
        if self._active:
            p.setBrush(th.color("success", 0.22))
            p.drawEllipse(center, self._size / 2 + 3.5, self._size / 2 + 3.5)
            p.setBrush(th.color("success"))
        else:
            p.setBrush(th.color("text_muted"))
        p.drawEllipse(center, self._size / 2, self._size / 2)


class StatusPill(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusPill")
        self.setFixedHeight(28)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 12, 0)
        row.setSpacing(2)
        self.dot = StatusDot(7)
        self.text = label("Idle")
        row.addWidget(self.dot)
        row.addWidget(self.text)
        self.set_active(False)

    def set_active(self, active: bool, text: str | None = None) -> None:
        self.dot.set_active(active)
        self.text.setText(text or ("Active" if active else "Idle"))
        if self.property("active") != active:
            self.setProperty("active", active)
            repolish(self)
            repolish(self.text)
