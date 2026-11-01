"""Plain data models shared by the database, core and UI layers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum


class SessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"      # timer ran to the end
    STOPPED = "stopped"          # ended manually (or by quitting the app)
    INTERRUPTED = "interrupted"  # app was terminated unexpectedly while active
    FAILED = "failed"            # native power request was lost mid-session


class EndReason(StrEnum):
    EXPIRED = "expired"
    USER_STOPPED = "user_stopped"
    APP_QUIT = "app_quit"
    APP_TERMINATED = "app_terminated"
    POWER_ERROR = "power_error"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_session_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class Session:
    """One awake session.

    ``duration_seconds`` is the *planned* duration; ``None`` means indefinite.
    """

    id: str
    started_at: datetime
    duration_seconds: int | None
    system_awake: bool
    display_awake: bool
    status: SessionStatus = SessionStatus.ACTIVE
    ended_at: datetime | None = None
    end_reason: EndReason | None = None

    @property
    def is_indefinite(self) -> bool:
        return self.duration_seconds is None

    @property
    def is_active(self) -> bool:
        return self.status is SessionStatus.ACTIVE

    @property
    def ends_at(self) -> datetime | None:
        if self.duration_seconds is None:
            return None
        return self.started_at + timedelta(seconds=self.duration_seconds)

    @property
    def session_type(self) -> str:
        return "Indefinite" if self.is_indefinite else "Timed"

    def remaining_seconds(self, now: datetime) -> float | None:
        ends_at = self.ends_at
        if ends_at is None:
            return None
        return max(0.0, (ends_at - now).total_seconds())

    def elapsed_seconds(self, now: datetime | None = None) -> float | None:
        """Actual time awake. ``None`` when an interrupted session has no known end."""
        end = self.ended_at
        if end is None:
            if not self.is_active or now is None:
                return None
            end = now
        return max(0.0, (end - self.started_at).total_seconds())

    def progress(self, now: datetime) -> float | None:
        """Fraction of a timed session already elapsed (0..1)."""
        if self.duration_seconds is None:
            return None
        elapsed = (now - self.started_at).total_seconds()
        return min(1.0, max(0.0, elapsed / self.duration_seconds))

    def with_changes(self, **changes: object) -> "Session":
        return replace(self, **changes)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class PowerState:
    """What the native layer is currently holding on behalf of Awake."""

    system_awake: bool = False
    display_awake: bool = False
    backend: str = field(default="unknown", compare=False)

    @property
    def any_active(self) -> bool:
        return self.system_awake or self.display_awake
