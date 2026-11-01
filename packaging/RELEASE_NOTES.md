Awake keeps your computer from going to sleep while you work: one click, for 30 minutes, 1 hour, 2 hours or until you stop it. The display can still turn off. It lives in the menu bar / system tray, works offline and collects nothing.

## What's new in 0.2.0

- **AI mode: stays awake while Claude Code works.** Awake notices when a Claude Code session is busy (using Claude Code's own `claude agents --json` status) and keeps the system awake until it finishes, then lets your computer sleep normally about a minute later. The display can still turn off.
  - Pauses on battery below 20 %, and stops after 4 hours of continuous work.
  - Reads only each session's busy/idle status — never prompts, files or output.
  - On by default; turn it off from the dashboard, the menu bar, or Settings › AI agents.
  - While an agent keeps the system awake you'll see **AI mode** in the app, the menu (`AI mode — automatic`) and, on macOS, `☀ AI` in the menu bar.
- Manual sessions and AI mode are independent: stopping a manual session never stops AI mode, and vice versa.
- Status: verified live on macOS. On Windows the building blocks are verified on GitHub's Windows machines; a full real-world run on a Windows PC is still pending — reports welcome.

## Download

| Your computer | File |
|---|---|
| Mac with Apple silicon (M1/M2/M3/M4…) | `Awake-…-macOS-arm64.dmg` |
| Mac with Intel processor | `Awake-…-macOS-x86_64.dmg` |
| Windows 10 / 11 (installer, no admin needed) | `Awake-…-Setup.exe` |
| Windows 10 / 11 (portable, no install) | `Awake-…-Windows-x64-portable.zip` |

Not sure which Mac you have? Apple menu › About This Mac › *Chip* (Apple M…) or *Processor* (Intel).

## First launch: the app is not code-signed yet

These are early builds without a paid Apple / Microsoft signing certificate, so the OS will warn you once.

**macOS:** open the DMG and drag Awake to Applications. The first time, macOS says Awake "cannot be opened" or "is damaged". Go to System Settings › Privacy & Security, scroll down and click **Open Anyway**. If that button doesn't appear, run this once in Terminal:

```
xattr -dr com.apple.quarantine /Applications/Awake.app
```

**Windows:** if SmartScreen shows "Windows protected your PC", click **More info › Run anyway**.

## Status

- macOS: tested on real hardware (macOS 26, Apple silicon) — power requests confirmed with `pmset`, released on stop, quit and crash.
- Windows: automated tests, including real `SetThreadExecutionState` calls, pass on GitHub's Windows machines. Not yet tested by hand on a Windows 10/11 PC — reports welcome.

`SHA256SUMS.txt` lists checksums for every file.
