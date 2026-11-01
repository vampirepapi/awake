"""Human-friendly formatting of durations, times and session states."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from PySide6.QtCore import QLocale, QTime

from awake.database.models import Session, SessionStatus

DURATION_LABELS: dict[int | None, str] = {1800: "30 minutes", 3600: "1 hour", 7200: "2 hours", None: "Indefinite"}
DURATION_SHORT: dict[int | None, str] = {1800: "30 min", 3600: "1 hour", 7200: "2 hours", None: "Indefinite"}

STATUS_LABELS: dict[SessionStatus, str] = {
    SessionStatus.ACTIVE: "Active",
    SessionStatus.COMPLETED: "Completed",
    SessionStatus.STOPPED: "Stopped",
    SessionStatus.INTERRUPTED: "Interrupted",
    SessionStatus.FAILED: "Failed",
}


def format_duration(seconds: float | None) -> str:
    """Compact duration: ``45s``, ``12m``, ``1h 05m``, ``2h``."""
    if seconds is None:
        return "—"
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours == 0:
        return f"{minutes}m"
    return f"{hours}h" if minutes == 0 else f"{hours}h {minutes:02d}m"


def format_planned(duration: int | None) -> str:
    if duration in DURATION_LABELS:
        return DURATION_LABELS[duration]
    return format_duration(duration)


def format_countdown(seconds: float) -> str:
    """Clock-style countdown, rounded up so it reaches 0:00 exactly at expiry."""
    total = max(0, math.ceil(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def local(dt: datetime) -> datetime:
    return dt.astimezone()


def format_time(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    value = local(dt)
    return QLocale.system().toString(QTime(value.hour, value.minute), QLocale.FormatType.ShortFormat)


def format_day(dt: datetime, today: date | None = None) -> str:
    value = local(dt).date()
    today = today or datetime.now().astimezone().date()
    if value == today:
        return "Today"
    if value == today - timedelta(days=1):
        return "Yesterday"
    if value.year == today.year:
        return f"{value:%b} {value.day}"
    return f"{value:%b} {value.day}, {value.year}"


def format_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return f"{format_day(dt)}, {format_time(dt)}"


def session_span(session: Session) -> str:
    start = format_datetime(session.started_at)
    if session.ended_at is None:
        return f"{start} → {'now' if session.is_active else 'unknown'}"
    same_day = local(session.started_at).date() == local(session.ended_at).date()
    end = format_time(session.ended_at) if same_day else format_datetime(session.ended_at)
    return f"{start} → {end}"


def menu_bar_label(remaining_seconds: float) -> str:
    """Countdown shown next to the menu-bar icon, rounded *up* to the minute.

    ``1h 59m`` … ``1h 00m``, ``59m`` … ``1m`` — it never reads ``0m`` while the
    session is still running.
    """
    total_minutes = max(1, math.ceil(remaining_seconds / 60))
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def seconds_until_label_changes(remaining_seconds: float) -> float:
    """How long until :func:`menu_bar_label` shows a different value."""
    if remaining_seconds <= 60:
        return max(0.0, remaining_seconds)
    total_minutes = math.ceil(remaining_seconds / 60)
    return remaining_seconds - (total_minutes - 1) * 60
