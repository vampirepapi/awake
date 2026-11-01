"""Cursor detection through Cursor's agent hooks (https://cursor.com/docs/agent/hooks).

Cursor exposes no status command, so detection needs a one-time, user-approved
connection: Awake adds three entries to the user-level ``~/.cursor/hooks.json``:

* ``beforeSubmitPrompt`` → this conversation is busy
* ``stop`` (status completed / aborted / error) → idle
* ``sessionEnd`` → forget the conversation

Each runs a tiny command that writes ``busy <time>`` / ``idle <time>`` to
``<Awake data>/agent-state/cursor/<conversation_id>``. The hook payload includes
the user's prompt; the command extracts **only** the conversation id, so prompts
never touch the disk.

Safety properties:
* Hooks are fail-open by default in Cursor; ours always exit 0 and print
  ``{"continue":true}`` (beforeSubmitPrompt) or ``{}`` — never ``followup_message``,
  which would auto-submit a chat message.
* User level only: Cursor's cloud agents don't run user-level hooks, so remote
  work can never keep this machine awake.
* Merge, never overwrite: other hooks are preserved; the file is validated before
  anything is written; the original is kept as ``hooks.json.awake-original``;
  symlinked files are edited in place; disconnect removes only Awake's entries.
* Verified on macOS with simulated hook payloads. Not yet run against a live
  Cursor; the Windows (PowerShell) command is untested.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from awake.core.agents.discovery import ProcessInfo
from awake.core.agents.models import AgentKind, AgentSession, AgentStatus, SourceState, SourceStatus
from awake.logging_setup import kv
from awake.paths import app_data_dir

log = logging.getLogger("awake.agents")

HOOK_EVENTS = ("beforeSubmitPrompt", "stop", "sessionEnd")
HOOK_MARKER = "awake-agent-hook"
HOOK_TIMEOUT_SECONDS = 5
STATE_MAX_AGE_SECONDS = 24 * 3600
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class HookConfigError(RuntimeError):
    """Cursor's hooks.json can't be changed safely (invalid JSON, permissions…)."""


def cursor_state_dir() -> Path:
    return app_data_dir() / "agent-state" / "cursor"


def is_cursor_app(proc: ProcessInfo) -> bool:
    """The Cursor desktop app's main process (not its 'Cursor Helper' children)."""
    exe = (proc.exe or (proc.cmdline[0] if proc.cmdline else "")).replace("\\", "/")
    return os.path.basename(exe).lower() in {"cursor", "cursor.exe"}


# --------------------------------------------------------------------------- hook command


SH_SCRIPT = r"""#!/bin/sh
# Awake: records whether a Cursor agent turn is running, so the computer stays awake
# while it works. Stores only the conversation id and busy/idle — never the prompt.
# Remove it from Awake › Settings › AI agents › Cursor › Disconnect.
payload=$(cat 2>/dev/null | tr -d '\n\r')
field() { printf '%s' "$payload" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([A-Za-z0-9_-]*\)\".*/\1/p"; }
event=$(field hook_event_name); [ -n "$event" ] || event="$1"
id=$(field conversation_id); [ -n "$id" ] || id=$(field session_id)
dir=__STATE_DIR__
if [ -n "$id" ]; then
  mkdir -p "$dir" 2>/dev/null
  case "$event" in
    beforeSubmitPrompt) printf 'busy %s\n' "$(date +%s)" > "$dir/$id" 2>/dev/null ;;
    stop) printf 'idle %s\n' "$(date +%s)" > "$dir/$id" 2>/dev/null ;;
    sessionEnd) rm -f "$dir/$id" 2>/dev/null ;;
  esac
fi
if [ "$event" = "beforeSubmitPrompt" ]; then printf '{"continue":true}\n'; else printf '{}\n'; fi
exit 0
"""


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def windows_command(event: str, state_dir: Path) -> str:
    """Inline PowerShell (Cursor runs hook commands through PowerShell on Windows). UNTESTED."""
    action = {
        "beforeSubmitPrompt": "Set-Content -Path $f -Value ('busy ' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds())",
        "stop": "Set-Content -Path $f -Value ('idle ' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds())",
        "sessionEnd": "Remove-Item -Path $f -ErrorAction SilentlyContinue",
    }[event]
    # No literal double quotes: command-line parsing could strip them and break the JSON reply.
    reply = "('{' + [char]34 + 'continue' + [char]34 + ':true}')" if event == "beforeSubmitPrompt" else "'{}'"
    return (
        f"<# {HOOK_MARKER} #> "
        "try { $j = $input | Out-String | ConvertFrom-Json; $id = [string]$j.conversation_id; "
        "if (-not $id) { $id = [string]$j.session_id }; "
        "if ($id -match '^[A-Za-z0-9_-]+$') { "
        f"$d = {_ps_quote(str(state_dir))}; New-Item -ItemType Directory -Force -Path $d | Out-Null; "
        f"$f = Join-Path $d $id; {action} }} }} catch {{}}; {reply}"
    )


