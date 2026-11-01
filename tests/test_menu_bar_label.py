"""Countdown text next to the macOS menu-bar icon."""

from __future__ import annotations

import pytest

from awake.core.application_state import ApplicationState
from awake.core.power_manager import PowerManager
from awake.core.session_manager import SessionManager
from awake.core.settings_manager import SettingsManager
from awake.ui.formatting import menu_bar_label, seconds_until_label_changes


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [
        (7200, "2h 00m"),
        (7199, "2h 00m"),   # rounds up: never shows less time than is left
        (7140, "1h 59m"),
        (3600, "1h 00m"),
        (3599, "1h 00m"),
        (3540, "59m"),
        (61, "2m"),
        (60, "1m"),
        (1, "1m"),          # never "0m" while running
        (0, "1m"),
    ],
)
def test_label(remaining: float, expected: str) -> None:
    assert menu_bar_label(remaining) == expected


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [(3600, 60), (3599, 59), (3541, 1), (3540, 60), (61, 1), (60, 60), (30, 30), (0, 0)],
)
def test_seconds_until_label_changes(remaining: float, expected: float) -> None:
    assert seconds_until_label_changes(remaining) == pytest.approx(expected)
    # After waiting that long the label really is different (until the session ends).
    if remaining > 60:
        assert menu_bar_label(remaining - expected) != menu_bar_label(remaining)


def test_labelled_icon_is_wide_and_plain_icon_is_square(qapp) -> None:
    from awake.ui.icons import tray_icon

    plain = tray_icon(True, mask=True)
    timed = tray_icon(True, mask=True, label="1h 59m")
    forever = tray_icon(True, mask=True, infinite=True)
    for size in plain.availableSizes():
        assert size.width() == size.height()
    for icon in (timed, forever):
        heights = {s.height() for s in icon.availableSizes()}
        assert {18, 36, 40} <= heights                      # fits 22/24 pt bars at 1x and 2x
        assert all(s.width() > s.height() * 1.5 for s in icon.availableSizes())
        assert icon.isMask()                                  # macOS tints it for light/dark bars
    wide = max(timed.availableSizes(), key=lambda s: s.height())
    narrow = max(forever.availableSizes(), key=lambda s: s.height())
    assert wide.width() > narrow.width()


class FakeWidener:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.calls = 0

    def apply(self) -> bool:
        self.calls += 1
        return self.ok


@pytest.fixture
def tray(qapp, backend, session_repo, settings_repo, clock, monkeypatch):
    import awake.ui.tray as tray_module

    monkeypatch.setattr(tray_module, "IS_MAC", True)
    sessions = SessionManager(PowerManager(backend), session_repo, clock=clock)
    state = ApplicationState(sessions, SettingsManager(settings_repo))
    controller = tray_module.TrayController(state, lambda page: None, lambda: None)
    controller._widener = FakeWidener(True)  # offscreen tests have no real menu bar
    yield controller, state, clock
    controller.hide()


def test_tray_label_follows_session(tray) -> None:
    controller, state, clock = tray
    controller.tray.isVisible = lambda: True  # type: ignore[method-assign]
    assert controller._menu_bar_label(None, clock.now) == (None, False)

    state.select_duration(3600)
    state.toggle_session()
    session = state.active_session
    assert controller._menu_bar_label(session, clock.now) == ("1h 00m", False)
    assert controller._label_timer.isActive()
    assert controller._label_timer.interval() == 60_050   # next minute boundary + 50 ms

    clock.advance(25 * 60 + 10)
    assert controller._menu_bar_label(session, clock.now) == ("35m", False)

    state.select_duration(None)
    assert controller._menu_bar_label(state.active_session, clock.now) == (None, True)
    assert not controller._label_timer.isActive()          # nothing to tick for ∞

    state.toggle_session()
    controller.refresh()
    assert not controller._label_timer.isActive()


def test_tray_label_can_be_turned_off(tray) -> None:
    controller, state, clock = tray
    state.toggle_session()
    state.settings.set("show_menu_bar_timer", False)
    assert controller._menu_bar_label(state.active_session, clock.now) == (None, False)
    assert not controller._label_timer.isActive()


def test_no_label_on_windows(tray, monkeypatch) -> None:
    import awake.ui.tray as tray_module

    controller, state, clock = tray
    state.toggle_session()
    monkeypatch.setattr(tray_module, "IS_MAC", False)
    assert controller._menu_bar_label(state.active_session, clock.now) == (None, False)


def test_falls_back_to_plain_icon_when_item_cannot_be_widened(tray) -> None:
    """A wide image in Qt's square slot would be shrunk to ~1/3 — show the plain sun instead."""
    controller, state, clock = tray
    controller.tray.isVisible = lambda: True  # type: ignore[method-assign]
    controller._widener = FakeWidener(False)
    state.toggle_session()
    assert controller._menu_bar_label(state.active_session, clock.now) == (None, False)
    assert controller._widener.calls >= 1
