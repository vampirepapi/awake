"""Shared, UI-agnostic application state.

The dashboard and the tray menu both show (and change) the same selection:
duration, "keep system awake" and "keep display awake". ApplicationState holds
that selection, forwards changes to the SessionManager while a session is
running, and turns failures into a single ``error`` signal the UI can display.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from awake.core.power_manager import PowerError
from awake.core.session_manager import SessionError, SessionManager, validate_duration
from awake.core.settings_manager import SettingsManager, StartupBehaviour
from awake.database.models import Session
from awake.logging_setup import kv

log = logging.getLogger("awake.state")


class ApplicationState(QObject):
    selection_changed = Signal()
    error = Signal(str, str)  # title, message

    def __init__(self, sessions: SessionManager, settings: SettingsManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sessions = sessions
        self.settings = settings
        self._duration: int | None = settings.get("default_duration")
        self._keep_system: bool = settings.get("keep_system_awake_default")
        self._keep_display: bool = settings.get("keep_display_awake_default")
        sessions.session_started.connect(self._sync_from_session)
        sessions.session_changed.connect(self._sync_from_session)
        sessions.error_occurred.connect(lambda msg: self.error.emit("Awake", msg))

    # ------------------------------------------------------------------ selection

    @property
    def selected_duration(self) -> int | None:
        return self._duration

    @property
    def keep_system(self) -> bool:
        return self._keep_system

    @property
    def keep_display(self) -> bool:
        return self._keep_display

    @property
    def active_session(self) -> Session | None:
        return self.sessions.active_session

    def select_duration(self, duration: int | None) -> None:
        duration = validate_duration(duration)
        previous, self._duration = self._duration, duration  # set first so listeners never see the stale value
        if self.sessions.is_active:
            try:
                self.sessions.set_duration(duration)  # emits session_changed → selection_changed
            except SessionError as exc:
                self._duration = previous
                self._report("Could not change duration", exc)
                return
            return
        self.selection_changed.emit()

    def set_keep_system(self, enabled: bool) -> None:
        self._set_power(enabled, self._keep_display)

    def set_keep_display(self, enabled: bool) -> None:
        self._set_power(self._keep_system, enabled)

    def _set_power(self, system: bool, display: bool) -> None:
        if self.sessions.is_active:
            try:
                self.sessions.set_power_options(system_awake=system, display_awake=display)
            except PowerError as exc:
                self._report("Could not change power settings", exc)
                self.selection_changed.emit()  # make widgets snap back
                return
        self._keep_system, self._keep_display = system, display
        self.selection_changed.emit()

    # ------------------------------------------------------------------ session control

    def toggle_session(self) -> bool:
        """Start or stop a session with the current selection. Returns True on success."""
        try:
            self.sessions.toggle(
                self._duration, system_awake=self._keep_system, display_awake=self._keep_display
            )
            return True
        except PowerError as exc:
            self._report("Awake could not keep your computer awake", exc)
        except SessionError as exc:
            self._report("Awake", exc)
        return False

    def start_session(self) -> bool:
        return self.toggle_session() if not self.sessions.is_active else True

    def stop_session(self) -> None:
        self.sessions.stop()

    def apply_startup_behaviour(self) -> None:
        if self.settings.get("startup_behaviour") == StartupBehaviour.START_SESSION and not self.sessions.is_active:
            log.info(kv("startup_session_requested"))
            self.start_session()

    # ------------------------------------------------------------------ internals

    def _sync_from_session(self, session: Session) -> None:
        if session.ends_at is None:
            self._duration = None
        self._keep_system = session.system_awake
        self._keep_display = session.display_awake
        self.selection_changed.emit()

    def _report(self, title: str, exc: Exception) -> None:
        log.warning(kv("user_action_failed", title=title, error=exc))
        self.error.emit(title, str(exc))
