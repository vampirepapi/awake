"""Real SetThreadExecutionState tests — run only on Windows with ``pytest -m native``.

``test_native_calls_succeed`` always runs on Windows. The ``powercfg`` tests
confirm Windows actually lists (and later drops) the request; ``powercfg
/requests`` needs an elevated shell, so without admin rights they are reported
as SKIPPED rather than silently passing. GitHub-hosted runners are elevated.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.native,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
]

_HEADERS = {"DISPLAY:", "SYSTEM:", "AWAYMODE:", "EXECUTION:", "PERFBOOST:", "ACTIVELOCKSCREEN:"}


def _power_requests() -> dict[str, str]:
    """Parse ``powercfg /requests`` into {section: lowercase body}, or skip the test."""
    try:
        out = subprocess.run(["powercfg", "/requests"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"powercfg unavailable: {exc}")
    if out.returncode != 0:
        pytest.skip("powercfg /requests needs an elevated (administrator) shell")
    sections: dict[str, str] = {}
    current: str | None = None
    for line in out.stdout.splitlines():
        if line.strip() in _HEADERS:
            current = line.strip().rstrip(":")
            sections[current] = ""
        elif current:
            sections[current] += line.lower() + "\n"
    return sections


def _ours(section: str, requests: dict[str, str]) -> bool:
    exe = sys.executable.lower().rsplit("\\", 1)[-1]  # python.exe
    return exe in requests.get(section, "")


def test_native_calls_succeed() -> None:
    from awake.platforms.windows import WindowsPowerBackend

    backend = WindowsPowerBackend()
    try:
        backend.prevent_system_sleep()
        backend.prevent_display_sleep()
        status = backend.get_status()
        assert status.system_awake and status.display_awake
        backend.allow_display_sleep()
        assert not backend.get_status().display_awake
    finally:
        backend.shutdown()
    assert not backend.get_status().any_active


def test_system_request_listed_by_powercfg_and_released() -> None:
    from awake.platforms.windows import WindowsPowerBackend

    backend = WindowsPowerBackend()
    try:
        backend.prevent_system_sleep()
        requests = _power_requests()
        print("powercfg while held:", {k: v.strip() for k, v in requests.items()})
        assert _ours("SYSTEM", requests), requests
        assert not _ours("DISPLAY", requests), requests
    finally:
        backend.shutdown()
    assert not _ours("SYSTEM", _power_requests())


def test_display_request_listed_by_powercfg_and_released() -> None:
    from awake.platforms.windows import WindowsPowerBackend

    backend = WindowsPowerBackend()
    try:
        backend.prevent_system_sleep()
        backend.prevent_display_sleep()
        assert _ours("DISPLAY", _power_requests())
        backend.allow_display_sleep()
        requests = _power_requests()
        assert not _ours("DISPLAY", requests), requests
        assert _ours("SYSTEM", requests), requests
    finally:
        backend.shutdown()
