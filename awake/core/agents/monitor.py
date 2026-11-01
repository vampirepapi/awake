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
import os
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
from awake.core.agents.codex import CodexSource
from awake.core.agents.cursor import CursorHooks, CursorSource
from awake.core.agents.discovery import ProcessLister, list_processes
from awake.core.agents.models import AgentKind, AgentSession, AgentSnapshot, SourceState, SourceStatus
from awake.logging_setup import kv

log = logging.getLogger("awake.agents")

POLL_INTERVAL_MS = 20_000
FAILURE_BACKOFF_MS = 5 * 60_000     # the CLI can't do it (old version, unexpected output)
TRANSIENT_BACKOFF_MS = 60_000       # it didn't answer in time / couldn't start — try again soon
COMMAND_TIMEOUT_MS = 10_000

RunCallback = Callable[[int | None, str], None]  # (exit code or None on failure/timeout, stdout)


class CommandRunner(Protocol):
    def run(self, argv: list[str], env: dict[str, str], timeout_ms: int, done: RunCallback) -> None: ...


class QProcessRunner(QObject):
    """Runs one command at a time without blocking the event loop.

    A single long-lived QProcess and timer are reused for every call and wired once
    to bound methods. Creating a QProcess per call with closures that reference it
    (and deleteLater-ing it) creates Python/Qt reference cycles whose collection can
    free Qt objects at the wrong moment — that crashed in testing.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._done: RunCallback | None = None
        self._timeout_ms = 0

    @property
    def busy(self) -> bool:
        return self._done is not None

    def run(self, argv: list[str], env: dict[str, str], timeout_ms: int, done: RunCallback) -> None:
        if self._done is not None:
            return  # previous query still running; the next poll will try again
        self._done = done
        self._timeout_ms = timeout_ms
        environment = QProcessEnvironment.systemEnvironment()
        for key, value in env.items():
            environment.insert(key, value)
        self._process.setProcessEnvironment(environment)
        self._timer.start(timeout_ms)
        self._process.start(argv[0], argv[1:])

    def _complete(self, code: int | None) -> None:
        done, self._done = self._done, None
        if done is None:
            return
        self._timer.stop()
        out = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._process.readAllStandardError()  # discard; may contain update notices
        done(code, out)

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        self._complete(code if status == QProcess.ExitStatus.NormalExit else None)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._complete(None)

    def _on_timeout(self) -> None:
        log.warning(kv("agent_query_timeout", timeout_ms=self._timeout_ms))
        self._process.kill()  # `finished` (CrashExit) follows and completes the call


class AgentMonitor(QObject):
    snapshot_ready = Signal(object)  # AgentSnapshot

    def __init__(
        self,
        runner: CommandRunner | None = None,
        lister: ProcessLister = list_processes,
        interval_ms: int = POLL_INTERVAL_MS,
        path_exists: Callable[[str], bool] = os.path.exists,
        codex: CodexSource | None = None,
        cursor: CursorSource | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._path_exists = path_exists
        self._codex = codex if codex is not None else CodexSource()
        self._cursor = cursor if cursor is not None else CursorSource(CursorHooks())
        # Results of the file-based sources from the current pass, merged with Claude's.
        self._others: tuple[list[AgentSession], list[SourceStatus]] = ([], [])
        self._claude_status = SourceStatus(AgentKind.CLAUDE_CODE, SourceState.NOT_RUNNING)
        self._runner = runner if runner is not None else QProcessRunner(self)
        self._lister = lister
        self._interval_ms = interval_ms
        self._backoff_until = 0.0
        self._generation = 0  # results from a previous start()/stop() cycle are ignored
        self._live_pids: set[int] = set()
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
        self._generation += 1
        self._backoff_until = 0.0
        self._others = ([], [])
        self._claude_status = SourceStatus(AgentKind.CLAUDE_CODE, SourceState.NOT_RUNNING)
        self._emit(AgentSnapshot())

    def poll(self) -> None:
        try:
            processes = self._lister()
        except Exception as exc:  # noqa: BLE001 - detection must never take the app down
            log.error(kv("agent_scan_failed", error=exc))
            processes = []
        self._others = self._read_file_sources(processes)
        claude = find_claude_processes(processes)
        if not claude:
            self._backoff_until = 0.0  # a fresh start may be a newer, working version
            self._emit_claude([], SourceStatus(AgentKind.CLAUDE_CODE, SourceState.NOT_RUNNING))
            return
        if time.monotonic() < self._backoff_until:
            # Keep Claude's last (unavailable) status; still report the other agents.
            self._emit_claude([], self._claude_status)
            return
        argv = agents_command(claude, exists=self._path_exists)
        if argv is None:
            self._fail("Can't locate Claude Code.")
            return
        self._live_pids = {p.pid for p in claude}
        generation = self._generation
        self._runner.run(argv, QUIET_ENV, COMMAND_TIMEOUT_MS,
                         lambda code, out: self._on_result(code, out, generation))

    def _on_result(self, code: int | None, stdout: str, generation: int | None = None) -> None:
        if generation is not None and generation != self._generation:
            return  # stopped (or restarted) while the query was running
        if code is None:
            self._fail("Claude Code didn't respond.", transient=True)
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
        # Belt and braces: only trust "busy" for a process we can see running right now.
        live = [s for s in sessions if s.pid in self._live_pids]
        self._emit_claude(live, SourceStatus(AgentKind.CLAUDE_CODE, SourceState.ACTIVE))

    def _fail(self, reason: str, transient: bool = False) -> None:
        if self._last.claude is not SourceState.UNAVAILABLE:
            log.warning(kv("agent_source_unavailable", agent="claude-code", reason=reason))
        backoff_ms = TRANSIENT_BACKOFF_MS if transient else FAILURE_BACKOFF_MS
        self._backoff_until = time.monotonic() + backoff_ms / 1000
        self._emit_claude([], SourceStatus(AgentKind.CLAUDE_CODE, SourceState.UNAVAILABLE, reason))

    def _read_file_sources(self, processes: list) -> tuple[list[AgentSession], list[SourceStatus]]:  # noqa: ANN001
        sessions: list[AgentSession] = []
        statuses: list[SourceStatus] = []
        for kind, source in ((AgentKind.CODEX, self._codex), (AgentKind.CURSOR, self._cursor)):
            try:
                found, status = source.read(processes)
            except Exception as exc:  # noqa: BLE001 - one broken source must not stop the others
                log.error(kv("agent_source_failed", agent=kind.value, error=type(exc).__name__))
                found, status = [], SourceStatus(kind, SourceState.UNAVAILABLE, "Status couldn't be read.")
            sessions.extend(found)
            statuses.append(status)
        return sessions, statuses

    def _emit_claude(self, claude_sessions: list[AgentSession], claude_status: SourceStatus) -> None:
        self._claude_status = claude_status
        other_sessions, other_statuses = self._others
        self._emit(AgentSnapshot(tuple(claude_sessions) + tuple(other_sessions),
                                 (claude_status, *other_statuses)))

    def _emit(self, snapshot: AgentSnapshot) -> None:
        snapshot = AgentSnapshot(snapshot.sessions, snapshot.sources, time.time())
        if (snapshot.sessions, snapshot.sources) != (self._last.sessions, self._last.sources):
            states = {s.agent.value: s.state.value for s in snapshot.sources}
            busy = {k.value: sum(1 for s in snapshot.busy_sessions if s.agent is k) for k in AgentKind}
            log.info(kv("agent_snapshot", sessions=len(snapshot.sessions),
                        busy=sum(busy.values()), states=",".join(f"{k}:{v}" for k, v in states.items()),
                        busy_by_agent=",".join(f"{k}:{v}" for k, v in busy.items() if v)))
        self._last = snapshot
        self.snapshot_ready.emit(snapshot)
