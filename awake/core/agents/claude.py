"""Claude Code detection.

Signal: ``claude agents --json`` — the documented way to list live sessions with a
``status`` of ``busy`` / ``waiting`` / ``idle`` (https://code.claude.com/docs/en/agent-view).

How the command is located: a Dock/Finder-launched app gets a minimal PATH, so
``claude`` usually isn't on it. Instead we take the executable of a *running*
Claude process (psutil ``exe()``), which is also guaranteed to match the version
in use. We only ever run the command while a Claude process exists.

Process matching never relies on ``name()``: the native binary reports its
version (e.g. ``"2.1.316"``) as its process name on macOS.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from awake.core.agents.discovery import ProcessInfo
from awake.core.agents.models import AgentKind, AgentSession, AgentStatus

# Keep the polled CLI quiet and offline: no auto-update, telemetry or error reports.
# https://code.claude.com/docs/en/env-vars
QUIET_ENV: dict[str, str] = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
}

_NATIVE_NAMES = {"claude", "claude.exe"}
_NODE_NAMES = {"node", "node.exe", "bun", "bun.exe"}


class ClaudeOutputError(ValueError):
    """``claude agents --json`` produced something we can't use."""


@dataclass(frozen=True, slots=True)
class ClaudeProcess:
    pid: int
    command: tuple[str, ...]  # how to invoke this installation of the CLI


def _basename(path: str) -> str:
    return os.path.basename(path.replace("\\", "/")).lower()


def match_claude_process(proc: ProcessInfo) -> ClaudeProcess | None:
    """Return how to invoke Claude Code if ``proc`` is a Claude Code CLI process."""
    argv = proc.cmdline
    if not argv:
        return None
    # Our own (or anyone's) `claude agents …` query is not a session.
    if len(argv) > 1 and argv[1] == "agents":
        return None
    first = _basename(argv[0])
    if first in _NATIVE_NAMES or _basename(proc.exe or "") in _NATIVE_NAMES:
        exe = proc.exe or argv[0]
        return ClaudeProcess(proc.pid, (exe,))
    if first in _NODE_NAMES or _basename(proc.exe or "") in _NODE_NAMES:
        # npm install: node …/@anthropic-ai/claude-code/cli.js
        for index, arg in enumerate(argv[1:], start=1):
            normalised = arg.replace("\\", "/")
            if "@anthropic-ai/claude-code/" in normalised and normalised.endswith((".js", ".mjs")):
                return ClaudeProcess(proc.pid, (proc.exe or argv[0], *argv[1 : index + 1]))
    return None


def find_claude_processes(processes: list[ProcessInfo]) -> list[ClaudeProcess]:
    return [m for p in processes if (m := match_claude_process(p)) is not None]


def agents_command(processes: list[ClaudeProcess]) -> list[str] | None:
    """Command line for ``claude agents --json`` using a running installation."""
    for proc in processes:
        if proc.command and os.path.isabs(proc.command[0]):
            return [*proc.command, "agents", "--json"]
    return None


def parse_agents_json(text: str) -> list[AgentSession]:
    """Parse ``claude agents --json``. Only sessions with a live process are returned.

    Sessions without ``pid``/``status`` (background sessions whose process has
    exited) are ignored: they are not running on this machine right now.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClaudeOutputError(f"not JSON: {exc.msg}") from exc
    if not isinstance(data, list):
        raise ClaudeOutputError("expected a JSON array")
    sessions: list[AgentSession] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        pid, raw_status = entry.get("pid"), entry.get("status")
        if not isinstance(pid, int) or not isinstance(raw_status, str):
            continue
        try:
            status = AgentStatus(raw_status)
        except ValueError:
            continue  # unknown future status: ignore rather than guess
        session_id = entry.get("sessionId") or entry.get("id") or f"pid-{pid}"
        sessions.append(AgentSession(AgentKind.CLAUDE_CODE, str(session_id), pid, status))
    return sessions
