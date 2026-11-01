"""Power management: coordinator logic and both native adapters (OS APIs mocked).

These tests verify *our* use of the native APIs (arguments, return-value
handling, idempotency). They do not prove the OS honours the request — see
tests/test_native_macos.py and docs/TESTING.md for real-hardware checks.
"""

from __future__ import annotations

import pytest

from awake.core.power_manager import PowerError, PowerManager
from awake.platforms.macos import (
    ASSERT_DISPLAY,
    ASSERT_SYSTEM,
    K_IOPM_ASSERTION_LEVEL_ON,
    MacPowerBackend,
)
from awake.platforms.windows import (
    ES_CONTINUOUS,
    ES_DISPLAY_REQUIRED,
    ES_SYSTEM_REQUIRED,
    WindowsPowerBackend,
    flags_for,
)
from tests.fakes import FakeBackend

# --------------------------------------------------------------------------- PowerManager


def test_acquire_system_only_does_not_touch_display(backend: FakeBackend) -> None:
    pm = PowerManager(backend)
    pm.acquire("session", system=True, display=False)
    assert backend.system and not backend.display
    assert backend.calls == ["prevent_system"]


def test_failed_activation_raises_and_holds_nothing(backend: FakeBackend) -> None:
    backend.fail_on.add("prevent_system")
    pm = PowerManager(backend)
    with pytest.raises(PowerError):
        pm.acquire("session", system=True, display=False)
    assert pm.owners == {}
    assert not backend.get_status().any_active


def test_partial_failure_rolls_back(backend: FakeBackend) -> None:
    backend.fail_on.add("prevent_display")
    pm = PowerManager(backend)
    with pytest.raises(PowerError):
        pm.acquire("session", system=True, display=True)
    assert backend.calls == ["prevent_system", "prevent_display", "allow_system"]
    assert not backend.system and not backend.display
    assert pm.owners == {}


def test_release_restores_normal_behaviour(backend: FakeBackend) -> None:
    pm = PowerManager(backend)
    pm.acquire("session", system=True, display=True)
    pm.release("session")
    assert not backend.system and not backend.display


def test_overlapping_owners_never_release_each_other(backend: FakeBackend) -> None:
    pm = PowerManager(backend)
    pm.acquire("session", system=True, display=False)
    pm.acquire("agent", system=True, display=True)
    assert backend.display
    pm.release("agent")
    assert backend.system and not backend.display  # session's need survives
    pm.release("session")
    assert not backend.system


def test_duplicate_acquire_makes_no_extra_native_calls(backend: FakeBackend) -> None:
    pm = PowerManager(backend)
    pm.acquire("session", system=True, display=False)
    pm.acquire("session", system=True, display=False)
    assert backend.calls == ["prevent_system"]


def test_display_toggle_changes_only_display(backend: FakeBackend) -> None:
    pm = PowerManager(backend)
    pm.acquire("session", system=True, display=False)
    pm.acquire("session", system=True, display=True)
    pm.acquire("session", system=True, display=False)
    assert backend.calls == ["prevent_system", "prevent_display", "allow_display"]


def test_failed_change_keeps_previous_request(backend: FakeBackend) -> None:
    pm = PowerManager(backend)
    pm.acquire("session", system=True, display=False)
    backend.fail_on.add("prevent_display")
    with pytest.raises(PowerError):
        pm.acquire("session", system=True, display=True)
    assert backend.system and not backend.display
    assert pm.owners["session"].display is False


def test_empty_request_rejected(backend: FakeBackend) -> None:
    with pytest.raises(ValueError):
        PowerManager(backend).acquire("session", system=False, display=False)


def test_release_all(backend: FakeBackend) -> None:
    pm = PowerManager(backend)
    pm.acquire("a", system=True, display=True)
    pm.release_all()
    assert pm.owners == {} and not backend.system and not backend.display


# --------------------------------------------------------------------------- macOS adapter


class FakeIOKit:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []
        self.released: list[int] = []
        self.next_id = 100
        self.create_result = 0
        self.release_result = 0

    def create_assertion(self, assertion_type: str, name: str) -> tuple[int, int]:
        self.created.append((assertion_type, name))
        if self.create_result != 0:
            return self.create_result, 0
        self.next_id += 1
        return 0, self.next_id

    def release_assertion(self, assertion_id: int) -> int:
        self.released.append(assertion_id)
        return self.release_result


def test_macos_uses_correct_assertion_types() -> None:
    libs = FakeIOKit()
    backend = MacPowerBackend(libs=libs)
    backend.prevent_system_sleep()
    assert libs.created == [(ASSERT_SYSTEM, "Awake")]
    backend.prevent_display_sleep()
    assert libs.created[-1] == (ASSERT_DISPLAY, "Awake")
    assert ASSERT_SYSTEM == "PreventUserIdleSystemSleep"
    assert ASSERT_DISPLAY == "PreventUserIdleDisplaySleep"
    assert K_IOPM_ASSERTION_LEVEL_ON == 255


