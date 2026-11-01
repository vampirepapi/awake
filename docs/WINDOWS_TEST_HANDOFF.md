# Windows test handoff (for a Claude Code session on a Windows 10/11 PC)

You are picking up Awake (latest release) on a real Windows PC. Your job: verify that it really works
on Windows desktop hardware, fix any Windows-only bugs, and report evidence. Read `README.md`
and `docs/ARCHITECTURE.md` first.

## What is already established (do not re-litigate)

- macOS: verified on real hardware (power assertions seen with `pmset`, released on stop/quit/crash).
- Windows, on GitHub's hosted Windows Server runner only (not a desktop PC):
  - 78 mocked tests pass.
  - `tests/test_native_windows.py`: all 3 pass — real `SetThreadExecutionState` calls, and
    `powercfg /requests` lists the process under SYSTEM (not DISPLAY), moves it to DISPLAY when
    display-awake is on, and drops it on release.
  - `Awake.exe`, the `Awake-<version>-Setup.exe` installer and the portable zip build successfully.
- **Never launched on a Windows desktop.** Tray, notifications, installer, autostart, close-to-tray,
  real idle behaviour and performance are all unverified on Windows.

## Ground rules

- Run in an **elevated (Administrator) terminal** — `powercfg /requests` needs it. Awake itself
  must never need admin rights; if anything does, that is a bug.
- Report only what you observed. Mocked tests passing is not verification. If you could not check
  something, say so.
- The tray icon, notifications and SmartScreen prompts are visible only to the user. Ask them to
  click/look, then confirm the effect yourself (powercfg, log, database).
- Use a project-local venv. Do not install Python packages globally.
- Commit fixes on a branch (e.g. `windows-fixes`), not directly on `main`. Ask before pushing.
- Don't change macOS behaviour. Windows-specific code lives in `awake/platforms/windows.py`.

## Useful facts

| Thing | Where |
|---|---|
| Power backend | `awake/platforms/windows.py` → `WindowsPowerBackend` (worker thread calling `SetThreadExecutionState`) |
| Autostart | `WindowsAutostart`, value `Awake` under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` |
| Data + log | `%APPDATA%\Awake\awake.db`, `%APPDATA%\Awake\logs\awake.log` (override with `AWAKE_DATA_DIR`) |
| Log format | `event=<name> key=value …` — grep for `event=` |
| Startup time | logged as `event=startup_complete seconds=…` |
| Perf script | `scripts/measure_performance.py <pid> [seconds]` |
| Build | `scripts\build_windows.bat` (Inno Setup 6 for the installer) |

Expected `powercfg /requests` while a session runs (system only):

```
DISPLAY:
None.

