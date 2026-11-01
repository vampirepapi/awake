"""Autostart integrations (filesystem / registry mocked) and backend selection."""

from __future__ import annotations

import plistlib
import sys

import pytest

from awake.core.power_manager import PowerError
from awake.platforms import AUTOSTART_FLAG, UnsupportedPowerBackend, autostart_command
from awake.platforms.macos import MacAutostart
from awake.platforms.windows import WindowsAutostart


def test_mac_launch_agent_round_trip(tmp_path) -> None:
    autostart = MacAutostart(agents_dir=tmp_path)
    assert not autostart.is_enabled()
    autostart.set_enabled(True, ["/Applications/Awake.app/Contents/MacOS/Awake", AUTOSTART_FLAG])
    assert autostart.is_enabled()
    with autostart.plist_path.open("rb") as fh:
        payload = plistlib.load(fh)
    assert payload["RunAtLoad"] is True
    assert payload["ProgramArguments"][-1] == AUTOSTART_FLAG
    assert payload["Label"] == autostart.label
    autostart.set_enabled(False, [])
    assert not autostart.is_enabled()
    autostart.set_enabled(False, [])  # idempotent


class FakeKey:
    def __init__(self, store: dict) -> None:
        self.store = store

    def __enter__(self) -> "FakeKey":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.opened: list[str] = []

    def OpenKey(self, root, path, reserved, access):  # noqa: N802, ANN001
        assert root is self.HKEY_CURRENT_USER  # never HKLM — no admin rights needed
        self.opened.append(path)
        return FakeKey(self.values)

    def QueryValueEx(self, key, name):  # noqa: N802, ANN001
        if name not in key.store:
            raise FileNotFoundError(name)
        return key.store[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802, ANN001
        key.store[name] = value

    def DeleteValue(self, key, name):  # noqa: N802, ANN001
        if name not in key.store:
            raise FileNotFoundError(name)
        del key.store[name]


def test_windows_run_key_round_trip() -> None:
    reg = FakeWinreg()
    autostart = WindowsAutostart(winreg_module=reg)
    assert not autostart.is_enabled()
    autostart.set_enabled(True, [r"C:\Program Files\Awake\Awake.exe", AUTOSTART_FLAG])
    assert autostart.is_enabled()
    assert reg.values["Awake"] == rf'"C:\Program Files\Awake\Awake.exe" "{AUTOSTART_FLAG}"'
    assert reg.opened[-1] == r"Software\Microsoft\Windows\CurrentVersion\Run"
    autostart.set_enabled(False, [])
    assert not autostart.is_enabled()
    autostart.set_enabled(False, [])  # deleting a missing value is fine


def test_autostart_command_has_flag() -> None:
    command = autostart_command()
    assert command[-1] == AUTOSTART_FLAG
    assert command[0] == sys.executable or command[0].endswith(("python.exe", "pythonw.exe"))


def test_unsupported_backend_fails_loudly() -> None:
    backend = UnsupportedPowerBackend()
    with pytest.raises(PowerError):
        backend.prevent_system_sleep()
    with pytest.raises(PowerError):
        backend.prevent_display_sleep()
    backend.release_all()
    assert not backend.get_status().any_active
