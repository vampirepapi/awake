"""Real SetThreadExecutionState tests — run only on Windows with ``pytest -m native``.

Confirms the native call succeeds and, when ``powercfg /requests`` is available
(it needs an elevated shell, which GitHub-hosted runners have), that Windows
lists the request under SYSTEM / DISPLAY and drops it after release.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.native,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
]


def _power_requests() -> dict[str, str] | None:
    try:
        out = subprocess.run(["powercfg", "/requests"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None  # not elevated
    headers = {"DISPLAY:", "SYSTEM:", "AWAYMODE:", "EXECUTION:", "PERFBOOST:", "ACTIVELOCKSCREEN:"}
    sections: dict[str, str] = {}
    current: str | None = None
    for line in out.stdout.splitlines():
        if line.strip() in headers:
            current = line.strip().rstrip(":")
            sections[current] = ""
        elif current:
            sections[current] += line.lower() + "\n"
    return sections


def test_system_request_visible_and_released() -> None:
    from awake.platforms.windows import WindowsPowerBackend

    backend = WindowsPowerBackend()
    try:
        backend.prevent_system_sleep()
        assert backend.get_status().system_awake
        requests = _power_requests()
        if requests is not None:
            assert "python" in requests.get("SYSTEM", ""), requests
            assert "python" not in requests.get("DISPLAY", ""), requests
    finally:
        backend.shutdown()
    requests = _power_requests()
    if requests is not None:
        assert "python" not in requests.get("SYSTEM", ""), requests


def test_display_request_visible_and_released() -> None:
    from awake.platforms.windows import WindowsPowerBackend

    backend = WindowsPowerBackend()
    try:
        backend.prevent_system_sleep()
        backend.prevent_display_sleep()
        requests = _power_requests()
        if requests is not None:
            assert "python" in requests.get("DISPLAY", ""), requests
        backend.allow_display_sleep()
        requests = _power_requests()
        if requests is not None:
            assert "python" not in requests.get("DISPLAY", ""), requests
            assert "python" in requests.get("SYSTEM", ""), requests
    finally:
        backend.shutdown()
