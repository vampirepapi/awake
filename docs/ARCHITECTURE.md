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

## Future: AI-agent awareness (not in v0.1)

```
AgentMonitor (Claude Code, Codex, OpenCode, Cursor detectors)
    │  activity started / finished
    ▼
ActivityCoordinator  ── policy: when to keep awake, grace periods, user overrides
    │
    ▼
SessionManager.start()/stop()      or     PowerManager.acquire("agent:<name>") directly
```

Nothing in `core/` or `platforms/` needs to change: an agent integration can either drive sessions through `SessionManager` (visible in history and UI) or hold its own `PowerManager` owner (independent of manual sessions — the union semantics already guarantee that stopping a manual session never drops an agent's request, which is covered by `test_stop_does_not_release_other_owners`). The power engine has no dependency on any agent code.
