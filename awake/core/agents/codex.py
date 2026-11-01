"""OpenAI Codex detection — no setup required.

Codex persists every session to ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl``
(``CODEX_HOME`` defaults to ``~/.codex``). Turn lifecycle events are always persisted
(codex-rs/rollout/src/policy.rs), as lines like::

    {"timestamp":"…","type":"event_msg","payload":{"type":"task_started",…},"ordinal":3}

with ``task_started`` (alias ``turn_started``) at the start of a turn and
``task_complete`` (alias ``turn_complete``) or ``turn_aborted`` at the end
(codex-rs/protocol/src/protocol.rs).

A session counts as busy only when all three hold:
1. a Codex process is running,
2. its rollout file was written within ``ACTIVE_WINDOW`` (guards against a crash
   that left a turn open), and
3. the last lifecycle event in the file is a start.

Privacy: these files contain the user's conversation. Only lines that contain a
lifecycle marker are parsed, and only their ``type`` fields are used; nothing
from the files is logged, stored or shown.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from awake.core.agents.discovery import ProcessInfo
from awake.core.agents.models import AgentKind, AgentSession, AgentStatus, SourceState, SourceStatus
from awake.logging_setup import kv

log = logging.getLogger("awake.agents")

ACTIVE_WINDOW_SECONDS = 30 * 60
CHUNK_BYTES = 256 * 1024
MAX_SCAN_BYTES = 1024 * 1024  # a single command-output line can be up to 64 KiB (and more before truncation)

_STARTED = {"task_started", "turn_started"}
_ENDED = {"task_complete", "turn_complete", "turn_aborted"}
_MARKERS = tuple(f'"{name}"' for name in (*_STARTED, *_ENDED))
_SESSION_ID_RE = re.compile(
    r"rollout-.*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:_[^.]*)?\.jsonl$"
)


def codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    return Path(override) if override else Path.home() / ".codex"


def _basename(path: str) -> str:
    return os.path.basename(path.replace("\\", "/")).lower()


def is_codex_process(proc: ProcessInfo) -> bool:
    """Native ``codex`` binary (Homebrew, standalone, npm's vendored child) or the npm launcher."""
    argv = proc.cmdline
    if not argv:
        return False
    if _basename(argv[0]) in {"codex", "codex.exe"} or _basename(proc.exe or "") in {"codex", "codex.exe"}:
        return True
    if _basename(argv[0]) in {"node", "node.exe"}:
        return any("@openai/codex/" in arg.replace("\\", "/") for arg in argv[1:3])
    return False


def last_turn_event(path: Path) -> str | None:
    """``"started"``, ``"ended"`` or ``None`` (no lifecycle event in the scanned tail)."""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        end = size
        carry = b""
        scanned = 0
        while end > 0 and scanned < MAX_SCAN_BYTES:
            start = max(0, end - CHUNK_BYTES)
            fh.seek(start)
            block = fh.read(end - start) + carry
            scanned += end - start
            lines = block.split(b"\n")
            # The first piece may be the tail of a line that continues in the previous chunk.
            carry = lines.pop(0) if start > 0 else b""
            for raw in reversed(lines):
                event = _lifecycle(raw)
                if event is not None:
                    return event
            end = start
    return None


def _lifecycle(raw: bytes) -> str | None:
    if not raw:
        return None
    line = raw.decode("utf-8", errors="replace")
    if not any(marker in line for marker in _MARKERS):
        return None
    try:
        item = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(item, dict) or item.get("type") != "event_msg":
        return None
    payload = item.get("payload")
    kind = payload.get("type") if isinstance(payload, dict) else None
    if kind in _STARTED:
        return "started"
    if kind in _ENDED:
        return "ended"
    return None  # the marker text appeared inside some other content


@dataclass
class _CacheEntry:
    mtime_ns: int
    size: int
    event: str | None


class CodexSource:
    """Reads Codex's turn state from its session files. Cheap enough to call every poll."""

    def __init__(self, home: Path | None = None, clock=time.time) -> None:  # noqa: ANN001
        self._home = home
        self._clock = clock
        self._cache: dict[Path, _CacheEntry] = {}

    @property
    def sessions_dir(self) -> Path:
        return (self._home or codex_home()) / "sessions"

    def recent_rollouts(self) -> list[Path]:
        """Rollout files written within the active window. Only today's/yesterday's folders are listed."""
        root = self.sessions_dir
        now = self._clock()
        folders = {root}
        for moment in (datetime.fromtimestamp(now), datetime.fromtimestamp(now, timezone.utc)):
            for days in (0, 1):
                day = moment - timedelta(days=days)
                folders.add(root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}")
        recent: list[Path] = []
        for folder in folders:
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                if not (entry.name.startswith("rollout-") and entry.name.endswith(".jsonl")):
                    continue
                try:
                    if now - entry.stat().st_mtime <= ACTIVE_WINDOW_SECONDS:
                        recent.append(Path(entry.path))
                except OSError:
                    continue
        return recent

    def read(self, processes: list[ProcessInfo]) -> tuple[list[AgentSession], SourceStatus]:
        if not any(is_codex_process(p) for p in processes):
            self._cache.clear()
            return [], SourceStatus(AgentKind.CODEX, SourceState.NOT_RUNNING)
        sessions: list[AgentSession] = []
        unreadable = False
        live: set[Path] = set()
        for path in self.recent_rollouts():
            live.add(path)
            try:
                stat = path.stat()
                cached = self._cache.get(path)
                if cached and (cached.mtime_ns, cached.size) == (stat.st_mtime_ns, stat.st_size):
                    event = cached.event
                else:
                    event = last_turn_event(path)
                    self._cache[path] = _CacheEntry(stat.st_mtime_ns, stat.st_size, event)
            except OSError as exc:
                log.warning(kv("codex_rollout_unreadable", error=type(exc).__name__))
                unreadable = True
                continue
            if event is None and stat.st_size > MAX_SCAN_BYTES:
                unreadable = True  # a huge tail with no lifecycle event: unknown, not idle
                continue
            match = _SESSION_ID_RE.search(path.name)
            session_id = match.group(1) if match else path.stem
            status = AgentStatus.BUSY if event == "started" else AgentStatus.IDLE
            sessions.append(AgentSession(AgentKind.CODEX, session_id, 0, status))
        for stale in set(self._cache) - live:
            del self._cache[stale]
        if unreadable and not any(s.is_busy for s in sessions):
            return sessions, SourceStatus(AgentKind.CODEX, SourceState.UNAVAILABLE,
                                          "Couldn't read Codex's session status.")
        return sessions, SourceStatus(AgentKind.CODEX, SourceState.ACTIVE)
