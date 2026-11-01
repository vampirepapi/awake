"""Codex (session files) and Cursor (hooks) detection."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from awake.core.agents.codex import CodexSource, is_codex_process, last_turn_event
from awake.core.agents.coordinator import AgentCoordinator, AgentHoldState
from awake.core.agents.cursor import (
    HOOK_EVENTS,
    HOOK_MARKER,
    CursorHooks,
    CursorSource,
    HookConfigError,
    is_cursor_app,
    windows_command,
)
from awake.core.agents.discovery import ProcessInfo
from awake.core.agents.models import (
    AgentKind,
    AgentSession,
    AgentSnapshot,
    AgentStatus,
    SourceState,
    SourceStatus,
)
from awake.core.power_manager import PowerManager

SESSION = "5973b6c0-94b8-487b-a530-2aeb6098ae0e"


def proc(pid: int, *argv: str, exe: str | None = None) -> ProcessInfo:
    return ProcessInfo(pid, 1, tuple(argv), exe)


CODEX_PROC = proc(50, "codex", exe="/opt/homebrew/bin/codex")


def rollout_line(payload_type: str, ordinal: int = 1, **extra: object) -> str:
    """Same shape as Codex's own test fixtures (codex-rs/tui/src/lib_tests.rs)."""
    return json.dumps({"timestamp": "2025-01-05T12:00:00Z", "type": "event_msg",
                       "payload": {"type": payload_type, **extra}, "ordinal": ordinal})


def write_rollout(home: Path, lines: list[str], name: str | None = None) -> Path:
    now = time.localtime()
    folder = home / "sessions" / time.strftime("%Y", now) / time.strftime("%m", now) / time.strftime("%d", now)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name or f"rollout-2025-01-05T12-00-00-{SESSION}.jsonl")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- Codex: parsing


def test_turn_states_from_real_line_shapes(tmp_path) -> None:
    meta = json.dumps({"timestamp": "t", "type": "session_meta", "payload": {"id": SESSION}})
    user = rollout_line("user_message", message="please refactor", kind="plain")
    path = write_rollout(tmp_path, [meta, user, rollout_line("task_started", turn_id="t1")])
    assert last_turn_event(path) == "started"
    path = write_rollout(tmp_path, [meta, rollout_line("task_started"), rollout_line("task_complete")])
    assert last_turn_event(path) == "ended"
    path = write_rollout(tmp_path, [rollout_line("turn_started"), rollout_line("turn_aborted")])
    assert last_turn_event(path) == "ended"
    path = write_rollout(tmp_path, [meta, user])
    assert last_turn_event(path) is None                     # session open, no turn yet


def test_marker_inside_message_text_is_not_an_event(tmp_path) -> None:
    sneaky = rollout_line("user_message", message='literally type "task_started" please')
    path = write_rollout(tmp_path, [rollout_line("task_complete"), sneaky])
    assert last_turn_event(path) == "ended"


def test_lifecycle_event_found_behind_huge_lines(tmp_path) -> None:
    big = rollout_line("item_completed", output="x" * 300_000)  # bigger than one read chunk
    path = write_rollout(tmp_path, [rollout_line("task_started"), big, big])
    assert last_turn_event(path) == "started"


def test_codex_process_matching() -> None:
    assert is_codex_process(CODEX_PROC)
    assert is_codex_process(proc(1, "/usr/local/lib/node_modules/@openai/codex/vendor/aarch64-apple-darwin/codex/codex",
                                 exe="/usr/local/lib/node_modules/@openai/codex/vendor/aarch64-apple-darwin/codex/codex"))
    assert is_codex_process(proc(2, "node", "/usr/local/lib/node_modules/@openai/codex/bin/codex.js"))
    assert is_codex_process(proc(3, r"C:\Users\me\AppData\Roaming\npm\codex.exe", exe=r"C:\x\codex.exe"))
    assert is_codex_process(proc(4, "codex", "app-server", exe="/opt/homebrew/bin/codex"))  # IDE extension host
    assert not is_codex_process(proc(5, "node", "/app/server.js"))
    assert not is_codex_process(proc(6, "vim", "codex.md"))


