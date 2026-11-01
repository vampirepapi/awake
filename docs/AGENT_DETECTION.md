# Agent detection — research and design

Goal: keep the computer awake **while an AI coding agent is actually working** (Claude Code, Codex CLI,
OpenCode, Cursor, Gemini CLI, Aider), and let it sleep normally once the agent is done or waiting for the
user. Researched 2026-11-01; items marked **verified** were reproduced on a real Mac (macOS 26.5,
Claude Code 2.1.316), everything else is from the cited source.

## 1. What already exists

### Built into the agents

| Agent | Built-in sleep prevention | Notes |
|---|---|---|
| Claude Code CLI | macOS only: spawns `caffeinate -i -t 300`, renewed while busy | **Verified**: both `caffeinate` processes on this Mac were children of `claude`. Not officially documented beyond a changelog fix ([CHANGELOG 2.1.83](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)); no opt-out ([#21432](https://github.com/anthropics/claude-code/issues/21432)); reports of battery drain ([#85261](https://github.com/anthropics/claude-code/issues/85261)) and of nothing being held on Windows ([#96679](https://github.com/anthropics/claude-code/issues/96679)). |
| Codex CLI | `[features].prevent_idle_sleep`, experimental, **off by default** | IOKit on macOS ([PR 11711](https://github.com/openai/codex/pull/11711)); `PowerSetRequest` on Windows ([PR 11766](https://github.com/openai/codex/pull/11766)). |
| Cursor, OpenCode, Gemini CLI, Aider | none found | Cursor staff suggest running `caffeinate` manually ([forum](https://forum.cursor.com/t/option-to-enable-caffeinate-spawning-or-similar-for-other-oses/165844)). |

So on **Windows nobody keeps the PC awake for agents by default**, and on macOS only Claude Code does, invisibly and
without a battery policy. That is the gap Awake fills.

### Third-party tools (all small)

They converge on one design: **agent hooks → one state file per session → PID liveness check → grace period →
absolute cap**. Examples: [phin/insomnia](https://github.com/phin/insomnia) (state file per session, PID check every
10 s, 4 h cap), [botus-ai/vigil](https://github.com/botus-ai/vigil) (hooks → transcripts → CPU/network heuristic),
[peterjuras/Insomnia](https://github.com/peterjuras/Insomnia) (Windows; hooks + Codex session files + `tasklist`
poll), [bmoeskau/caffeinate-claude](https://github.com/bmoeskau/caffeinate-claude) (ref-counted
`UserPromptSubmit`/`Stop`), [samber/cc-caffeine](https://github.com/samber/cc-caffeine) (deprecated). Recurring
failures: stale "busy" after Ctrl+C or a crash (no end event), and "stays awake whenever the app is open" for tools
that only check that a process exists (Amphetamine, Holdor).

## 2. Signals per agent

| Agent | Busy (start) | Idle (end) | Zero-setup fallback |
|---|---|---|---|
| **Claude Code** | hook `UserPromptSubmit` | hooks `Stop` (unless `background_tasks` still running), `StopFailure`, `SessionEnd`, `Notification` (`permission_prompt`, `idle_prompt`). **No interrupt event.** [docs](https://code.claude.com/docs/en/hooks) | **`claude agents --json`** — every live session with `pid` and `status` `busy`/`waiting`/`idle` ([docs](https://code.claude.com/docs/en/agent-view)). **Verified**: listed 3 sessions correctly, ~70 ms per call. |
| **Codex CLI** | hook `UserPromptSubmit` | hooks `Stop`, **`Interrupt`**, `SessionEnd` (also fires after 30 min idle). User-added hooks must be trusted via `/hooks`. [docs](https://developers.openai.com/codex/hooks) | Rollout files `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` record `task_started` / `task_complete` / `TurnAborted` ([recorder.rs](https://github.com/openai/codex/blob/main/codex-rs/rollout/src/recorder.rs)). `notify` only fires on turn complete. |
| **OpenCode** | plugin event `session.status` = `busy` | `session.status` = `idle` (`session.idle` is deprecated) [plugins](https://opencode.ai/docs/plugins/) | `GET /session/status` needs a known server port; the TUI uses a random one ([server](https://opencode.ai/docs/server/)) |
| **Cursor** (IDE) | hook `beforeSubmitPrompt` | hook `stop` with `status` `completed`/`aborted`/`error` — covers interrupts. `~/.cursor/hooks.json` ([docs](https://cursor.com/docs/agent/hooks)) | process watch; CLI (`agent`) hook support unverified |
| **Gemini CLI** | hook `BeforeAgent` | `AfterAgent`, `SessionEnd` ([reference](https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md)) | process watch |
| **Aider** | — | `--notifications-command` (waiting for input) | process watch |

## 3. OS-level facts that shape the design

- **Process matching must use the executable path, not the name.** **Verified**: Claude Code's `psutil` `name()` is
  its version (`'2.1.316'`); `exe()` is `~/.local/share/claude/versions/2.1.316`. Codex is a Node launcher plus a Rust
  `codex` binary; Cursor CLI is a bundled `node` under `cursor-agent`; Gemini CLI and Aider are `node`/`python`
  with the tool in the command line.
- **Network connections are useless for busy/idle**: an idle Claude session keeps several HTTPS keep-alive connections.
- **Transcript writes lag** (batched; a long build may write nothing for minutes) → only usable with a long grace period.
  The Claude transcript format is internal and changes between versions; use modification time, never content.
- **Polling cost is small**: one `psutil.process_iter` pass ≈ 7–20 ms; `claude agents --json` ≈ 70 ms. Polling every
  10–30 s stays well under 0.5 % of one core.
- **Exit can be event-driven**: kqueue `NOTE_EXIT` (macOS) / `WaitForSingleObject` on a process handle (Windows).
- **Lid close and the power button always win**: user-initiated sleep is not blocked by assertions or power requests on
  either OS ([PowerSetRequest remarks](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powersetrequest)).
- **Windows Modern Standby on battery**: Microsoft documents that system/execution power requests are *"terminated
  5 minutes after the system sleep timeout has expired"* on DC power
  ([PowerSetRequest](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powersetrequest)). The
  `SetThreadExecutionState` page does not say whether the same cap applies to it; assume it does until tested. This
  affects Awake's manual sessions too — added to the Windows test plan. Switching to `PowerCreateRequest` would add a
  readable reason in `powercfg /requests` but would not escape this cap.

## 4. Proposed design for Awake

```
Signal sources ──► AgentMonitor ──► per-session state ──► ActivityCoordinator ──► PowerManager owner "agents"
(polling, files,      (Qt, no UI)     (busy/idle/ended,      (grace, cap, battery     (independent of the manual
 hook drop-box)                        last signal, pid)      policy)                    "session" owner)
```

The power layer needs no changes: agent activity is just another `PowerManager` owner, so a manual session and agent
activity never release each other's request (already covered by `test_stop_does_not_release_other_owners`). Agent
mode keeps the **system** awake only; the display may sleep.

### Layer 1 — zero setup (ship first)

1. **Claude Code**: if a Claude process exists (cheap `psutil` scan by exe path every 10 s), poll
   `claude agents --json` every 20 s. Busy if any session is `busy`; `waiting`/`idle` count as idle.
2. **Codex**: read the last lines of the newest rollout file (event type field only) for `task_started` vs
   `task_complete`/`TurnAborted`; confirm a `codex` process is alive.
3. **Fallback for anything else detected by process** (Cursor CLI, Gemini, Aider, OpenCode): busy while the process
   tree's CPU is above ~3–5 % for two consecutive 5 s samples. Explicitly lower confidence; off by default.

### Layer 2 — opt-in hooks (most accurate, needs user consent)

Settings › Agents offers "Install hook" per agent. Awake shows exactly what it will add to the agent's config file,
backs the file up, and can remove it again. Hooks call a tiny command (`Awake --agent-event <agent> <event>`) that
writes `agent-state/<agent>-<session>.json` (event, time, pid) into Awake's data folder and exits — **no network
port**, works even if Awake isn't running yet, cross-platform. Awake watches that folder.

- Claude Code: `UserPromptSubmit` busy · `PreToolUse`/`PostToolUse` heartbeat · `Stop`/`StopFailure`/`SessionEnd`/`Notification` idle
- Codex: `UserPromptSubmit` busy · `Stop`/`Interrupt`/`SessionEnd` idle (user must trust the hook in `/hooks`)
- Cursor: `beforeSubmitPrompt` busy · `stop` idle
- OpenCode: a small plugin file that maps `session.status` busy/idle
- Gemini CLI: `BeforeAgent` / `AfterAgent`

### Rules that make it trustworthy

- **Per-session state**, awake while *any* session is busy; only main-agent end events end a turn (ignore `SubagentStop`).
- **Grace period** 60 s after the last session goes idle (avoids flapping between turns).
- **Stale-busy protection**: a busy session with no signal for 20 min *and* no confirmation from Layer 1 is treated as
  idle; a dead PID ends the session immediately (exit events, not polling).
- **Absolute cap**: 4 h per continuous hold (fixed in 0.2; a setting may follow).
- **Battery policy**: optional "don't keep awake for agents on battery below 20 %" — the main complaint about Claude's
  built-in behaviour.
- **Visible**: dashboard card "Agents" listing detected sessions and why Awake is holding the machine; menu bar shows
  `☀ AI` while agent-driven; history records agent-driven stretches as their own session type.
- **Privacy**: read process paths, statuses, timestamps and event *types* only — never prompts, transcripts or output.

### Implementation phases

1. ✅ **Shipped in 0.2**: `AgentMonitor` + Claude (`claude agents --json`) + process discovery + coordinator with grace/cap/battery guard, dashboard row, tray toggle and `☀ AI` label — macOS verified live; on Windows (GitHub runner) `claude agents --json` works and Awake's process matching recognises `claude.exe` — a real working session on a Windows PC is still untested.
2. Codex rollout signal, CPU fallback, Agents UI card and settings, battery policy.
3. Opt-in hook installer and drop-box for Claude, Codex, Cursor, OpenCode, Gemini.
4. Windows: verify Modern Standby behaviour on battery; evaluate `PowerCreateRequest` for a readable reason string.
