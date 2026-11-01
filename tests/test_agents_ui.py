"""UI wiring for AI-agent detection (offscreen)."""

from __future__ import annotations

import pytest

from awake.core.agents import AgentCoordinator, AgentKind, AgentSession, AgentSnapshot, AgentStatus, SourceState
from awake.core.application_state import ApplicationState
from awake.core.power_manager import PowerManager
from awake.core.session_manager import SessionManager
from awake.core.settings_manager import SettingsManager


def snapshot(*statuses: str, claude: SourceState = SourceState.ACTIVE, detail: str | None = None) -> AgentSnapshot:
    sessions = tuple(AgentSession(AgentKind.CLAUDE_CODE, f"s{i}", 200 + i, AgentStatus(st))
                     for i, st in enumerate(statuses))
    return AgentSnapshot(sessions, claude, detail)


@pytest.fixture
def ui(qapp, backend, session_repo, settings_repo, clock):
    from awake.ui.main_window import MainWindow

    power = PowerManager(backend)
    sessions = SessionManager(power, session_repo, clock=clock)
    state = ApplicationState(sessions, SettingsManager(settings_repo))
    agents = AgentCoordinator(power, lambda: None, clock=clock)
    window = MainWindow(state, session_repo, "fake", lambda: True, agents)
    window.apply_theme()
    yield window, state, agents, backend
    window.prepare_quit()
    window.close()


def test_agent_working_shows_awake_on_but_button_stays_manual(ui) -> None:
    window, state, agents, backend = ui
    agents.update(snapshot("busy"))
    hero = window.dashboard.hero
    assert backend.system
    assert hero.title.text() == "Awake is ON"
    assert "Claude Code working" in hero.description.text()
    assert not hero.button.is_active()                     # manual session is off
    assert window.dashboard.pill.text.text() == "Auto · Claude Code working"
    assert window.sidebar.status_text.text() == "On · Claude Code working"
    assert window.dashboard.controls.agents.description.text() == "Claude Code working now."


def test_stopping_manual_session_while_agent_works_still_reads_on(ui) -> None:
    window, state, agents, backend = ui
    state.toggle_session()
    agents.update(snapshot("busy"))
    state.toggle_session()                                  # user presses Stop
    assert backend.system                                   # agent still holds the system
    assert window.dashboard.hero.title.text() == "Awake is ON"
    assert not window.dashboard.hero.button.is_active()


def test_row_describes_each_state(ui, clock) -> None:
    window, _, agents, _ = ui
    row = window.dashboard.controls.agents
    agents.update(snapshot(claude=SourceState.NOT_RUNNING))
    assert row.description.text() == "Watching for Claude Code."
    agents.update(snapshot("idle"))
    assert row.description.text() == "No agent working right now."
    agents.update(snapshot("busy", "busy"))
    assert row.description.text() == "2 Claude Code sessions working now."
    agents.update(snapshot("idle"))
    assert row.description.text() == "Just finished — sleeping normally soon."
    clock.advance(61)
    agents._grace_timer.check()                             # grace over → released
    agents.update(snapshot(claude=SourceState.UNAVAILABLE, detail="Update Claude Code to enable this."))
    assert row.description.text() == "Update Claude Code to enable this."


def test_toggle_row_updates_setting_and_releases(ui) -> None:
    window, state, agents, backend = ui
    agents.update(snapshot("busy"))
    assert window.dashboard.controls.agents.is_checked()
    window.dashboard.controls.agents.switch.click()
    assert state.settings.get("agent_detection_enabled") is False
    agents.set_enabled(False)                               # app.py does this on the setting change
    assert not backend.system
    assert window.dashboard.hero.title.text() == "Awake is OFF"


def test_settings_page_has_agent_switches(ui) -> None:
    window, state, _, _ = ui
    window.settings_page._switches["agent_battery_guard"].click()
    assert state.settings.get("agent_battery_guard") is False
    assert "agent_detection_enabled" in window.settings_page._switches


def test_tray_shows_ai_label_only_when_agent_alone_holds(ui, monkeypatch) -> None:
    import awake.ui.tray as tray_module

    window, state, agents, _ = ui
    monkeypatch.setattr(tray_module, "IS_MAC", True)
    tray = tray_module.TrayController(state, lambda page: None, lambda: None, agents)
    tray._widener = type("W", (), {"apply": lambda self: True})()
    tray.tray.isVisible = lambda: True  # type: ignore[method-assign]
    try:
        now = state.sessions.now()
        agents.update(snapshot("busy"))
        assert tray._menu_bar_label(None, now, agent_holding=True) == ("AI", False)
        tray.refresh()
        assert tray.status_action.text() == "Awake is ON · Claude Code working"
        assert tray.agents_action.isChecked()
        state.select_duration(1800)
        state.toggle_session()
        assert tray._menu_bar_label(state.active_session, now, agent_holding=True) == ("30m", False)  # manual wins
        tray.agents_action.trigger()
        assert state.settings.get("agent_detection_enabled") is False
    finally:
        tray.hide()