@dataclass(frozen=True)
class HookPlan:
    """What Awake will write — shown to the user before they confirm."""

    hooks_file: Path
    script_file: Path | None
    entries: dict[str, dict]

    def describe(self) -> str:
        lines = [f"Adds to {self.hooks_file}:"]
        for event, entry in self.entries.items():
            lines.append(f'  "{event}": {json.dumps(entry)}')
        if self.script_file is not None:
            lines.append(f"Creates {self.script_file} (stores only the conversation id and busy/idle).")
        lines.append("Your existing hooks are kept, and the original file is backed up first.")
        return "\n".join(lines)


class CursorHooks:
    """Installs / removes Awake's entries in the user-level ``~/.cursor/hooks.json``."""

    def __init__(self, cursor_dir: Path | None = None, state_dir: Path | None = None,
                 platform: str = sys.platform) -> None:
        self.cursor_dir = cursor_dir or Path.home() / ".cursor"
        self._state_dir = state_dir
        self._windows = platform == "win32"
        self._installed_cache: tuple[tuple | None, bool] = (None, False)

    @property
    def state_dir(self) -> Path:
        return self._state_dir or cursor_state_dir()

    @property
    def hooks_file(self) -> Path:
        return self.cursor_dir / "hooks.json"

    @property
    def script_file(self) -> Path:
        return self.cursor_dir / "hooks" / f"{HOOK_MARKER}.sh"

    def plan(self) -> HookPlan:
        entries = {}
        for event in HOOK_EVENTS:
            if self._windows:
                command = windows_command(event, self.state_dir)
            else:
                command = f'"{self.script_file}" {event}'
            entries[event] = {"command": command, "timeout": HOOK_TIMEOUT_SECONDS}
        return HookPlan(self.hooks_file, None if self._windows else self.script_file, entries)

    # ------------------------------------------------------------------ status

    def is_installed(self) -> bool:
        """Never raises: any unreadable or unexpected file simply means 'not connected'."""
        try:
            stat_key = self.hooks_file.stat()
            key = (stat_key.st_mtime_ns, stat_key.st_size, self._windows or self.script_file.exists())
        except OSError:
            return False
        if key == self._installed_cache[0]:
            return self._installed_cache[1]
        try:
            hooks = self._hooks_section(self._load())
            installed = key[2] and all(
                any(isinstance(e, dict) and self._is_ours(e) for e in self._event_list(hooks, event))
                for event in HOOK_EVENTS
            )
        except HookConfigError:
            installed = False
        self._installed_cache = (key, installed)
        return installed

    @staticmethod
    def _hooks_section(config: dict) -> dict:
        hooks = config.get("hooks")
        if hooks is None:
            return {}
        if not isinstance(hooks, dict):
            raise HookConfigError("Cursor's hooks.json has an unexpected 'hooks' value; Awake won't change it.")
        return hooks

    @staticmethod
    def _event_list(hooks: dict, event: str) -> list:
        entries = hooks.get(event)
        if entries is None:
            return []
        if not isinstance(entries, list):
            raise HookConfigError(f"Cursor's hooks.json has an unexpected '{event}' value; Awake won't change it.")
        return entries

    @staticmethod
    def _is_ours(entry: dict) -> bool:
        return HOOK_MARKER in str(entry.get("command", ""))

    # ------------------------------------------------------------------ install / remove

    def install(self) -> HookPlan:
        plan = self.plan()
        config = self._load()
        hooks = self._hooks_section(config)                        # validate everything first …
        existing = {event: self._event_list(hooks, event) for event in plan.entries}
        if not self.hooks_file.exists() and not self._original_backup.exists():
            # An empty "original" records that no file existed before Awake created one.
            self._original_backup.parent.mkdir(parents=True, exist_ok=True)
            self._original_backup.write_text("", encoding="utf-8")
        self._backup()                                             # … then back up …
        config["hooks"] = hooks                                    # … then change
        for event, entry in plan.entries.items():
            hooks[event] = [e for e in existing[event] if not (isinstance(e, dict) and self._is_ours(e))] + [entry]
        config.setdefault("version", 1)
        if plan.script_file is not None:
            self._write_script(plan.script_file)
        self._write(config)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        log.info(kv("cursor_hooks_installed", file=self.hooks_file))
        return plan

    def uninstall(self) -> None:
        if self.hooks_file.exists():
            config = self._load()
            hooks = self._hooks_section(config)
            self._backup()
            for event in list(hooks):
                entries = hooks[event]
                if not isinstance(entries, list):
                    continue
                kept = [e for e in entries if not (isinstance(e, dict) and self._is_ours(e))]
                if kept:
                    hooks[event] = kept
                elif len(kept) != len(entries):
                    del hooks[event]
            created_by_awake = self._original_backup.exists() and self._original_backup.stat().st_size == 0
            if config == {"version": 1, "hooks": {}} and created_by_awake:
                self._target().unlink()                            # Awake created this file; remove it again
                self._original_backup.unlink()
                self.hooks_file.with_name("hooks.json.awake-backup").unlink(missing_ok=True)
            else:
                self._write(config)
        if self.script_file.exists():
            self.script_file.unlink()
        shutil.rmtree(self.state_dir, ignore_errors=True)
        log.info(kv("cursor_hooks_removed", file=self.hooks_file))

    # ------------------------------------------------------------------ file helpers

    def _load(self) -> dict:
        if not self.hooks_file.exists():
            return {"version": 1, "hooks": {}}
        try:
            data = json.loads(self.hooks_file.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            raise HookConfigError(f"Cursor's hooks.json isn't valid JSON ({exc}); Awake won't change it.") from exc
        if not isinstance(data, dict):
            raise HookConfigError("Cursor's hooks.json has an unexpected format; Awake won't change it.")
        return data

    @property
    def _original_backup(self) -> Path:
        return self.hooks_file.with_name("hooks.json.awake-original")

    def _target(self) -> Path:
        """The real file (follows a dotfiles symlink so it isn't replaced by a copy)."""
        return Path(os.path.realpath(self.hooks_file))

    def _backup(self) -> None:
        """``awake-original``: the file before Awake ever touched it (never overwritten).
        ``awake-backup``: the file before Awake's latest change."""
        if not self.hooks_file.exists():
            return
        if not self._original_backup.exists():
            shutil.copy2(self._target(), self._original_backup)
        shutil.copy2(self._target(), self.hooks_file.with_name("hooks.json.awake-backup"))

    def _write(self, config: dict) -> None:
        target = self._target()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".awake-tmp")
        tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)

    def _write_script(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SH_SCRIPT.replace("__STATE_DIR__", _sh_quote(str(self.state_dir))), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# --------------------------------------------------------------------------- source


class CursorSource:
    def __init__(self, hooks: CursorHooks, clock=time.time) -> None:  # noqa: ANN001
        self._hooks = hooks
        self._clock = clock

    def read(self, processes: list[ProcessInfo]) -> tuple[list[AgentSession], SourceStatus]:
        apps = [p for p in processes if is_cursor_app(p)]
        if not apps:
            return [], SourceStatus(AgentKind.CURSOR, SourceState.NOT_RUNNING)
        # A "busy" written before the running Cursor started was left by a crash or force-quit.
        started = min((p.create_time for p in apps if p.create_time), default=0.0)
        if not self._hooks.is_installed():
            return [], SourceStatus(AgentKind.CURSOR, SourceState.NOT_CONNECTED,
                                    "Connect Cursor in Settings to detect it.")
        sessions: list[AgentSession] = []
        now = self._clock()
        try:
            entries = list(os.scandir(self._hooks.state_dir))
        except FileNotFoundError:
            entries = []
        except OSError as exc:
            log.warning(kv("cursor_state_unreadable", error=type(exc).__name__))
            return [], SourceStatus(AgentKind.CURSOR, SourceState.UNAVAILABLE, "Couldn't read Cursor's status.")
        for entry in entries:
            if not _ID_RE.match(entry.name):
                continue
            try:
                mtime = entry.stat().st_mtime
                age = now - mtime
                if age > STATE_MAX_AGE_SECONDS:
                    os.unlink(entry.path)  # sweep conversations that never sent sessionEnd
                    continue
                with open(entry.path, encoding="utf-8") as fh:
                    word = fh.read(16).split(" ", 1)[0].strip()
            except OSError:
                continue
            status = AgentStatus.BUSY if word == "busy" and mtime >= started else AgentStatus.IDLE
            sessions.append(AgentSession(AgentKind.CURSOR, entry.name, 0, status))
        return sessions, SourceStatus(AgentKind.CURSOR, SourceState.ACTIVE)
