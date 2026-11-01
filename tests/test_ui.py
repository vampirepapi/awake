"""Offscreen UI tests: wiring between widgets, ApplicationState and SessionManager."""

from __future__ import annotations

import re

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from awake.core.application_state import ApplicationState
from awake.core.power_manager import PowerManager
from awake.core.session_manager import SessionManager
from awake.core.settings_manager import SettingsManager
from awake.ui.styles.awake_tokens import template_tokens
from awake.ui.styles.theme import DARK, LIGHT, DARK_THEME, LIGHT_THEME


@pytest.fixture
def ui(qapp, backend, session_repo, settings_repo, clock):
    from awake.ui.main_window import MainWindow

    sessions = SessionManager(PowerManager(backend), session_repo, clock=clock)
    state = ApplicationState(sessions, SettingsManager(settings_repo))
    errors: list[tuple[str, str]] = []
    state.error.connect(lambda t, m: errors.append((t, m)))
    window = MainWindow(state, session_repo, "fake", lambda: True)
    window.apply_theme()
    yield window, state, backend, errors
    window.prepare_quit()
    window.close()


def test_every_stylesheet_token_exists_in_both_themes() -> None:
    tokens = template_tokens()
    assert tokens, "template has no tokens?"
    for palette in (DARK, LIGHT):
        missing = tokens - set(palette) - {"chevron_url"}
        assert not missing, missing
    for theme in (DARK_THEME, LIGHT_THEME):
        assert not re.search(r"\{\{\w+\}\}", theme.stylesheet())


def test_power_button_starts_and_stops_session(ui) -> None:
    window, state, backend, _ = ui
    hero = window.dashboard.hero
    QTest.mouseClick(hero.button, Qt.MouseButton.LeftButton)
    assert state.active_session is not None and backend.system
    assert hero.title.text() == "Awake is ON"
    assert hero.button.is_active()
    QTest.mouseClick(hero.button, Qt.MouseButton.LeftButton)
    assert state.active_session is None and not backend.system
    assert hero.title.text() == "Awake is OFF"


def test_native_failure_never_shows_active(ui) -> None:
    window, state, backend, errors = ui
    backend.fail_on.add("prevent_system")
    QTest.mouseClick(window.dashboard.hero.button, Qt.MouseButton.LeftButton)
    assert state.active_session is None
    assert window.dashboard.hero.title.text() == "Awake is OFF"
    assert not window.dashboard.hero.button.is_active()
    assert errors and "could not" in errors[0][0].lower()


def test_duration_and_toggles_flow_into_session(ui) -> None:
    window, state, backend, _ = ui
    dash = window.dashboard
    dash.hero.durations._buttons[None].click()
    dash.controls.display.switch.click()
    assert state.selected_duration is None and state.keep_display
    state.toggle_session()
    session = state.active_session
    assert session.is_indefinite and session.display_awake and backend.display
    assert dash.time.value.text() == "Indefinite"


def test_display_toggle_during_session_is_live(ui) -> None:
    window, state, backend, _ = ui
    state.toggle_session()
    assert not backend.display
    window.dashboard.controls.display.switch.click()
    assert backend.display and state.active_session.display_awake


def test_recent_sessions_and_history_page_update(ui) -> None:
    window, state, _, _ = ui
    for _ in range(4):
        state.toggle_session()
        state.toggle_session()
    window.dashboard.reload_recent()
    cards = [window.dashboard.recent._list.itemAt(i).widget() for i in range(window.dashboard.recent._list.count())]
    assert sum(type(c).__name__ == "SessionCard" for c in cards) == 3
    window.sessions_page.reload()
    assert window.sessions_page.model.rowCount() == 4
    assert window.sessions_page.stack.currentIndex() == 0


def test_sessions_page_empty_state(ui) -> None:
    window, _, _, _ = ui
    window.sessions_page.reload()
    assert window.sessions_page.stack.currentIndex() == 1
    assert not window.sessions_page.clear_button.isEnabled()


def test_close_hides_to_tray_instead_of_quitting(ui) -> None:
    window, state, _, _ = ui
    window.show()
    assert state.settings.get("close_to_tray")
    window.close()
    assert not window.isVisible()


def test_tray_menu_reflects_state(ui) -> None:
    from awake.ui.tray import TrayController

    window, state, _, _ = ui
    tray = TrayController(state, lambda page: None, lambda: None)
    try:
        assert tray.toggle_action.text() == "Start Awake"
        tray.duration_actions[7200].trigger()
        assert state.selected_duration == 7200
        tray.toggle_action.trigger()
        assert state.active_session is not None
        assert tray.toggle_action.text() == "Stop Awake"
        assert tray.status_action.text().startswith("Awake is ON")
        tray.display_action.trigger()
        assert state.keep_display and state.active_session.display_awake
        tray.toggle_action.trigger()
        assert state.active_session is None and tray.status_action.text() == "Awake is OFF"
    finally:
        tray.hide()


def test_settings_page_persists_changes(ui) -> None:
    window, state, _, _ = ui
    window.settings_page._switches["notify_on_start"].click()
    assert state.settings.get("notify_on_start") is True
    combo = window.settings_page._combos["default_duration"]
    combo.setCurrentIndex(combo.findData(7200))
    combo.activated.emit(combo.currentIndex())
    assert state.settings.get("default_duration") == 7200
