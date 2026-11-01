"""Agent detection: parsing, process matching, monitor and coordinator rules (no real processes)."""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QCoreApplication

from awake.core.agents.claude import (
    QUIET_ENV,
    ClaudeOutputError,
    agents_command,
    find_claude_processes,
    match_claude_process,
    parse_agents_json,
)
from awake.core.agents.coordinator import POWER_OWNER, AgentCoordinator, AgentHoldState
from awake.core.agents.discovery import ProcessInfo
from awake.core.agents.models import (
    AgentKind,
    AgentSession,
    AgentSnapshot,
    AgentStatus,
    SourceState,
    SourceStatus,
    claude_snapshot,
)
from awake.core.agents.monitor import AgentMonitor
from awake.core.power_manager import PowerManager
from tests.fakes import FakeBackend, FakeClock

CLAUDE_BIN = "/Users/me/.local/share/claude/versions/2.1.316"


# --------------------------------------------------------------------------- parsing


def test_parse_real_output_shape() -> None:
    text = json.dumps([
        {"pid": 1, "cwd": "/a", "kind": "interactive", "startedAt": 1, "sessionId": "s1", "name": "x", "status": "busy"},
        {"pid": 2, "cwd": "/b", "kind": "interactive", "startedAt": 2, "sessionId": "s2", "status": "idle"},
        {"pid": 3, "cwd": "/c", "kind": "interactive", "startedAt": 3, "sessionId": "s3", "status": "waiting",
         "waitingFor": "permission prompt"},
    ])
    sessions = parse_agents_json(text)
    assert [(s.pid, s.status) for s in sessions] == [
        (1, AgentStatus.BUSY), (2, AgentStatus.IDLE), (3, AgentStatus.WAITING)]
    assert [s.is_busy for s in sessions] == [True, False, False]  # waiting on the user is not work
    assert all(s.agent is AgentKind.CLAUDE_CODE for s in sessions)


def test_parse_ignores_sessions_without_live_process_and_unknown_status() -> None:
    text = json.dumps([
        {"kind": "background", "id": "bg1", "state": "working", "cwd": "/x", "startedAt": 1},  # process exited
        {"pid": 9, "kind": "background", "id": "bg2", "state": "working", "status": "busy", "cwd": "/y", "startedAt": 1},
        {"pid": 10, "status": "thinking-hard", "cwd": "/z", "kind": "interactive", "startedAt": 1},
        "garbage",
    ])
    sessions = parse_agents_json(text)
    assert [(s.session_id, s.status) for s in sessions] == [("bg2", AgentStatus.BUSY)]


def test_parse_empty_and_errors() -> None:
    assert parse_agents_json("[]") == []
    with pytest.raises(ClaudeOutputError):
        parse_agents_json("Usage: claude [options]")
    with pytest.raises(ClaudeOutputError):
        parse_agents_json('{"not": "a list"}')


def test_parsed_sessions_hold_no_paths_or_names() -> None:
    session = parse_agents_json(json.dumps([{"pid": 1, "status": "busy", "cwd": "/secret/project",
                                             "name": "client-x", "sessionId": "s"}]))[0]
    assert "/secret" not in repr(session) and "client-x" not in repr(session)


# --------------------------------------------------------------------------- process matching


def proc(pid: int, *argv: str, exe: str | None = None) -> ProcessInfo:
    return ProcessInfo(pid, 1, tuple(argv), exe)


def test_matches_native_binary_by_cmdline_not_name() -> None:
    match = match_claude_process(proc(10, "claude", "--resume", exe=CLAUDE_BIN))
    assert match is not None and match.command == (CLAUDE_BIN,)


def test_matches_windows_exe() -> None:
    exe = r"C:\Users\me\.local\bin\claude.exe"
    match = match_claude_process(proc(11, exe, exe=exe))
    assert match is not None and match.command == (exe,)


def test_matches_npm_node_install() -> None:
    script = "/usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js"
    match = match_claude_process(proc(12, "node", script, "--continue", exe="/usr/local/bin/node"))
    assert match is not None and match.command == ("/usr/local/bin/node", script)