# --------------------------------------------------------------------------- Codex: source


def test_codex_busy_requires_process_recent_file_and_open_turn(tmp_path) -> None:
    source = CodexSource(home=tmp_path)
    path = write_rollout(tmp_path, [rollout_line("task_started")])
    sessions, status = source.read([CODEX_PROC])
    assert status.state is SourceState.ACTIVE
    assert [(s.session_id, s.status) for s in sessions] == [(SESSION, AgentStatus.BUSY)]

    sessions, status = source.read([proc(9, "zsh")])         # Codex not running (crashed / quit)
    assert sessions == [] and status.state is SourceState.NOT_RUNNING

    old = time.time() - 31 * 60                               # open turn but file stale → crash left it open
    os.utime(path, (old, old))
    sessions, _ = source.read([CODEX_PROC])
    assert sessions == []


def test_codex_turn_end_and_revert_suffix(tmp_path) -> None:
    source = CodexSource(home=tmp_path)
    write_rollout(tmp_path, [rollout_line("task_started"), rollout_line("task_complete")],
                  name=f"rollout-2025-01-05T12-00-00-{SESSION}_rev2.jsonl")
    sessions, _ = source.read([CODEX_PROC])
    assert [(s.session_id, s.status) for s in sessions] == [(SESSION, AgentStatus.IDLE)]


def test_codex_honours_codex_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "custom"))
    write_rollout(tmp_path / "custom", [rollout_line("task_started")])
    sessions, _ = CodexSource().read([CODEX_PROC])
    assert sessions and sessions[0].is_busy


def test_codex_unchanged_files_are_not_reread(tmp_path, monkeypatch) -> None:
    import awake.core.agents.codex as codex_module

    source = CodexSource(home=tmp_path)
    write_rollout(tmp_path, [rollout_line("task_started")])
    calls = []
    real = codex_module.last_turn_event
    monkeypatch.setattr(codex_module, "last_turn_event", lambda p: calls.append(p) or real(p))
    source.read([CODEX_PROC])
    source.read([CODEX_PROC])
    assert len(calls) == 1


# --------------------------------------------------------------------------- Cursor: hooks file


def test_install_merges_and_backs_up(tmp_path) -> None:
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    user_hook = {"command": "./my-audit.sh", "timeout": 3}
    (cursor_dir / "hooks.json").write_text(json.dumps(
        {"version": 1, "hooks": {"stop": [user_hook], "afterFileEdit": [{"command": "fmt"}]}, "extra": True}))
    hooks = CursorHooks(cursor_dir=cursor_dir, state_dir=tmp_path / "state", platform="darwin")
    hooks.install()
    config = json.loads((cursor_dir / "hooks.json").read_text())
    assert config["extra"] is True and config["hooks"]["afterFileEdit"] == [{"command": "fmt"}]
    assert config["hooks"]["stop"][0] == user_hook                          # user's hook kept, first
    for event in HOOK_EVENTS:
        ours = [e for e in config["hooks"][event] if HOOK_MARKER in e["command"]]
        assert len(ours) == 1 and ours[0]["timeout"] == 5
    assert "followup_message" not in hooks.script_file.read_text()          # never auto-submits chat
    assert list(cursor_dir.glob("hooks.json.awake-backup-*"))
    assert hooks.is_installed()
    hooks.install()                                                          # idempotent
    config = json.loads((cursor_dir / "hooks.json").read_text())
    assert sum(HOOK_MARKER in e["command"] for e in config["hooks"]["stop"]) == 1


