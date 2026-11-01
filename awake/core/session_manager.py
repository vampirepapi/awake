"""Session lifecycle — the only component that starts, changes or ends sessions.

Ordering guarantees:

* start:  native power acquire  →  persist  →  emit ``session_started``.
  If the native call fails nothing is persisted and :class:`PowerError` is
  raised, so the UI can never show a session that is not really active.
* stop:   native power release  →  persist  →  emit ``session_ended``.

A future AgentMonitor/ActivityCoordinator would call ``start``/``stop`` here
(or hold its own PowerManager owner) without touching the power layer.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from awake.core.power_manager import PowerError, PowerManager
from awake.core.timer_manager import Clock, TimerManager
from awake.database.database import DatabaseError
from awake.database.models import EndReason, Session, SessionStatus, new_session_id, utcnow
from awake.database.repository import SessionRepository
from awake.logging_setup import kv

log = logging.getLogger("awake.session")

SUPPORTED_DURATIONS: tuple[int | None, ...] = (1800, 3600, 7200, None)
POWER_OWNER = "session"


class SessionError(RuntimeError):
    """User-facing error about a session request (bad input, wrong state)."""


class SessionAlreadyActiveError(SessionError):
    pass


def validate_duration(duration: object) -> int | None:
    if duration is None:
        return None
    if isinstance(duration, bool) or not isinstance(duration, int) or duration not in SUPPORTED_DURATIONS:
        raise SessionError(f"Unsupported duration: {duration!r}. Choose 30 minutes, 1 hour, 2 hours or indefinite.")
    return duration


class SessionManager(QObject):
    session_started = Signal(object)   # Session
    session_changed = Signal(object)   # Session (duration or power options changed)
    session_ended = Signal(object)     # Session (final state)
    error_occurred = Signal(str)       # non-fatal, user-facing message

    def __init__(
        self,
        power: PowerManager,
        repository: SessionRepository,
        clock: Clock = utcnow,
        timer: TimerManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._power = power
        self._repo = repository
        self._clock = clock
        self._timer = timer or TimerManager(clock=clock, parent=self)
        self._timer.expired.connect(self._on_expired)
        self._active: Session | None = None
        self._shut_down = False

    # ------------------------------------------------------------------ queries

    @property
    def active_session(self) -> Session | None:
        return self._active

    @property
    def is_active(self) -> bool:
        return self._active is not None

    def now(self) -> datetime:
        return self._clock()

    # ------------------------------------------------------------------ commands

    def start(self, duration: int | None, *, system_awake: bool = True, display_awake: bool = False) -> Session:
        if self._shut_down:
            raise SessionError("Awake is shutting down.")
        duration = validate_duration(duration)
        if self._active is not None:
            raise SessionAlreadyActiveError("An awake session is already running.")
        if not (system_awake or display_awake):
            raise SessionError("Turn on “Keep system awake” or “Keep display awake” to start a session.")

        # 1. Native request first. PowerError propagates — nothing else happens.
        self._power.acquire(POWER_OWNER, system=system_awake, display=display_awake)

        session = Session(
            id=new_session_id(),
            started_at=self._clock(),
            duration_seconds=duration,
            system_awake=system_awake,
            display_awake=display_awake,
        )
        self._active = session
        self._persist(session, insert=True)
        if session.ends_at is not None:
            self._timer.schedule(session.ends_at)
        log.info(
            kv("session_started", id=session.id, duration=duration or "indefinite",
               system=system_awake, display=display_awake)
        )
        self.session_started.emit(session)
        return session

    def stop(self, reason: EndReason = EndReason.USER_STOPPED) -> Session | None:
        session = self._active
        if session is None:
            return None
        self._timer.cancel()
        status = SessionStatus.COMPLETED if reason is EndReason.EXPIRED else SessionStatus.STOPPED
        release_error: PowerError | None = None
        try:
            self._power.release(POWER_OWNER)
        except PowerError as exc:
            release_error = exc
            log.error(kv("session_release_failed", id=session.id, error=exc))

        ended = session.with_changes(status=status, ended_at=self._clock(), end_reason=reason)
        self._active = None
        self._persist(ended)
        log.info(kv("session_stopped", id=ended.id, reason=reason.value, status=status.value))
        self.session_ended.emit(ended)
        if release_error is not None:
            self.error_occurred.emit(
                f"The session ended, but the system did not confirm releasing the power request: {release_error}"
            )
        return ended

    def toggle(self, duration: int | None, *, system_awake: bool, display_awake: bool) -> Session | None:
        """Start if idle, stop if active. Returns the new active session (or None)."""
        if self._active is not None:
            self.stop(EndReason.USER_STOPPED)
            return None
        return self.start(duration, system_awake=system_awake, display_awake=display_awake)

    def set_duration(self, duration: int | None) -> Session | None:
        """Change the active session so it runs ``duration`` from *now* (or indefinitely)."""
        duration = validate_duration(duration)
        session = self._active
        if session is None:
            return None
        now = self._clock()
        if duration is None:
            planned = None
        else:
            elapsed = int((now - session.started_at).total_seconds())
            planned = elapsed + duration
        updated = session.with_changes(duration_seconds=planned)
        self._active = updated
        self._persist(updated)
        if updated.ends_at is None:
            self._timer.cancel()
        else:
            self._timer.schedule(updated.ends_at)
        log.info(kv("session_duration_changed", id=session.id, duration=duration or "indefinite"))
        self.session_changed.emit(updated)
        return updated

    def set_power_options(self, *, system_awake: bool, display_awake: bool) -> Session | None:
        """Change what the active session keeps awake.

        Turning both options off ends the session (there is nothing left to keep awake).
        Raises PowerError if the new request cannot be honoured; the old one stays in place.
        """
        session = self._active
        if session is None:
            return None
        if not (system_awake or display_awake):
            return self.stop(EndReason.USER_STOPPED)
        if (system_awake, display_awake) == (session.system_awake, session.display_awake):
            return session
        self._power.acquire(POWER_OWNER, system=system_awake, display=display_awake)
        updated = session.with_changes(system_awake=system_awake, display_awake=display_awake)
        self._active = updated
        self._persist(updated)
        log.info(kv("session_power_changed", id=session.id, system=system_awake, display=display_awake))
        self.session_changed.emit(updated)
        return updated

    def shutdown(self) -> None:
        """Stop any active session and release every native request. Idempotent."""
        if self._shut_down:
            return
        self._shut_down = True
        try:
            self.stop(EndReason.APP_QUIT)
        finally:
            try:
                self._power.release_all()
            except PowerError as exc:
                log.error(kv("shutdown_release_failed", error=exc))

    # ------------------------------------------------------------------ internals

    def _on_expired(self) -> None:
        if self._active is None:
            return
        log.info(kv("session_expired", id=self._active.id))
        self.stop(EndReason.EXPIRED)

    def _persist(self, session: Session, *, insert: bool = False) -> None:
        # Persistence failures must not stop the machine staying awake — keeping
        # the power request is the primary job — but they are never silent.
        try:
            if insert:
                self._repo.insert(session)
            else:
                self._repo.update(session)
        except DatabaseError as exc:
            log.error(kv("session_persist_failed", id=session.id, error=exc))
            self.error_occurred.emit(f"Session history could not be saved: {exc}")
