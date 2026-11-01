"""Segmented control for the four supported session durations."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

from awake.core.session_manager import SUPPORTED_DURATIONS
from awake.ui.formatting import DURATION_LABELS, DURATION_SHORT


class DurationSelector(QWidget):
    duration_selected = Signal(object)  # int | None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[int | None, QPushButton] = {}
        for duration in SUPPORTED_DURATIONS:
            button = QPushButton(DURATION_SHORT[duration])
            button.setProperty("segment", True)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(DURATION_LABELS[duration])
            button.setAccessibleName(f"Duration: {DURATION_LABELS[duration]}")
            button.clicked.connect(lambda _=False, d=duration: self.duration_selected.emit(d))
            self._group.addButton(button)
            self._buttons[duration] = button
            layout.addWidget(button, 1)

    def set_duration(self, duration: int | None) -> None:
        button = self._buttons.get(duration)
        if button is not None and not button.isChecked():
            button.setChecked(True)
