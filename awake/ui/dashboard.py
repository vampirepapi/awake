"""Home screen: power button, duration, power options, time remaining, recent sessions."""

from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from awake.core.agents import AgentCoordinator
from awake.core.application_state import ApplicationState
from awake.database.database import DatabaseError
from awake.database.models import Session
from awake.database.repository import SessionRepository
from awake.ui import agent_text
from awake.ui.components.common import Card, Divider, ProgressBar, SectionHeader, label, repolish
from awake.ui.components.duration_selector import DurationSelector
from awake.ui.components.power_button import PowerButton
from awake.ui.components.power_toggle import PowerToggleRow
from awake.ui.components.session_card import SessionCard
from awake.ui.components.status_indicator import StatusDot, StatusPill
from awake.logging_setup import kv
from awake.ui.formatting import format_countdown, format_duration, format_planned, format_time

log = logging.getLogger("awake.ui")


def page_header(title: str, subtitle: str) -> tuple[QHBoxLayout, QVBoxLayout]:
    row = QHBoxLayout()
    row.setSpacing(16)
    text = QVBoxLayout()
    text.setSpacing(2)
    text.addWidget(label(title, "title"))
    text.addWidget(label(subtitle, "subtitle"))
    row.addLayout(text, 1)
    return row, text


class HeroCard(Card):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=24, spacing=0)
        self.body.addStretch(1)
        self.button = PowerButton()
        self.body.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.body.addSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(0)
        title_row.addStretch(1)
        self.dot = StatusDot(8)
        self.title = label("Awake is OFF", "heroTitle")
        title_row.addWidget(self.dot)
        title_row.addWidget(self.title)
        title_row.addSpacing(16)  # balance the dot so the text looks centred
        title_row.addStretch(1)
        self.body.addLayout(title_row)
        self.body.addSpacing(4)
        self.description = label("Your system can sleep normally.", "body")
        self.description.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.description)
        self.body.addSpacing(28)

        duration_label = label("DURATION", "section")
        self.body.addWidget(duration_label)
        self.body.addSpacing(8)
        self.durations = DurationSelector()
        self.body.addWidget(self.durations)
        self.body.addStretch(1)

    def set_active(self, active: bool, animate: bool = True, agent_status: str | None = None) -> None:
        """``active`` = manual session (drives the button); ``agent_status`` = an agent is holding."""
        self.button.set_active(active, animate)
        on = active or agent_status is not None
        self.dot.set_active(on)
        self.title.setText("Awake is ON" if on else "Awake is OFF")
        if active:
            self.description.setText("Your system will stay awake.")
        elif agent_status is not None:
            self.description.setText(f"{agent_status} — staying awake automatically.")
        else:
            self.description.setText("Your system can sleep normally.")


class PowerControlsCard(Card):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=16, spacing=4)
        header = SectionHeader("Power controls", "sliders-horizontal")
        header.setContentsMargins(4, 2, 4, 6)
        self.body.addWidget(header)
        self.system = PowerToggleRow("cpu", "Keep system awake", "Prevents automatic sleep.")
        self.display = PowerToggleRow("monitor", "Keep display awake", "Keeps the screen on.")
        self.agents = PowerToggleRow("bot", "Keep awake for AI agents", agent_text.describe(None))
        self.body.addWidget(self.system)
        self.body.addWidget(self.display)
        self.body.addWidget(self.agents)


class TimeRemainingCard(Card):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=20, spacing=10)
        self.body.addWidget(SectionHeader("Time remaining", "timer"))
        self.value = label("—", "bigValue")
        self.body.addWidget(self.value)
        self.progress = ProgressBar()
        self.body.addWidget(self.progress)
        self.body.addSpacing(2)
        meta = QHBoxLayout()
        meta.setSpacing(24)
        self.started = self._meta(meta, "STARTED")
        self.duration = self._meta(meta, "DURATION")
        self.ends = self._meta(meta, "ENDS")
        meta.addStretch(1)
        self.body.addLayout(meta)

    @staticmethod
    def _meta(row: QHBoxLayout, caption: str):  # noqa: ANN205 - QLabel
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(caption, "section"))
        value = label("—", "metaValue")
        col.addWidget(value)
        row.addLayout(col)
        return value

    def show_session(self, session: Session | None, now: datetime) -> None:
        muted = session is None
        if self.value.property("muted") != muted:
            self.value.setProperty("muted", muted)
            repolish(self.value)
        if session is None:
            self.value.setText("No active session")
            self.progress.set_progress(0.0, animate=False)
            for widget in (self.started, self.duration, self.ends):
                widget.setText("—")
            return
        self.started.setText(format_time(session.started_at))
        if session.is_indefinite:
            self.value.setText("Indefinite")
            self.progress.set_indeterminate()
            self.duration.setText(f"Running {format_duration(session.elapsed_seconds(now))}")
            self.ends.setText("When stopped")
        else:
            self.value.setText(format_countdown(session.remaining_seconds(now) or 0))
            self.progress.set_progress(session.progress(now) or 0.0)
            self.duration.setText(format_planned(session.duration_seconds))
            self.ends.setText(format_time(session.ends_at))


