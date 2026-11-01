"""Deadline timer that is robust against sleep, clock drift and long waits.

Instead of counting ticks, the timer stores an absolute wall-clock deadline and
re-checks it each time it wakes. It wakes at most once per ``max_interval``
(default 60 s), so a timer whose monotonic clock paused while the machine slept
(e.g. lid closed) still expires promptly afterwards. While no deadline is set,
there is no timer running at all.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from awake.database.models import utcnow

Clock = Callable[[], datetime]


class TimerManager(QObject):
    expired = Signal()

    def __init__(self, clock: Clock = utcnow, max_interval_ms: int = 60_000, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._clock = clock
        self._max_interval_ms = max_interval_ms
        self._deadline: datetime | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.check)

    @property
    def deadline(self) -> datetime | None:
        return self._deadline

    @property
    def is_running(self) -> bool:
        return self._deadline is not None

    def schedule(self, deadline: datetime) -> None:
        self._deadline = deadline
        self.check()

    def cancel(self) -> None:
        self._deadline = None
        self._timer.stop()

    def check(self) -> None:
        """Emit ``expired`` if the deadline has passed, otherwise re-arm."""
        if self._deadline is None:
            return
        remaining_ms = (self._deadline - self._clock()).total_seconds() * 1000
        if remaining_ms <= 0:
            self._deadline = None
            self._timer.stop()
            self.expired.emit()
            return
        self._timer.start(int(min(remaining_ms + 1, self._max_interval_ms)))