def test_uninstall_removes_only_awake_entries(tmp_path) -> None:
    cursor_dir = tmp_path / ".cursor"
    hooks = CursorHooks(cursor_dir=cursor_dir, state_dir=tmp_path / "state", platform="darwin")
    cursor_dir.mkdir()
    (cursor_dir / "hooks.json").write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "mine"}]}}))
    hooks.install()
    hooks.uninstall()
    config = json.loads((cursor_dir / "hooks.json").read_text())
    assert config["hooks"] == {"stop": [{"command": "mine"}]}
    assert not hooks.script_file.exists() and not hooks.is_installed()


def test_invalid_hooks_json_is_never_touched(tmp_path) -> None:
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "hooks.json").write_text("{ this is not json")
    hooks = CursorHooks(cursor_dir=cursor_dir, state_dir=tmp_path / "state", platform="darwin")
    with pytest.raises(HookConfigError):
        hooks.install()
    assert (cursor_dir / "hooks.json").read_text() == "{ this is not json"
    assert not hooks.script_file.exists()


def test_windows_plan_is_inline_powershell(tmp_path) -> None:
    hooks = CursorHooks(cursor_dir=tmp_path / ".cursor", state_dir=Path(r"C:\Users\it's me\Awake"), platform="win32")
    plan = hooks.plan()
    assert plan.script_file is None
    command = plan.entries["beforeSubmitPrompt"]["command"]
    assert HOOK_MARKER in command and "\n" not in command
    assert "ConvertFrom-Json" in command and "'{\"continue\":true}'" in command
    assert "'C:\\Users\\it''s me\\Awake'" in command                         # PowerShell-escaped quote
    assert "prompt" not in windows_command("beforeSubmitPrompt", Path("C:/x"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell script")
def test_real_hook_script_never_stores_the_prompt(tmp_path) -> None:
    hooks = CursorHooks(cursor_dir=tmp_path / ".cursor", state_dir=tmp_path / "data dir" / "cursor", platform="darwin")
    hooks.install()
    conv = "3f2a9c1e-7b44-4d2a-9a1e-1234567890ab"
    secret = 'SECRET "conversation_id":"evil/../x" $(whoami) rm -rf ~'

    def run(event: str, payload: object, pretty: bool = False) -> tuple[int, str]:
        result = subprocess.run(["/bin/sh", "-c", f'"{hooks.script_file}" {event}'],
                                input=json.dumps(payload, indent=2 if pretty else None),
                                capture_output=True, text=True, timeout=10)
        return result.returncode, result.stdout.strip()

    for pretty in (False, True):
        assert run("beforeSubmitPrompt", {"conversation_id": conv, "prompt": secret}, pretty) == (0, '{"continue":true}')
        assert (hooks.state_dir / conv).read_text().startswith("busy ")
        assert run("stop", {"conversation_id": conv, "status": "aborted"}, pretty) == (0, "{}")
        assert (hooks.state_dir / conv).read_text().startswith("idle ")
    assert run("sessionEnd", {"conversation_id": conv}) == (0, "{}")
    assert not (hooks.state_dir / conv).exists()
    assert run("beforeSubmitPrompt", "garbage") == (0, '{"continue":true}')   # fail-safe reply
    leaked = [p for p in tmp_path.rglob("*") if p.is_file() and "SECRET" in p.read_text(errors="ignore")]
    assert leaked == []


# --------------------------------------------------------------------------- Cursor: source


CURSOR_APP = proc(70, "/Applications/Cursor.app/Contents/MacOS/Cursor", exe="/Applications/Cursor.app/Contents/MacOS/Cursor")


def test_cursor_app_matching() -> None:
    assert is_cursor_app(CURSOR_APP)
    assert is_cursor_app(proc(71, r"C:\Users\me\AppData\Local\Programs\cursor\Cursor.exe",
                              exe=r"C:\Users\me\AppData\Local\Programs\cursor\Cursor.exe"))
    assert not is_cursor_app(proc(72, "/Applications/Cursor.app/Contents/Frameworks/Cursor Helper (Renderer).app/"
                                      "Contents/MacOS/Cursor Helper (Renderer)"))


def test_cursor_source_states(tmp_path) -> None:
    hooks = CursorHooks(cursor_dir=tmp_path / ".cursor", state_dir=tmp_path / "state", platform="darwin")
    source = CursorSource(hooks)
    assert source.read([proc(1, "zsh")])[1].state is SourceState.NOT_RUNNING
    assert source.read([CURSOR_APP])[1].state is SourceState.NOT_CONNECTED
    hooks.install()
    (hooks.state_dir / "conv-a").write_text("busy 1\n")
    (hooks.state_dir / "conv-b").write_text("idle 1\n")
    (hooks.state_dir / "../escape").resolve()                                 # names are validated
    stale = hooks.state_dir / "conv-old"
    stale.write_text("busy 1\n")
    old = time.time() - 25 * 3600
    os.utime(stale, (old, old))
    sessions, status = source.read([CURSOR_APP])
    assert status.state is SourceState.ACTIVE
    assert sorted((s.session_id, s.status) for s in sessions) == [("conv-a", AgentStatus.BUSY),
                                                                    ("conv-b", AgentStatus.IDLE)]
    assert not stale.exists()                                                 # swept
    assert source.read([proc(1, "zsh")])[0] == []                             # Cursor quit mid-turn → ignored


# --------------------------------------------------------------------------- mixed agents


def snap(sessions: list[AgentSession], *sources: SourceStatus) -> AgentSnapshot:
    return AgentSnapshot(tuple(sessions), tuple(sources))


def test_any_busy_agent_holds_even_if_another_is_unreadable(qapp, backend, clock) -> None:
    coordinator = AgentCoordinator(PowerManager(backend), lambda: None, clock=clock)
    coordinator.update(snap([AgentSession(AgentKind.CODEX, "c", 0, AgentStatus.BUSY)],
                            SourceStatus(AgentKind.CLAUDE_CODE, SourceState.UNAVAILABLE, "x"),
                            SourceStatus(AgentKind.CODEX, SourceState.ACTIVE)))
    assert coordinator.activity.state is AgentHoldState.HOLDING and backend.system


def test_unreadable_agent_with_nothing_busy_keeps_decision(qapp, backend, clock) -> None:
    coordinator = AgentCoordinator(PowerManager(backend), lambda: None, clock=clock)
    coordinator.update(snap([AgentSession(AgentKind.CURSOR, "k", 0, AgentStatus.BUSY)],
                            SourceStatus(AgentKind.CURSOR, SourceState.ACTIVE)))
    coordinator.update(snap([], SourceStatus(AgentKind.CODEX, SourceState.UNAVAILABLE, "x"),
                            SourceStatus(AgentKind.CURSOR, SourceState.ACTIVE)))
    clock.advance(120)
    coordinator._grace_timer.check()
    assert backend.system                                                     # unknown ≠ idle


def test_wording_for_mixed_agents(qapp, backend, clock) -> None:
    from awake.ui import agent_text

    coordinator = AgentCoordinator(PowerManager(backend), lambda: None, clock=clock)
    coordinator.update(snap([AgentSession(AgentKind.CODEX, "c", 0, AgentStatus.BUSY)],
                            SourceStatus(AgentKind.CODEX, SourceState.ACTIVE)))
    assert agent_text.describe(coordinator.activity) == "Codex is working."
    assert agent_text.hero_line(coordinator.activity) == "AI mode: staying awake while Codex works."
    coordinator.update(snap([AgentSession(AgentKind.CLAUDE_CODE, "a", 1, AgentStatus.BUSY),
                             AgentSession(AgentKind.CURSOR, "k", 0, AgentStatus.BUSY)],
                            SourceStatus(AgentKind.CLAUDE_CODE, SourceState.ACTIVE),
                            SourceStatus(AgentKind.CURSOR, SourceState.ACTIVE)))
    assert agent_text.short_status(coordinator.activity) == "Claude Code + Cursor working"
    assert agent_text.hero_line(coordinator.activity) == "AI mode: staying awake while Claude Code + Cursor work."
    assert agent_text.describe(coordinator.activity) == "2 sessions working."