class RecentSessionsCard(Card):
    view_all_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=16, spacing=4)
        header = SectionHeader("Recent sessions", "history")
        header.setContentsMargins(4, 2, 0, 4)
        view_all = QPushButton("View all  →")
        view_all.setProperty("variant", "link")
        view_all.setCursor(Qt.CursorShape.PointingHandCursor)
        view_all.clicked.connect(self.view_all_requested)
        header.add_right(view_all)
        self.body.addWidget(header)
        self._list = QVBoxLayout()
        self._list.setSpacing(0)
        self.body.addLayout(self._list)

    def set_sessions(self, sessions: list[Session]) -> None:
        while self._list.count():
            item = self._list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not sessions:
            empty = label("No sessions yet. Press the power button to start your first one.", "rowDescription")
            empty.setContentsMargins(8, 12, 8, 12)
            self._list.addWidget(empty)
            return
        for index, session in enumerate(sessions):
            if index:
                self._list.addWidget(Divider())
            self._list.addWidget(SessionCard(session))


class Dashboard(QWidget):
    view_all_requested = Signal()

    def __init__(
        self,
        state: ApplicationState,
        sessions_repo: SessionRepository,
        agents: AgentCoordinator | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("page", True)
        self._state = state
        self._repo = sessions_repo
        self._agents = agents

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(20)

        header, _ = page_header("Home", "Stay focused. Stay awake.")
        self.pill = StatusPill()
        header.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        self.hero = HeroCard()
        self.controls = PowerControlsCard()
        self.time = TimeRemainingCard()
        self.recent = RecentSessionsCard()
        grid.addWidget(self.hero, 0, 0, 2, 1)
        grid.addWidget(self.controls, 0, 1)
        grid.addWidget(self.time, 1, 1)
        grid.addWidget(self.recent, 2, 0, 1, 2)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 4)
        root.addLayout(grid, 1)

        # 1 Hz tick only while a session is active *and* this page is visible.
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._refresh_time)

        self.hero.button.clicked.connect(self._on_power_clicked)
        self.hero.durations.duration_selected.connect(state.select_duration)
        self.controls.system.toggled.connect(state.set_keep_system)
        self.controls.display.toggled.connect(state.set_keep_display)
        self.recent.view_all_requested.connect(self.view_all_requested)
        self.controls.agents.toggled.connect(lambda on: state.settings.set("agent_detection_enabled", on))
        state.settings.changed.connect(self._on_setting_changed)
        if agents is not None:
            agents.changed.connect(lambda _activity: self._sync_session())

        state.selection_changed.connect(self._sync_selection)
        state.sessions.session_started.connect(self._on_session_event)
        state.sessions.session_changed.connect(self._on_session_event)
        state.sessions.session_ended.connect(self._on_session_ended)

        self._sync_selection()
        self._sync_session(animate=False)
        self.reload_recent()

    # ------------------------------------------------------------------ slots

    def _on_power_clicked(self) -> None:
        # The button only flips once SessionManager confirms (via signals).
        self._state.toggle_session()

    def _on_session_event(self, _session: Session) -> None:
        self._sync_session()

    def _on_session_ended(self, _session: Session) -> None:
        self._sync_session()
        self.reload_recent()

    def _sync_selection(self) -> None:
        self.hero.durations.set_duration(self._state.selected_duration)
        self.controls.system.set_checked(self._state.keep_system)
        self.controls.display.set_checked(self._state.keep_display)
        self.controls.agents.set_checked(self._state.settings.get("agent_detection_enabled"))

    def _on_setting_changed(self, key: str, _value: object) -> None:
        if key == "agent_detection_enabled":
            self._sync_selection()
            self._sync_session()

    def _sync_session(self, animate: bool = True) -> None:
        session = self._state.active_session
        active = session is not None
        activity = self._agents.activity if self._agents is not None else None
        agent_status = agent_text.short_status(activity)
        self.hero.set_active(active, animate, agent_status)
        self.controls.agents.description.setText(agent_text.describe(activity))
        self.controls.agents.tile.set_active(agent_status is not None
                                             or self._state.settings.get("agent_detection_enabled"))
        if active:
            left = "running indefinitely" if session.is_indefinite else \
                f"{format_duration(session.remaining_seconds(self._state.sessions.now()))} left"
            self.pill.set_active(True, f"Active · {left}")
        elif agent_status is not None:
            self.pill.set_active(True, f"Auto · {agent_status}")
        else:
            self.pill.set_active(False, "Idle")
        self._refresh_time()
        self._update_tick()

    def _refresh_time(self) -> None:
        session = self._state.active_session
        now = self._state.sessions.now()
        self.time.show_session(session, now)
        self.hero.button.set_progress(session.progress(now) if session else None)
        if session is not None and self._tick.isActive():
            left = "running indefinitely" if session.is_indefinite else \
                f"{format_duration(session.remaining_seconds(now))} left"
            self.pill.text.setText(f"Active · {left}")

    def _update_tick(self) -> None:
        should_tick = self._state.active_session is not None and self.isVisible()
        if should_tick and not self._tick.isActive():
            self._tick.start()
        elif not should_tick and self._tick.isActive():
            self._tick.stop()

    def reload_recent(self) -> None:
        try:
            sessions = self._repo.recent(3)
        except DatabaseError as exc:
            log.error(kv("recent_sessions_load_failed", error=exc))
            sessions = []
        self.recent.set_sessions(sessions)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_time()
        self._update_tick()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._update_tick()