SYSTEM:
[PROCESS] \Device\HarddiskVolumeN\...\Awake.exe     (python.exe when run from source)
```

## Steps

### 1. Environment
```powershell
git clone https://github.com/vampirepapi/awake.git; cd awake
py -3.12 --version            # install with: winget install Python.Python.3.12
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
```
Record: Windows edition + build (`winver` / `[Environment]::OSVersion`), CPU, whether it's a laptop on battery.

### 2. Automated tests
```powershell
.venv\Scripts\python -m pytest -v -rs
```
Expect all pass, macOS-only tests skipped, **no** Windows native test skipped (if they skip, the shell isn't elevated).

### 3. From source: power behaviour
Launch `.venv\Scripts\python main.py` (in a separate terminal or `Start-Process`). Ask the user to click the
power button, then check each state with `powercfg /requests` and the log:
1. Started → process under SYSTEM, not DISPLAY.
2. "Keep display awake" on → also under DISPLAY. Off → removed from DISPLAY only.
3. Stopped → gone from both.
4. Start again, then **Quit Awake** from the tray → gone; log ends `event=exit code=0`.
5. Start again, then kill hard: `Stop-Process -Id <pid> -Force` → gone from powercfg (Windows drops a dead
   thread's state). Relaunch → that session shows as *Interrupted* (`event=stale_sessions_recovered`).

### 4. Installer build and install
```powershell
winget install JRSoftware.InnoSetup     # if missing
.\scripts\build_windows.bat
```
Or download `Awake-<version>-Setup.exe` from https://github.com/vampirepapi/awake/releases/latest.
Install it (should not ask for admin; installs to `%LOCALAPPDATA%\Programs\Awake`). Note whether
SmartScreen appears. Repeat step 3 with the installed `Awake.exe` — powercfg should now show `Awake.exe`.

### 5. Desktop experience (user clicks, you verify)
- Tray icon appears (may be under the `^` overflow). Left-click opens the window; right-click shows the menu
  with status, Start/Stop, 4 durations, 2 power toggles, Open, Settings, Quit.
- Menu actions change state (verify via powercfg/log): duration, toggles, Start/Stop.
- Closing the window keeps the tray icon and the process; first close shows a "still running" notification.
- Second launch of `Awake.exe` brings the existing window forward instead of starting another process
  (`event=already_running_handoff` in the log; only one Awake.exe in Task Manager).
- Notifications appear for start/stop/expiry when enabled in Settings.
- Light/dark theme toggle works; text readable in both; tray icon visible on light and dark taskbars.
- DPI: check at 100% and 150%+ scaling — nothing clipped or blurry.

### 6. Launch at startup
Enable it in Settings, then:
```powershell
Get-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\Run | Select-Object Awake
```
Value should be `"…\Awake.exe" "--autostart"`. Ideally sign out/in and confirm Awake starts
(`launched_at_login=True` in the log). Disable it → value removed. Uninstall → value removed too.

### 7. Real idle test
Settings › System › Power & battery › Screen and sleep: screen off after 1 min, sleep after 2–3 min (note the
user's original values and restore them afterwards). Start a session with only "Keep system awake", user leaves
the PC alone ~5 min. Expected: screen turns off, PC does **not** sleep. Evidence: Awake still running with an
unbroken log, and `powercfg /systemsleepdiagnostics` or the System event log (Kernel-Power events 42/107) shows no
sleep in that window. Repeat once without Awake to show the PC *does* sleep, so the result is attributable.

### 7b. Modern Standby on battery
Check `powercfg /a` — if it lists "Standby (S0 Low Power Idle)" this is a Modern Standby device. Microsoft documents
that on battery, system power requests are ended 5 minutes after the sleep timeout
(https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powersetrequest). Repeat the idle test on
battery and record whether Awake's request survives past sleep-timeout + 5 min. Report the result either way; it
decides what the README must tell Windows laptop users.

### 7c. AI-agent detection (new in 0.2)
Install Claude Code on the PC if it isn't already (`claude --version`). Then:
1. `claude agents --json` in a terminal. (Already confirmed on GitHub's Windows runner with Claude Code 2.1.316 — exit 0, JSON array — via `scripts/probe_claude_agents.py`; re-check on the real PC and note the version.)
2. With Awake running and agent detection on, give a Claude Code session a task that takes > 1 minute. Within ~20 s the
   dashboard should read "Auto · Claude Code working" and `powercfg /requests` should list Awake under SYSTEM.
3. When the task finishes, Awake should release about 60 s later (log `event=agent_hold_released reason=idle`).
4. Turn "Keep awake for AI agents" off mid-task → released immediately.

### 7d. Codex and Cursor in AI mode (new in 0.3)
- Codex: run a Codex CLI task > 1 minute → Awake shows "Codex working" within ~20 s; releases ~60 s after it finishes.
  Session files are under `%USERPROFILE%\.codex\sessions\…`.
- Cursor: Settings › AI agents › Cursor › Connect, restart Cursor, run an agent task. **The Windows hook is inline
  PowerShell and has never run** — check `%USERPROFILE%\.cursor\hooks.json`, that prompts still submit normally, and
  that `%APPDATA%\Awake\agent-state\cursor\` gets a file containing only `busy …`/`idle …`.

### 8. Performance
With a session active and the window hidden:
```powershell
.venv\Scripts\python scripts\measure_performance.py (Get-Process Awake).Id 30
```
Targets: CPU < 1 %, memory < 100 MB, startup < 2 s (from the log).

### 9. Uninstall
Uninstall from Settings › Apps. Confirm the app folder and the Run key are gone. (`%APPDATA%\Awake` data is
intentionally kept.)

## Report back

A short table: each step → pass / fail / not checked, with the evidence (powercfg excerpts, log lines,
numbers). For failures: what happened, the log excerpt, and the fix (branch + commit). Update the Windows
column of the "Verification status" table in `README.md` and the Windows section of `docs/TESTING.md`
to match what was actually verified.
