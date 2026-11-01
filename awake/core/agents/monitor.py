"""Periodic agent detection, without blocking the UI thread.

Each pass: list processes (~10 ms) → if Claude Code is running, run
``claude agents --json`` asynchronously (QProcess, ~70 ms of CPU in the child)
→ emit an :class:`AgentSnapshot`.

Nothing is run while no agent process exists, and a hard failure (old Claude
version without ``agents``, bad output) backs off to every few minutes instead
of retrying every pass.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from awake.core.agents.claude import (
    QUIET_ENV,
    ClaudeOutputError,
    agents_command,
    find_claude_processes,
    parse_agents_json,
)
from awake.core.agents.discovery import ProcessLister, list_processes
from awake.core.agents.models import AgentSnapshot, SourceState
from awake.logging_setup import kv

log = logging.getLogger("awake.agents")

POLL_INTERVAL_MS = 20_000
FAILURE_BACKOFF_MS = 5 * 60_000
COMMAND_TIMEOUT_MS = 10_000

RunCallback = Callable[[int | None, str], None]  # (exit code or None on failure/timeout, stdout)


class CommandRunner(Protocol):
    def run(self, argv: list[str], env: dict[str, str], timeout_ms: int, done: RunCallback) -> None: ...


class QProcessRunner(QObject):
    """Runs one command at a time with QProcess; never blocks the event loop."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False

    def run(self, argv: list[str], env: dict[str, str], timeout_ms: int, done: RunCallback) -> None:
        if self._busy:
            return
        self._busy = True
        process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        for key, value in env.items():
            environment.insert(key, value)
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        timer = QTimer(process)
        timer.setSingleShot(True)
        finished = {"done": False}

        def finish(code: int | None) -> None:
            if finished["done"]:
                return
            finished["done"] = True
            timer.stop()
            out = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
            self._busy = False
            process.deleteLater()
            done(code, out)

        def on_finished(code: int, status: QProcess.ExitStatus) -> None:
            finish(code if status == QProcess.ExitStatus.NormalExit else None)

        def on_error(error: QProcess.ProcessError) -> None:
            if error == QProcess.ProcessError.FailedToStart:
                finish(None)

        def on_timeout() -> None:
            log.warning(kv("agent_query_timeout", timeout_ms=timeout_ms))
            process.kill()
            finish(None)

        process.finished.connect(on_finished)
        process.errorOccurred.connect(on_error)
        timer.timeout.connect(on_timeout)
        timer.start(timeout_ms)
        process.start(argv[0], argv[1:])


class AgentMonitor(QObject):
    snapshot_ready = Signal(object)  # AgentSnapshot

    def __init__(
        self,
        runner: CommandRunner | None = None,
        lister: ProcessLister = list_processes,
        interval_ms: int = POLL_INTERVAL_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner if runner is not None else QProcessRunner(self)
        self._lister = lister
        self._interval_ms = interval_ms
        self._backoff_until = 0.0
        self._last: AgentSnapshot = AgentSnapshot()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)

    @property
    def last_snapshot(self) -> AgentSnapshot:
        return self._last

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(self._interval_ms)
            log.info(kv("agent_monitor_started", interval_s=self._interval_ms // 1000))
        self.poll()

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            log.info(kv("agent_monitor_stopped"))
        self._emit(AgentSnapshot())

    def poll(self) -> None:
        try:
            claude = find_claude_processes(self._lister())
        except Exception as exc:  # noqa: BLE001 - detection must never take the app down
            log.error(kv("agent_scan_failed", error=exc))
            claude = []
        if not claude:
            self._backoff_until = 0.0  # a fresh start may be a newer, working version
            self._emit(AgentSnapshot(claude=SourceState.NOT_RUNNING))
            return
        if time.monotonic() < self._backoff_until:
            return  # keep the last (unavailable) snapshot until the back-off ends
        argv = agents_command(claude)
        if argv is None:
            self._fail("Can't locate Claude Code.")
            return
        self._runner.run(argv, QUIET_ENV, COMMAND_TIMEOUT_MS, self._on_result)

    def _on_result(self, code: int | None, stdout: str) -> None:
        if code is None:
            self._fail("Claude Code didn't respond.")
            return
        if code != 0:
            self._fail("Update Claude Code to enable this.")
            return
        try:
            sessions = parse_agents_json(stdout)
        except ClaudeOutputError as exc:
            log.warning(kv("agent_query_bad_output", error=exc))
            self._fail("Unexpected Claude Code status.")
            return
        self._emit(AgentSnapshot(tuple(sessions), SourceState.ACTIVE))

    def _fail(self, reason: str) -> None:
        if self._last.claude is not SourceState.UNAVAILABLE:
            log.warning(kv("agent_source_unavailable", agent="claude-code", reason=reason))
        self._backoff_until = time.monotonic() + FAILURE_BACKOFF_MS / 1000
        self._emit(AgentSnapshot(claude=SourceState.UNAVAILABLE, claude_detail=reason))

    def _emit(self, snapshot: AgentSnapshot) -> None:
        snapshot = AgentSnapshot(snapshot.sessions, snapshot.claude, snapshot.claude_detail, time.time())
        changed = (snapshot.sessions, snapshot.claude) != (self._last.sessions, self._last.claude)
        if changed:
            busy = len(snapshot.busy_sessions)
            log.info(kv("agent_snapshot", claude=snapshot.claude.value, sessions=len(snapshot.sessions), busy=busy))
        self._last = snapshot
        self.snapshot_ready.emit(snapshot)
