"""OS-specific integrations.

Everything that differs between macOS and Windows lives in this package; the
rest of the application talks to it through the factories below.

(The package is named ``platforms`` rather than ``platform`` because a
top-level ``platform`` package would shadow Python's standard-library module.)
"""

from __future__ import annotations

import logging
import sys
from typing import Protocol

from awake.core.power_manager import PowerBackend, PowerError
from awake.database.models import PowerState
from awake.logging_setup import kv

log = logging.getLogger("awake.platform")

AUTOSTART_FLAG = "--autostart"


class Autostart(Protocol):
    def is_enabled(self) -> bool: ...
    def set_enabled(self, enabled: bool, program_arguments: list[str]) -> None: ...


class UnsupportedPowerBackend(PowerBackend):
    """Used on platforms without an adapter: every request fails loudly."""

    name = "unsupported"

    def prevent_system_sleep(self) -> None:
        raise PowerError(f"Keeping the system awake is not supported on {sys.platform}.")

    def prevent_display_sleep(self) -> None:
        raise PowerError(f"Keeping the display awake is not supported on {sys.platform}.")

    def allow_system_sleep(self) -> None:
        return None

    def allow_display_sleep(self) -> None:
        return None

    def release_all(self) -> None:
        return None

    def get_status(self) -> PowerState:
        return PowerState(backend=self.name)


def create_power_backend() -> PowerBackend:
    try:
        if sys.platform == "darwin":
            from awake.platforms.macos import MacPowerBackend

            return MacPowerBackend()
        if sys.platform == "win32":
            from awake.platforms.windows import WindowsPowerBackend

            return WindowsPowerBackend()
    except OSError as exc:
        log.error(kv("power_backend_load_failed", platform=sys.platform, error=exc))
    return UnsupportedPowerBackend()


def create_autostart() -> Autostart | None:
    if sys.platform == "darwin":
        from awake.platforms.macos import MacAutostart

        return MacAutostart()
    if sys.platform == "win32":
        from awake.platforms.windows import WindowsAutostart

        return WindowsAutostart()
    return None


def autostart_command() -> list[str]:
    if sys.platform == "win32":
        from awake.platforms.windows import launch_command
    else:
        from awake.platforms.macos import launch_command
    return launch_command([AUTOSTART_FLAG])
