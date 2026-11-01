"""Real IOKit tests — run only on macOS with ``pytest -m native``.

They create genuine power assertions and confirm with ``pmset -g assertions``
that macOS sees them (and that they disappear on release).
"""

from __future__ import annotations

import os
import sys

import pytest

pytestmark = [
    pytest.mark.native,
    pytest.mark.skipif(sys.platform != "darwin", reason="macOS only"),
]


def _awake_assertions() -> str:
    from awake.platforms.macos import assertions_report

    pid = str(os.getpid())
    lines = [line for line in assertions_report().splitlines() if f"pid {pid}(" in line]
    return "\n".join(lines)


def test_system_only_assertion_is_visible_to_pmset() -> None:
    from awake.platforms.macos import MacPowerBackend

    backend = MacPowerBackend(assertion_name="Awake (pytest)")
    try:
        backend.prevent_system_sleep()
        mine = _awake_assertions()
        assert "PreventUserIdleSystemSleep" in mine
        assert "PreventUserIdleDisplaySleep" not in mine
    finally:
        backend.release_all()
    assert _awake_assertions() == ""


def test_display_assertion_is_visible_and_released() -> None:
    from awake.platforms.macos import MacPowerBackend

    backend = MacPowerBackend(assertion_name="Awake (pytest)")
    try:
        backend.prevent_system_sleep()
        backend.prevent_display_sleep()
        assert "PreventUserIdleDisplaySleep" in _awake_assertions()
        backend.allow_display_sleep()
        mine = _awake_assertions()
        assert "PreventUserIdleDisplaySleep" not in mine
        assert "PreventUserIdleSystemSleep" in mine
    finally:
        backend.release_all()
    assert _awake_assertions() == ""
