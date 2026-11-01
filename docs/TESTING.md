# Testing

## Automated

```bash
.venv/bin/python -m pytest                 # everything; native tests skip on the other OS
.venv/bin/python -m pytest -m "not native" # mocked only (what packaging scripts run)
.venv/bin/python -m pytest -m native -v    # real OS power APIs
```

| File | Covers |
|---|---|
| `test_power_manager.py` | owner union, rollback, duplicate prevention, display independence; macOS IOKit adapter and Windows adapter against fake native libraries (exact flags, return-value handling, dedicated thread) |
| `test_sessions.py` | start/stop, expiry (fake clock), indefinite, duplicate start, invalid durations, power failure ⇒ no session, live option changes, shutdown, real `QTimer` expiry |
| `test_database.py` | init, migrations, newer-schema refusal, persistence across reopen, ordering, stale recovery, pruning, clear, parameterisation |
| `test_settings.py` | defaults, persistence across restart, validation, corrupt values |
| `test_ui.py` | offscreen widget wiring: button only shows ON after success, toggles, tray menu, close-to-tray, settings |
| `test_platform_integration.py` | LaunchAgent plist, HKCU Run key (fake `winreg`) |
| `test_native_macos.py` | **real** IOKit assertions checked with `pmset -g assertions` |
| `test_native_windows.py` | **real** `SetThreadExecutionState`, checked with `powercfg /requests` when elevated |

Mocked tests prove Awake calls the APIs correctly; they do not prove the OS honours the request. Only the native tests and the manual checks below do.

## Manual checks on real hardware

### macOS

1. Start a 30-minute session. `pmset -g assertions | grep Awake` → one `PreventUserIdleSystemSleep named: "Awake"`, **no** `PreventUserIdleDisplaySleep`.
2. Turn on *Keep display awake* → a `PreventUserIdleDisplaySleep` line appears; turn it off → it disappears, the system one stays.
3. Stop → no `Awake` lines remain.
4. With System Settings › Lock Screen › *Turn display off* set to 1 minute and a session active (display toggle off): the display sleeps, but `pmset -g log` shows no idle system sleep.
5. Quit from the menu bar, with Cmd+Q, and with `kill -9 <pid>` → no `Awake` assertions remain; relaunch shows the killed session as *Interrupted*.
6. Close the window → Awake stays in the menu bar and the Dock icon disappears; *Open Awake* brings both back.
7. Packaged app: notifications appear for start/stop/expiry when enabled.

**Status (2026-11-01, MacBook / macOS 26.5, arm64):** steps 1, 3 and 5 verified (from source and from the packaged `Awake.app`); display assertion add/remove verified via `test_native_macos.py`; step 6's Dock behaviour verified with `lsappinfo` (`UIElement` hidden → `Foreground` shown). Step 4, clicking through the real menu bar in step 6, and step 7 still need a person to run them.

### Windows 10 / 11 — not yet verified

1. Start a session. In an elevated terminal `powercfg /requests` → `Awake.exe` under **SYSTEM**, not under **DISPLAY**.
2. Toggle *Keep display awake* → appears under **DISPLAY**; off → removed.
3. Stop / Quit / End task in Task Manager → no `Awake.exe` entries remain.
4. With a 1-minute display timeout, the display turns off while the PC does not sleep.
5. Tray: left-click opens the window, right-click shows the menu; closing the window keeps the tray icon.
6. *Launch at startup* → `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Awake` exists; sign out and back in.
7. Performance: `python scripts\measure_performance.py <pid>`.
