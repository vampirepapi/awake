# Architecture

```
main.py
awake/
├── app.py                 bootstrap: wiring, single instance, signals, autostart, shutdown
├── paths.py               app-data / log / asset locations
├── logging_setup.py       rotating file log, key=value events
├── core/                  business logic — no widgets
│   ├── power_manager.py   PowerBackend interface + owner-based PowerManager
│   ├── session_manager.py the only owner of session lifecycle
│   ├── timer_manager.py   wall-clock deadline timer (sleep/drift safe)
│   ├── settings_manager.py typed settings with validation
│   └── application_state.py shared UI selection (dashboard ⇄ tray) + error signal
├── platforms/             everything OS-specific
│   ├── __init__.py        factories: create_power_backend(), create_autostart()
│   ├── macos.py           IOKit assertions, LaunchAgent, Dock visibility
│   └── windows.py         SetThreadExecutionState worker thread, HKCU Run key
├── database/              SQLite: schema migrations, models, repositories
└── ui/                    Qt Widgets presentation only
    ├── main_window.py, dashboard.py, sessions_page.py, settings_page.py, about_page.py, tray.py
    ├── components/        power button, toggle, duration selector, status, session card
    └── styles/            design tokens + QSS template
```

## Layers and rules

1. **UI → ApplicationState → SessionManager → PowerManager → PowerBackend.** Each layer only calls the one below it. The UI never calls the power layer.
2. **Only `SessionManager` starts, changes or ends sessions.** It acquires power *before* persisting or emitting `session_started`; a `PowerError` propagates and nothing is recorded, so the UI cannot show a session that isn't real.
3. **`PowerManager` reference-counts owners.** `acquire(owner, system, display)` / `release(owner)` apply the union of all owners' requests to the backend. Acquisitions happen before releases (no gap), failures roll back, and one owner can never release another's requirement.
4. **Platform code is isolated** in `awake/platforms/`. Swapping or adding an OS means implementing `PowerBackend` (six methods) and an autostart object.
5. **Timers use deadlines, not tick counts.** `TimerManager` stores an absolute UTC deadline and re-checks at most every 60 s, so a monotonic clock pausing during sleep or lid-close cannot make sessions overrun.

## Lifecycle and cleanup

| Event | What happens |
|---|---|
| Start | validate → `PowerManager.acquire("session")` → insert row (`active`) → schedule deadline → `session_started` |
| Expire | deadline reached → `stop(EXPIRED)` → status `completed` |
| Stop / toggle | release → update row (`stopped`, `user_stopped`) → `session_ended` |
| Quit (menu, Cmd+Q, Dock, SIGINT/SIGTERM) | `aboutToQuit` → `SessionManager.shutdown()` → `stop(APP_QUIT)` + `PowerManager.release_all()` → DB closed |
| Crash / `kill -9` | OS releases the request (kernel-owned IOKit assertion / per-thread execution state); next launch marks the dangling row `interrupted` |
| Persistence failure | logged + `error_occurred` shown to the user; the power request is kept (staying awake is the primary job) |

## Database

`awake.db` in the app-data directory. Schema version in `PRAGMA user_version`; each migration runs in a transaction. All queries are parameterised; sort columns come from a whitelist. On startup: stale `active` rows → `interrupted`; finished sessions older than 365 days are pruned. No heartbeat writes while a session runs.

## AI-agent awareness

Implemented in 0.2 for Claude Code (`awake/core/agents/`); research and roadmap in [AGENT_DETECTION.md](AGENT_DETECTION.md).

```
AgentMonitor (every 20 s; only runs `claude agents --json` while a Claude process exists)
    │  AgentSnapshot: sessions with busy / waiting / idle
    ▼
AgentCoordinator — grace 60 s, cap 4 h, battery guard (< 20 % on battery), error retry
    │  PowerManager owner "agents" (system only; display may sleep)
    ▼
PowerManager  ◄── owner "session" (manual sessions, SessionManager)
```

Design decisions:

- **Agents get their own PowerManager owner instead of going through `SessionManager`.** Manual sessions stay
  single-active with an honest history, and the union semantics guarantee that stopping a manual session never drops
  an agent's request (and vice versa) — `test_agents_and_manual_session_never_release_each_other`.
- **The power button reflects manual sessions only**; title, status pill, sidebar and tray reflect the union
  ("Auto · Claude Code working"), so pressing Stop while an agent works never looks broken.
- **The `claude` command is taken from the running process** (`psutil` `exe()`), not `PATH`: a Dock-launched app has a
  minimal PATH, and this guarantees the matching version. It runs through `QProcess` (never blocking the UI) with
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, so polling causes no update checks or telemetry.
- **Failures back off** to 5 minutes and surface a one-line reason in the dashboard row.
- **Shutdown order**: `AgentCoordinator.shutdown()` runs before `SessionManager.shutdown()` so a late poll can't
  re-acquire after `release_all()`.
- **Agent stretches are not yet recorded in session history** (phase 2).
