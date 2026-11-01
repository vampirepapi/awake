"""Test doubles for native power APIs and time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from awake.core.power_manager import PowerBackend, PowerError
from awake.database.models import PowerState


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeBackend(PowerBackend):
    name = "fake"

    def __init__(self) -> None:
        self.system = False
        self.display = False
        self.calls: list[str] = []
        self.fail_on: set[str] = set()

    def _do(self, call: str) -> None:
        self.calls.append(call)
        if call in self.fail_on:
            raise PowerError(f"simulated failure: {call}")

    def prevent_system_sleep(self) -> None:
        self._do("prevent_system")
        self.system = True

    def allow_system_sleep(self) -> None:
        self._do("allow_system")
        self.system = False

    def prevent_display_sleep(self) -> None:
        self._do("prevent_display")
        self.display = True

    def allow_display_sleep(self) -> None:
        self._do("allow_display")
        self.display = False

    def release_all(self) -> None:
        self._do("release_all")
        self.system = self.display = False

    def get_status(self) -> PowerState:
        return PowerState(self.system or self.display, self.display, backend=self.name)
