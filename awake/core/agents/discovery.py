"""Cheap, permission-safe process listing (psutil).

One pass over all processes costs roughly 7–20 ms. Processes we may not inspect
(other users, protected system processes) are skipped silently — agents run as
the current user, so nothing relevant is lost.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

import psutil

from awake.logging_setup import kv

log = logging.getLogger("awake.agents")


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    pid: int
    ppid: int
    cmdline: tuple[str, ...]
    exe: str | None = None
    create_time: float = 0.0  # process start, seconds since the epoch


ProcessLister = Callable[[], list[ProcessInfo]]


def list_processes() -> list[ProcessInfo]:
    """All inspectable processes except this app and its children."""
    own = os.getpid()
    result: list[ProcessInfo] = []
    try:
        iterator = psutil.process_iter(["pid", "ppid", "cmdline", "create_time"])
    except psutil.Error as exc:  # pragma: no cover - defensive
        log.warning(kv("process_scan_failed", error=exc))
        return result
    for proc in iterator:
        info = proc.info
        cmdline = info.get("cmdline")
        if not cmdline or info.get("pid") == own or info.get("ppid") == own:
            continue
        exe: str | None = None
        first = os.path.basename(str(cmdline[0]).replace("\\", "/")).lower()
        # Only resolve exe() for plausible candidates — it is the expensive call.
        if first in {"claude", "claude.exe", "node", "node.exe", "bun", "bun.exe"}:
            try:
                exe = proc.exe() or None
            except (psutil.Error, OSError):
                exe = None
        result.append(ProcessInfo(int(info["pid"]), int(info.get("ppid") or 0), tuple(map(str, cmdline)), exe,
                                  float(info.get("create_time") or 0.0)))
    return result


def battery_state() -> tuple[float, bool] | None:
    """``(percent, plugged_in)`` or ``None`` on machines without a battery."""
    try:
        battery = psutil.sensors_battery()
    except (psutil.Error, OSError, NotImplementedError) as exc:
        log.warning(kv("battery_read_failed", error=exc))
        return None
    if battery is None:
        return None
    return float(battery.percent), bool(battery.power_plugged)
