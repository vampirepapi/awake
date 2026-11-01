"""macOS menu-bar / Windows notification-area integration."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from awake import APP_NAME
from awake.core.agents import AgentCoordinator
from awake.core.application_state import ApplicationState
from awake.core.session_manager import SUPPORTED_DURATIONS
from awake.database.models import EndReason, Session
from awake.logging_setup import kv
from awake.ui import agent_text, icons
from awake.ui.formatting import (
    DURATION_LABELS,
    format_duration,
    format_time,
    menu_bar_label,
    seconds_until_label_changes,
)

log = logging.getLogger("awake.tray")

IS_MAC = sys.platform == "darwin"


class TrayController(QObject):
    def __init__(
        self,
        state: ApplicationState,
        open_window: Callable[[str | None], None],
        quit_app: Callable[[], None],
        agents: AgentCoordinator | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._agents = agents
        self._open_window = open_window
        self._quit = quit_app
        self._hint_shown = False

        # macOS: Qt's menu-bar item is a fixed square; widen it so the countdown fits.
        self._widener = None
        if IS_MAC:
            from awake.platforms.macos import StatusItemWidener

            self._widener = StatusItemWidener()
        # Fires once per displayed-minute change while a timed session runs (macOS label).
        self._label_timer = QTimer(self)
        self._label_timer.setSingleShot(True)
        self._label_timer.setTimerType(Qt.TimerType.PreciseTimer)  # flip the minute on time
        self._label_timer.timeout.connect(self.refresh)

        self.tray = QSystemTrayIcon(self)
        self.menu = QMenu()
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.menu.aboutToShow.connect(self.refresh)

        sessions = state.sessions
        sessions.session_started.connect(self._on_started)
        sessions.session_changed.connect(lambda _s: self.refresh())
        sessions.session_ended.connect(self._on_ended)
        state.selection_changed.connect(self.refresh)
        state.settings.changed.connect(
            lambda key, _v: self.refresh() if key in ("show_menu_bar_timer", "agent_detection_enabled") else None
        )
        if agents is not None:
            agents.changed.connect(lambda _activity: self.refresh())
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_: self.refresh())

        self.refresh()
        self.tray.show()
        QTimer.singleShot(0, self.refresh)  # the native item exists only after show()
        log.info(kv("tray_shown", available=QSystemTrayIcon.isSystemTrayAvailable()))

    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    # ------------------------------------------------------------------ menu

    def _build_menu(self) -> None:
        m = self.menu
        self.status_action = QAction("Awake is OFF", m)
        self.status_action.setEnabled(False)
        m.addAction(self.status_action)
        m.addSeparator()

        self.toggle_action = QAction("Start Awake", m)
        self.toggle_action.triggered.connect(self._state.toggle_session)
        m.addAction(self.toggle_action)
        m.addSeparator()

        self.duration_group = QActionGroup(m)
        self.duration_group.setExclusive(True)
        self.duration_actions: dict[int | None, QAction] = {}
        for duration in SUPPORTED_DURATIONS:
            action = QAction(DURATION_LABELS[duration], m, checkable=True)
            action.triggered.connect(lambda _=False, d=duration: self._state.select_duration(d))
            self.duration_group.addAction(action)
            self.duration_actions[duration] = action
            m.addAction(action)
        m.addSeparator()

        self.system_action = QAction("Keep system awake", m, checkable=True)
        self.system_action.triggered.connect(self._state.set_keep_system)
        self.display_action = QAction("Keep display awake", m, checkable=True)
        self.display_action.triggered.connect(self._state.set_keep_display)
        self.agents_action = QAction("Keep awake while AI agents work", m, checkable=True)
        self.agents_action.triggered.connect(
            lambda on: self._state.settings.set("agent_detection_enabled", bool(on))
        )
        m.addAction(self.system_action)
        m.addAction(self.display_action)
        m.addAction(self.agents_action)
        m.addSeparator()

        open_action = QAction(f"Open {APP_NAME}", m)
        open_action.triggered.connect(lambda: self._open_window("home"))
        settings_action = QAction("Settings…", m)
        settings_action.triggered.connect(lambda: self._open_window("settings"))
        m.addAction(open_action)
        m.addAction(settings_action)
        m.addSeparator()
        quit_action = QAction(f"Quit {APP_NAME}", m)
        quit_action.setMenuRole(QAction.MenuRole.NoRole)  # keep it in this menu on macOS
        quit_action.triggered.connect(self._quit)
        m.addAction(quit_action)

    def refresh(self) -> None:
        session = self._state.active_session
        now = self._state.sessions.now()
        agent_status = agent_text.short_status(self._agents.activity if self._agents is not None else None)
        if session is None and agent_status is not None:
            status = f"Awake is ON · {agent_status}"
            tooltip = f"{APP_NAME} — on, {agent_status}"
        elif session is None:
            status = "Awake is OFF"
            tooltip = f"{APP_NAME} — off"
        elif session.is_indefinite:
            status = f"Awake is ON · since {format_time(session.started_at)}"
            tooltip = f"{APP_NAME} — on indefinitely"
        else:
            left = menu_bar_label(session.remaining_seconds(now) or 0)
            status = f"Awake is ON · {left} left"
            tooltip = f"{APP_NAME} — on, {left} left"
        self.status_action.setText(status)
        self.toggle_action.setText("Stop Awake" if session else "Start Awake")
        self.tray.setToolTip(tooltip)

        selected = self._state.selected_duration
        for duration, action in self.duration_actions.items():
            action.setChecked(duration == selected)
        self.system_action.setChecked(self._state.keep_system)
        self.display_action.setChecked(self._state.keep_display)
        self.agents_action.setChecked(bool(self._state.settings.get("agent_detection_enabled")))

        dark = QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
        label, infinite = self._menu_bar_label(session, now, agent_holding=agent_status is not None)
        active = session is not None or agent_status is not None
        self.tray.setIcon(icons.tray_icon(active, mask=IS_MAC, dark_background=dark,
                                          label=label, infinite=infinite))

    def _wide_item_available(self) -> bool:
        if self._widener is None:
            return True  # tests / non-mac: nothing to widen
        return self.tray.isVisible() and self._widener.apply()

    def _menu_bar_label(
        self, session: Session | None, now, agent_holding: bool = False  # noqa: ANN001
    ) -> tuple[str | None, bool]:
        """Text (or ∞) to draw beside the macOS menu-bar icon; schedules the next update.

        A manual session's countdown wins; otherwise ``AI`` while an agent holds the system.
        """
        self._label_timer.stop()
        if not IS_MAC or not self._state.settings.get("show_menu_bar_timer"):
            return None, False
        if session is None and not agent_holding:
            return None, False
        if not self._wide_item_available():
            return None, False  # a wide image in a square slot would be shrunk to a third
        if session is None:
            return "AI", False
        if session.is_indefinite:
            return None, True
        remaining = session.remaining_seconds(now) or 0.0
        # +50 ms so we land just after the boundary, not just before it.
        self._label_timer.start(int(seconds_until_label_changes(remaining) * 1000) + 50)
        return menu_bar_label(remaining), False

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # macOS shows the menu on click; on Windows a left click opens the window.
        if not IS_MAC and reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                     QSystemTrayIcon.ActivationReason.DoubleClick):
            self._open_window(None)

    # ------------------------------------------------------------------ notifications

    def notify(self, title: str, message: str) -> None:
        if self.tray.isVisible() and self.tray.supportsMessages():
            self.tray.showMessage(title, message, icons.app_icon(), 5000)

    def show_running_hint(self) -> None:
        if self._hint_shown:
            return
        self._hint_shown = True
        where = "menu bar" if IS_MAC else "system tray"
        self.notify(APP_NAME, f"Awake is still running in the {where}. Use Quit Awake to exit.")

    def _on_started(self, session: Session) -> None:
        self.refresh()
        if self._state.settings.get("notify_on_start"):
            if session.is_indefinite:
                detail = "until you stop it"
            else:
                detail = f"until {format_time(session.ends_at)}"
            self.notify("Awake is on", f"Your computer will stay awake {detail}.")

    def _on_ended(self, session: Session) -> None:
        self.refresh()
        settings = self._state.settings
        if session.end_reason is EndReason.EXPIRED and settings.get("notify_on_expire"):
            self.notify("Awake session finished",
                        f"Your {format_duration(session.elapsed_seconds())} session ended. "
                        "Your computer can sleep normally again.")
        elif session.end_reason is EndReason.USER_STOPPED and settings.get("notify_on_stop"):
            self.notify("Awake is off", "Your computer can sleep normally again.")

    def hide(self) -> None:
        self.tray.hide()
