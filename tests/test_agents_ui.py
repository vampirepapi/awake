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
    assert hero.description.text() == "AI mode: staying awake while Claude Code works."
    assert not hero.button.is_active()                     # manual session is off
    assert window.dashboard.pill.text.text() == "AI mode · Claude Code working"
    assert window.sidebar.status_text.text() == "On · AI mode"
    assert window.dashboard.controls.agents.description.text() == "Claude Code is working."


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
    assert row.description.text() == "2 sessions working."
    agents.update(snapshot("idle"))
    assert row.description.text() == "Finished — sleeping soon."
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
        assert tray.status_action.text() == "Awake is ON · AI mode · Claude Code working"
        assert tray.agents_action.isChecked()
        state.select_duration(1800)
        state.toggle_session()
        assert tray._menu_bar_label(state.active_session, now, agent_holding=True) == ("30m", False)  # manual wins
        tray.agents_action.trigger()
        assert state.settings.get("agent_detection_enabled") is False
    finally:
        tray.hide()


def test_ai_mode_replaces_duration_highlight(ui) -> None:
    """While an agent alone keeps the system awake, no duration looks 'running'."""
    window, state, agents, _ = ui
    hero = window.dashboard.hero
    buttons = hero.durations._buttons
    assert buttons[3600].isChecked() and not hero.ai_badge.isVisibleTo(hero)
    agents.update(snapshot("busy"))
    assert not any(b.isChecked() for b in buttons.values())
    assert hero.ai_badge.isVisibleTo(hero)
    buttons[7200].click()                                   # user picks a duration during AI mode
    assert buttons[7200].isChecked() and state.selected_duration == 7200
    agents.set_enabled(False)                               # AI mode ends → normal display
    assert not hero.ai_badge.isVisibleTo(hero) and buttons[7200].isChecked()


def test_manual_session_wins_over_ai_mode_display(ui) -> None:
    window, state, agents, _ = ui
    agents.update(snapshot("busy"))
    state.toggle_session()                                  # manual 1 h session while agent works
    hero = window.dashboard.hero
    assert hero.durations._buttons[3600].isChecked() and not hero.ai_badge.isVisibleTo(hero)
    assert hero.button.is_active()


def test_tray_menu_shows_ai_mode_instead_of_duration(ui) -> None:
    from awake.ui.tray import TrayController

    window, state, agents, _ = ui
    tray = TrayController(state, lambda page: None, lambda: None, agents)
    try:
        assert not tray.ai_mode_action.isVisible() and tray.duration_actions[3600].isChecked()
        agents.update(snapshot("busy"))
        assert tray.ai_mode_action.isVisible() and tray.ai_mode_action.isChecked()
        assert not any(a.isChecked() for a in tray.duration_actions.values())
        agents.set_enabled(False)
        tray.refresh()
        assert not tray.ai_mode_action.isVisible() and tray.duration_actions[3600].isChecked()
    finally:
        tray.hide()
