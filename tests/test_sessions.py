from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from awake.core.power_manager import PowerError, PowerManager
from awake.core.session_manager import (
    POWER_OWNER,
    SessionAlreadyActiveError,
    SessionError,
    SessionManager,
)
from awake.core.timer_manager import TimerManager
from awake.database import EndReason, SessionRepository, SessionStatus
from tests.fakes import FakeBackend, FakeClock


@pytest.fixture
def manager(qapp, backend: FakeBackend, session_repo: SessionRepository, clock: FakeClock) -> SessionManager:
    return SessionManager(PowerManager(backend), session_repo, clock=clock)


def test_start_session_acquires_power_and_persists(manager, backend, session_repo) -> None:
    started = []
    manager.session_started.connect(started.append)
    session = manager.start(3600)
    assert manager.is_active and backend.system and not backend.display
    assert session_repo.get(session.id).status is SessionStatus.ACTIVE
    assert started == [session]


def test_default_power_options_keep_display_sleep_allowed(manager, backend) -> None:
    session = manager.start(1800)
    assert session.system_awake and not session.display_awake
    assert not backend.display


def test_stop_session_releases_and_records(manager, backend, session_repo, clock) -> None:
    session = manager.start(3600)
    clock.advance(120)
    ended = manager.stop()
    assert not manager.is_active and not backend.system
    stored = session_repo.get(session.id)
    assert stored.status is SessionStatus.STOPPED
    assert stored.end_reason is EndReason.USER_STOPPED
    assert stored.elapsed_seconds() == 120
    assert ended == stored


def test_timer_expiration_completes_session(manager, backend, session_repo, clock) -> None:
    session = manager.start(1800)
    clock.advance(1799)
    manager._timer.check()
    assert manager.is_active
    clock.advance(1)
    manager._timer.check()
    assert not manager.is_active and not backend.system
    stored = session_repo.get(session.id)
    assert stored.status is SessionStatus.COMPLETED
    assert stored.end_reason is EndReason.EXPIRED


def test_indefinite_session_never_expires(manager, clock) -> None:
    session = manager.start(None)
    assert session.is_indefinite and session.ends_at is None
    clock.advance(10 * 24 * 3600)
    manager._timer.check()
    assert manager.is_active
    assert not manager._timer.is_running


def test_second_start_is_rejected(manager, backend) -> None:
    manager.start(3600)
    with pytest.raises(SessionAlreadyActiveError):
        manager.start(1800)
    assert backend.calls == ["prevent_system"]


def test_toggle_starts_then_stops(manager) -> None:
    assert manager.toggle(3600, system_awake=True, display_awake=False) is not None
    assert manager.toggle(3600, system_awake=True, display_awake=False) is None
    assert not manager.is_active


@pytest.mark.parametrize("bad", [0, -1, 60, 1800.0, "3600", True])
def test_invalid_durations_rejected(manager, backend, bad) -> None:
    with pytest.raises(SessionError):
        manager.start(bad)
    assert backend.calls == [] and not manager.is_active


def test_both_options_off_rejected(manager) -> None:
    with pytest.raises(SessionError):
        manager.start(3600, system_awake=False, display_awake=False)


def test_power_failure_means_no_session(manager, backend, session_repo) -> None:
    backend.fail_on.add("prevent_system")
    with pytest.raises(PowerError):
        manager.start(3600)
    assert not manager.is_active
    assert session_repo.count() == 0


def test_change_display_during_session(manager, backend, session_repo) -> None:
    session = manager.start(3600)
    manager.set_power_options(system_awake=True, display_awake=True)
    assert backend.display
    assert session_repo.get(session.id).display_awake
    manager.set_power_options(system_awake=True, display_awake=False)
    assert backend.system and not backend.display


def test_turning_everything_off_ends_session(manager, backend) -> None:
    manager.start(3600)
    manager.set_power_options(system_awake=False, display_awake=False)
    assert not manager.is_active and not backend.system


def test_set_duration_extends_from_now(manager, clock) -> None:
    session = manager.start(1800)
    clock.advance(600)
    updated = manager.set_duration(3600)
    assert updated.ends_at == clock.now + timedelta(seconds=3600)
    assert updated.started_at == session.started_at
    manager.set_duration(None)
    assert manager.active_session.is_indefinite


def test_stop_does_not_release_other_owners(qapp, backend, session_repo, clock) -> None:
    power = PowerManager(backend)
    power.acquire("agent-monitor", system=True, display=False)
    manager = SessionManager(power, session_repo, clock=clock)
    manager.start(3600)
    manager.stop()
    assert backend.system  # still held for the other owner
    assert POWER_OWNER not in power.owners


def test_shutdown_stops_session_and_releases_everything(manager, backend, session_repo) -> None:
    session = manager.start(None)
    manager.shutdown()
    assert not backend.system and "release_all" in backend.calls
    stored = session_repo.get(session.id)
    assert stored.status is SessionStatus.STOPPED and stored.end_reason is EndReason.APP_QUIT
    manager.shutdown()  # idempotent
    with pytest.raises(SessionError):
        manager.start(3600)


def test_timer_manager_fires_with_real_event_loop(qapp) -> None:
    timer = TimerManager()
    fired = []
    loop = QEventLoop()
    timer.expired.connect(lambda: (fired.append(True), loop.quit()))
    timer.schedule(datetime.now(timezone.utc) + timedelta(milliseconds=80))
    QTimer.singleShot(2000, loop.quit)  # safety net
    loop.exec()
    assert fired == [True] and not timer.is_running


def test_timer_manager_rearms_in_chunks(qapp, clock) -> None:
    timer = TimerManager(clock=clock, max_interval_ms=60_000)
    timer.schedule(clock.now + timedelta(hours=2))
    assert timer._timer.isActive()
    assert timer._timer.interval() == 60_000
    # Simulate waking from sleep past the deadline: the next check expires immediately.
    fired = []
    timer.expired.connect(lambda: fired.append(True))
    clock.advance(3 * 3600)
    timer.check()
    assert fired == [True]