def test_macos_no_duplicate_assertions() -> None:
    libs = FakeIOKit()
    backend = MacPowerBackend(libs=libs)
    backend.prevent_system_sleep()
    backend.prevent_system_sleep()
    assert len(libs.created) == 1


def test_macos_failure_raises_and_holds_nothing() -> None:
    libs = FakeIOKit()
    libs.create_result = 0xE00002C2 - (1 << 32)  # kIOReturnBadArgument as signed int32
    backend = MacPowerBackend(libs=libs)
    with pytest.raises(PowerError):
        backend.prevent_system_sleep()
    assert not backend.get_status().system_awake


def test_macos_release_uses_assertion_id() -> None:
    libs = FakeIOKit()
    backend = MacPowerBackend(libs=libs)
    backend.prevent_system_sleep()
    held_id = libs.next_id
    backend.allow_system_sleep()
    backend.allow_system_sleep()  # idempotent
    assert libs.released == [held_id]
    assert not backend.get_status().system_awake


def test_macos_release_failure_is_reported() -> None:
    libs = FakeIOKit()
    backend = MacPowerBackend(libs=libs)
    backend.prevent_system_sleep()
    libs.release_result = -1
    with pytest.raises(PowerError):
        backend.allow_system_sleep()


def test_macos_release_all_and_status() -> None:
    libs = FakeIOKit()
    backend = MacPowerBackend(libs=libs)
    backend.prevent_system_sleep()
    backend.prevent_display_sleep()
    status = backend.get_status()
    assert status.system_awake and status.display_awake
    backend.release_all()
    assert len(libs.released) == 2
    assert not backend.get_status().any_active


# --------------------------------------------------------------------------- Windows adapter


class FakeKernel32:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.return_value = ES_CONTINUOUS  # any non-zero previous state = success
        self.thread_ids: set[int] = set()

    def SetThreadExecutionState(self, flags: int) -> int:  # noqa: N802 - Win32 name
        import threading

        self.thread_ids.add(threading.get_ident())
        self.calls.append(flags)
        return self.return_value


def test_windows_flag_values() -> None:
    assert ES_CONTINUOUS == 0x80000000
    assert ES_SYSTEM_REQUIRED == 0x00000001
    assert ES_DISPLAY_REQUIRED == 0x00000002
    assert flags_for(True, False) == ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    assert flags_for(True, True) == ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    assert flags_for(False, False) == ES_CONTINUOUS


def test_windows_system_then_display_then_release() -> None:
    k32 = FakeKernel32()
    backend = WindowsPowerBackend(kernel32=k32)
    try:
        backend.prevent_system_sleep()
        backend.prevent_display_sleep()
        backend.allow_display_sleep()
        backend.allow_system_sleep()
        assert k32.calls == [
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED,
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED,
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED,
            ES_CONTINUOUS,
        ]
    finally:
        backend.shutdown()


def test_windows_calls_happen_on_one_dedicated_thread() -> None:
    import threading

    k32 = FakeKernel32()
    backend = WindowsPowerBackend(kernel32=k32)
    try:
        backend.prevent_system_sleep()
        backend.allow_system_sleep()
        assert len(k32.thread_ids) == 1
        assert threading.get_ident() not in k32.thread_ids
    finally:
        backend.shutdown()


def test_windows_null_return_is_failure() -> None:
    k32 = FakeKernel32()
    k32.return_value = 0
    backend = WindowsPowerBackend(kernel32=k32)
    try:
        with pytest.raises(PowerError):
            backend.prevent_system_sleep()
        assert not backend.get_status().system_awake
    finally:
        backend.shutdown()


def test_windows_no_duplicate_calls() -> None:
    k32 = FakeKernel32()
    backend = WindowsPowerBackend(kernel32=k32)
    try:
        backend.prevent_system_sleep()
        backend.prevent_system_sleep()
        assert len(k32.calls) == 1
    finally:
        backend.shutdown()


def test_windows_shutdown_releases_and_stops_thread() -> None:
    k32 = FakeKernel32()
    backend = WindowsPowerBackend(kernel32=k32)
    backend.prevent_display_sleep()
    backend.shutdown()
    assert k32.calls[-1] == ES_CONTINUOUS
    with pytest.raises(PowerError):
        backend.prevent_system_sleep()


def test_windows_display_implies_system_in_status() -> None:
    k32 = FakeKernel32()
    backend = WindowsPowerBackend(kernel32=k32)
    try:
        backend.prevent_display_sleep()
        assert k32.calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED]
        status = backend.get_status()
        assert status.system_awake and status.display_awake
    finally:
        backend.shutdown()