@pytest.mark.parametrize("argv", [
    ("claude", "agents", "--json"),          # a status query, not a session
    ("node", "/app/server.js"),              # unrelated node process
    ("/usr/bin/python3", "claude.py"),       # name collision in an argument
    ("zsh",),
    (),
])
def test_ignores_non_sessions(argv: tuple[str, ...]) -> None:
    assert match_claude_process(proc(13, *argv, exe="/x/" + (argv[0] if argv else "none"))) is None


def test_agents_command_requires_absolute_path() -> None:
    relative = find_claude_processes([proc(1, "claude")])          # exe unknown, argv[0] relative
    assert agents_command(relative) is None
    absolute = find_claude_processes([proc(1, "claude", exe=CLAUDE_BIN)])
    assert agents_command(absolute, exists=lambda _p: True) == [CLAUDE_BIN, "agents", "--json"]
    assert agents_command(absolute, exists=lambda _p: False) is None   # pruned by the updater


def test_quiet_env_disables_nonessential_traffic() -> None:
    assert QUIET_ENV["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


# --------------------------------------------------------------------------- monitor


class NoSource:
    """Stand-in for a file-based source (Codex/Cursor) that sees nothing running."""

    def __init__(self, agent: AgentKind, sessions=(), state=SourceState.NOT_RUNNING) -> None:  # noqa: ANN001
        self.agent, self.sessions, self.state = agent, list(sessions), state

    def read(self, processes):  # noqa: ANN001, ANN201
        return list(self.sessions), SourceStatus(self.agent, self.state)


class FakeRunner:
    def __init__(self, code: int | None = 0, stdout: str = "[]") -> None:
        self.code, self.stdout = code, stdout
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def run(self, argv, env, timeout_ms, done) -> None:  # noqa: ANN001
        self.calls.append((argv, env))
        done(self.code, self.stdout)


def make_monitor(qapp, runner: FakeRunner, processes: list[ProcessInfo]) -> tuple[AgentMonitor, list[AgentSnapshot]]:
    monitor = AgentMonitor(runner=runner, lister=lambda: processes, path_exists=lambda _p: True,
                           codex=NoSource(AgentKind.CODEX), cursor=NoSource(AgentKind.CURSOR))
    seen: list[AgentSnapshot] = []
    monitor.snapshot_ready.connect(seen.append)
    return monitor, seen


def test_monitor_does_nothing_without_claude(qapp) -> None:
    runner = FakeRunner()
    monitor, seen = make_monitor(qapp, runner, [proc(1, "zsh")])
    monitor.poll()
    assert runner.calls == [] and seen[-1].claude is SourceState.NOT_RUNNING


def test_monitor_reports_busy_session(qapp) -> None:
    runner = FakeRunner(stdout=json.dumps([{"pid": 5, "status": "busy", "sessionId": "a"}]))
    monitor, seen = make_monitor(qapp, runner, [proc(5, "claude", exe=CLAUDE_BIN)])
    monitor.poll()
    argv, env = runner.calls[0]
    assert argv == [CLAUDE_BIN, "agents", "--json"] and env == QUIET_ENV
    assert seen[-1].claude is SourceState.ACTIVE and seen[-1].any_busy


@pytest.mark.parametrize(("code", "stdout"), [(1, "error: unknown command 'agents'"), (None, ""), (0, "not json")])
def test_monitor_failure_marks_unavailable_and_backs_off(qapp, code, stdout) -> None:
    runner = FakeRunner(code=code, stdout=stdout)
    monitor, seen = make_monitor(qapp, runner, [proc(5, "claude", exe=CLAUDE_BIN)])
    monitor.poll()
    assert seen[-1].claude is SourceState.UNAVAILABLE and seen[-1].claude_detail
    monitor.poll()
    assert len(runner.calls) == 1  # backing off: no second query


# --------------------------------------------------------------------------- coordinator


def busy_snapshot(*statuses: str) -> AgentSnapshot:
    sessions = tuple(AgentSession(AgentKind.CLAUDE_CODE, f"s{i}", 100 + i, AgentStatus(st))
                     for i, st in enumerate(statuses))
    return claude_snapshot(sessions)


class Battery:
    def __init__(self, state: tuple[float, bool] | None = None) -> None:
        self.state = state

    def __call__(self) -> tuple[float, bool] | None:
        return self.state


@pytest.fixture
def coord(qapp, backend: FakeBackend, clock: FakeClock):
    power = PowerManager(backend)
    battery = Battery()
    coordinator = AgentCoordinator(power, battery, clock=clock, grace_seconds=60, cap_seconds=4 * 3600)
    return coordinator, power, backend, clock, battery


def test_busy_acquires_system_only(coord) -> None:
    coordinator, power, backend, _, _ = coord
    coordinator.update(busy_snapshot("busy", "idle"))
    assert coordinator.activity.state is AgentHoldState.HOLDING
    assert backend.system and not backend.display
    assert power.owners[POWER_OWNER].system and not power.owners[POWER_OWNER].display


def test_idle_releases_after_grace(coord) -> None:
    coordinator, _, backend, clock, _ = coord
    coordinator.update(busy_snapshot("busy"))
    coordinator.update(busy_snapshot("idle"))
    assert coordinator.activity.state is AgentHoldState.GRACE and backend.system
    clock.advance(59)
    coordinator._grace_timer.check()
    assert backend.system
    clock.advance(1)
    coordinator._grace_timer.check()
    assert coordinator.activity.state is AgentHoldState.IDLE and not backend.system


def test_busy_again_within_grace_keeps_holding(coord) -> None:
    coordinator, _, backend, clock, _ = coord
    coordinator.update(busy_snapshot("busy"))
    coordinator.update(busy_snapshot("waiting"))
    clock.advance(30)
    coordinator.update(busy_snapshot("busy"))
    clock.advance(60)
    coordinator._grace_timer.check()
    assert coordinator.activity.state is AgentHoldState.HOLDING and backend.system
    assert backend.calls.count("prevent_system") == 1  # no release/re-acquire flapping


def test_cap_releases_until_agents_go_idle(coord) -> None:
    coordinator, _, backend, clock, _ = coord
    coordinator.update(busy_snapshot("busy"))
    clock.advance(4 * 3600)
    coordinator._cap_timer.check()
    assert coordinator.activity.state is AgentHoldState.CAPPED and not backend.system
    coordinator.update(busy_snapshot("busy"))            # still busy → stays released
    assert not backend.system
    coordinator.update(busy_snapshot("idle"))            # idle once → cap resets
    coordinator.update(busy_snapshot("busy"))
    assert coordinator.activity.state is AgentHoldState.HOLDING and backend.system


def test_battery_guard(coord) -> None:
    coordinator, _, backend, _, battery = coord
    battery.state = (15.0, False)
    coordinator.update(busy_snapshot("busy"))
    assert coordinator.activity.state is AgentHoldState.LOW_BATTERY and not backend.system
    battery.state = (15.0, True)                         # plugged in
    coordinator.update(busy_snapshot("busy"))
    assert backend.system
    battery.state = (12.0, False)                        # unplugged while holding → release
    coordinator.update(busy_snapshot("busy"))
    assert not backend.system
    coordinator.set_battery_guard(False)
    assert backend.system


def test_no_battery_means_no_guard(coord) -> None:
    coordinator, _, backend, _, battery = coord
    battery.state = None  # desktop
    coordinator.update(busy_snapshot("busy"))
    assert backend.system


def test_disable_releases_immediately(coord) -> None:
    coordinator, _, backend, _, _ = coord
    coordinator.update(busy_snapshot("busy"))
    coordinator.set_enabled(False)
    assert coordinator.activity.state is AgentHoldState.DISABLED and not backend.system
    coordinator.update(busy_snapshot("busy"))
    assert not backend.system


def test_power_error_reported_once_then_retried(coord) -> None:
    coordinator, _, backend, _, _ = coord
    errors: list[str] = []
    coordinator.error_occurred.connect(errors.append)
    backend.fail_on.add("prevent_system")
    coordinator.update(busy_snapshot("busy"))
    coordinator.update(busy_snapshot("busy"))
    QCoreApplication.processEvents()  # the error is delivered on the next event-loop turn
    assert coordinator.activity.state is AgentHoldState.ERROR and len(errors) == 1
    backend.fail_on.clear()
    coordinator.update(busy_snapshot("busy"))
    assert coordinator.activity.state is AgentHoldState.HOLDING and backend.system


def test_agents_and_manual_session_never_release_each_other(qapp, backend, session_repo, clock) -> None:
    from awake.core.session_manager import SessionManager

    power = PowerManager(backend)
    sessions = SessionManager(power, session_repo, clock=clock)
    coordinator = AgentCoordinator(power, Battery(), clock=clock)
    sessions.start(3600)
    coordinator.update(busy_snapshot("busy"))
    sessions.stop()
    assert backend.system                                # agent still working
    sessions.start(3600)
    coordinator.update(busy_snapshot("idle"))
    clock.advance(61)
    coordinator._grace_timer.check()
    assert backend.system                                # manual session still running
    sessions.stop()
    assert not backend.system


def test_shutdown_releases_and_ignores_later_updates(coord) -> None:
    coordinator, power, backend, _, _ = coord
    coordinator.update(busy_snapshot("busy"))
    coordinator.shutdown()
    coordinator.update(busy_snapshot("busy"))
    assert not backend.system and POWER_OWNER not in power.owners


# --------------------------------------------------------------------------- review regressions


@pytest.mark.parametrize("argv,exe", [
    (("/Applications/Claude.app/Contents/MacOS/Claude",), "/Applications/Claude.app/Contents/MacOS/Claude"),
    ((r"C:\Users\me\AppData\Local\AnthropicClaude\app-1.0\claude.exe",),
     r"C:\Users\me\AppData\Local\AnthropicClaude\app-1.0\claude.exe"),
    (("claude", "--chrome-native-host"), CLAUDE_BIN),      # browser-extension bridge
    (("claude", "mcp", "serve"), CLAUDE_BIN),              # MCP server, not a session
])
def test_desktop_app_and_helpers_are_not_sessions(argv, exe) -> None:
    assert match_claude_process(proc(20, *argv, exe=exe)) is None


def test_newest_installed_version_is_queried() -> None:
    old = "/Users/me/.local/share/claude/versions/2.1.306"
    new = "/Users/me/.local/share/claude/versions/2.1.316"
    procs = find_claude_processes([proc(5, "claude", exe=old), proc(90, "claude", exe=new), proc(7, "claude", exe=old)])
    assert agents_command(procs, exists=lambda _p: True)[0] == new
    # newest binary pruned → fall back to one that still exists
    assert agents_command(procs, exists=lambda p: p == old)[0] == old


def test_cap_still_applies_after_idle_and_busy_again_within_grace(coord) -> None:
    coordinator, _, backend, clock, _ = coord
    coordinator.update(busy_snapshot("busy"))
    for _ in range(12):                                  # 12 turns x 20.5 min = 246 min > 4 h cap; idle < grace each time
        clock.advance(20 * 60)
        coordinator.update(busy_snapshot("idle"))
        clock.advance(30)
        coordinator.update(busy_snapshot("busy"))
    coordinator._cap_timer.check()
    assert coordinator.activity.state is AgentHoldState.CAPPED and not backend.system


def test_unknown_status_keeps_the_current_decision(coord) -> None:
    coordinator, _, backend, clock, _ = coord
    unavailable = claude_snapshot((), SourceState.UNAVAILABLE, "Claude Code didn't respond.")
    coordinator.update(busy_snapshot("busy"))
    coordinator.update(unavailable)                      # a timeout is not "idle"
    clock.advance(120)
    coordinator._grace_timer.check()
    assert backend.system and coordinator.activity.state is AgentHoldState.HOLDING
    assert coordinator.activity.snapshot.claude_detail == "Claude Code didn't respond."


def test_unknown_status_does_not_reset_the_cap(coord) -> None:
    coordinator, _, backend, clock, _ = coord
    coordinator.update(busy_snapshot("busy"))
    clock.advance(4 * 3600)
    coordinator._cap_timer.check()
    coordinator.update(claude_snapshot((), SourceState.UNAVAILABLE, "x"))
    coordinator.update(busy_snapshot("busy"))
    assert coordinator.activity.state is AgentHoldState.CAPPED and not backend.system


def test_battery_replug_while_busy_reacquires(coord) -> None:
    coordinator, _, backend, _, battery = coord
    battery.state = (50.0, False)
    coordinator.update(busy_snapshot("busy"))
    battery.state = (10.0, False)
    coordinator.update(busy_snapshot("busy"))
    assert not backend.system
    battery.state = (10.0, True)
    coordinator.update(busy_snapshot("busy"))
    assert backend.system and coordinator.activity.state is AgentHoldState.HOLDING


def test_changed_only_emitted_when_something_changes(coord) -> None:
    coordinator, _, _, _, _ = coord
    seen = []
    coordinator.changed.connect(seen.append)
    for _ in range(5):
        coordinator.update(busy_snapshot("busy"))
    assert len(seen) == 1


def test_claude_exiting_during_backoff_clears_it(qapp) -> None:
    runner = FakeRunner(code=1, stdout="")
    processes = [proc(5, "claude", exe=CLAUDE_BIN)]
    monitor, seen = make_monitor(qapp, runner, processes)
    monitor.poll()                                        # fails → back-off
    processes.clear()
    monitor.poll()                                        # Claude gone → back-off cleared
    assert seen[-1].claude is SourceState.NOT_RUNNING
    processes.append(proc(6, "claude", exe=CLAUDE_BIN))
    runner.code, runner.stdout = 0, "[]"
    monitor.poll()
    assert len(runner.calls) == 2 and seen[-1].claude is SourceState.ACTIVE


def test_result_after_stop_is_ignored(qapp) -> None:
    pending = []

    class SlowRunner:
        def run(self, argv, env, timeout_ms, done) -> None:  # noqa: ANN001
            pending.append(done)

    monitor = AgentMonitor(runner=SlowRunner(), lister=lambda: [proc(5, "claude", exe=CLAUDE_BIN)],
                           path_exists=lambda _p: True, codex=NoSource(AgentKind.CODEX),
                           cursor=NoSource(AgentKind.CURSOR))
    seen = []
    monitor.snapshot_ready.connect(seen.append)
    monitor.poll()
    monitor.stop()
    pending[0](0, json.dumps([{"pid": 5, "status": "busy", "sessionId": "a"}]))
    assert not seen[-1].any_busy                          # stale busy result dropped


def test_sessions_without_a_visible_process_are_ignored(qapp) -> None:
    runner = FakeRunner(stdout=json.dumps([{"pid": 5, "status": "busy", "sessionId": "a"},
                                           {"pid": 999, "status": "busy", "sessionId": "ghost"}]))
    monitor, seen = make_monitor(qapp, runner, [proc(5, "claude", exe=CLAUDE_BIN)])
    monitor.poll()
    assert [s.session_id for s in seen[-1].sessions] == ["a"]


# --------------------------------------------------------------------------- real QProcess runner


def _run_real(argv: list[str], timeout_ms: int) -> tuple[int | None, str]:
    from PySide6.QtCore import QEventLoop, QTimer

    from awake.core.agents.monitor import QProcessRunner

    runner = QProcessRunner()
    result: list[tuple[int | None, str]] = []
    loop = QEventLoop()
    runner.run(argv, {"AWAKE_TEST_VAR": "1"}, timeout_ms, lambda c, o: (result.append((c, o)), loop.quit()))
    QTimer.singleShot(10_000, loop.quit)
    loop.exec()
    return result[0]


def test_qprocess_runner_success_and_env(qapp) -> None:
    import sys

    code, out = _run_real([sys.executable, "-c", "import os; print(os.environ['AWAKE_TEST_VAR'])"], 10_000)
    assert code == 0 and out.strip() == "1"


def test_qprocess_runner_timeout_kills_without_blocking(qapp) -> None:
    import sys
    import time

    start = time.monotonic()
    code, _ = _run_real([sys.executable, "-c", "import time; time.sleep(30)"], 300)
    assert code is None and time.monotonic() - start < 5


def test_qprocess_runner_missing_program(qapp) -> None:
    code, _ = _run_real(["/nonexistent/claude-binary", "agents", "--json"], 5_000)
    assert code is None
