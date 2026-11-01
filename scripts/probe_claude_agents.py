"""Probe Claude Code agent detection on this machine (used by CI on Windows; works anywhere).

Reports, without failing:
1. where `claude` is installed,
2. what `claude agents --json` returns (exit code, output, parsed by Awake's parser),
3. which running processes Awake's matcher recognises as Claude Code, and the
   command Awake would build from them.

Usage:  python scripts/probe_claude_agents.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from awake.core.agents.claude import (  # noqa: E402
    QUIET_ENV,
    ClaudeOutputError,
    agents_command,
    find_claude_processes,
    parse_agents_json,
)
from awake.core.agents.discovery import list_processes  # noqa: E402


def redact(text: str) -> str:
    """Keep the structure Awake relies on; drop folder paths and session names."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return repr(text.strip()[:200])
    if isinstance(data, list):
        data = [{k: ("<redacted>" if k in {"cwd", "name"} else v) for k, v in entry.items()}
                if isinstance(entry, dict) else entry for entry in data]
    return json.dumps(data)


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    for candidate in (home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude"):
        if candidate.exists():
            return str(candidate)
    return None


def main() -> int:
    print(f"platform: {sys.platform}")
    claude = find_claude()
    print(f"claude executable: {claude}")
    if claude is None:
        print("RESULT: Claude Code not found")
        return 0

    version = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=60, check=False)
    print(f"claude --version: exit={version.returncode} out={version.stdout.strip()!r}")

    env = {**os.environ, **QUIET_ENV}
    result = subprocess.run([claude, "agents", "--json"], capture_output=True, text=True, env=env,
                            timeout=60, check=False)
    print(f"claude agents --json: exit={result.returncode}")
    print(f"  stdout (paths and names redacted): {redact(result.stdout)[:500]}")
    print(f"  stderr: {result.stderr.strip()[:300]!r}")
    if result.returncode == 0:
        try:
            sessions = parse_agents_json(result.stdout)
            print(f"  parsed by Awake: OK, {len(sessions)} live session(s)")
            print("RESULT: `claude agents --json` WORKS on this platform")
        except ClaudeOutputError as exc:
            print(f"  parsed by Awake: FAILED ({exc})")
            print("RESULT: command runs but output is not what Awake expects")
    else:
        print("RESULT: `claude agents --json` is NOT supported here")

    matches = find_claude_processes(list_processes())
    print(f"processes Awake recognises as Claude Code: {len(matches)}")
    for match in matches:
        print(f"  pid {match.pid}: {match.command}")
    print(f"command Awake would run: {agents_command(matches)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
