"""Application bootstrap: wires the database, core services, platform layer and UI.

Shutdown safety net (any of these releases native power requests):
1. Quit Awake / Cmd+Q / SIGINT / SIGTERM  →  ``QApplication.aboutToQuit``  →  ``shutdown()``.
2. ``atexit`` handler (interpreter exit without a Qt quit).
3. Process death of any kind: macOS drops IOKit assertions of dead processes;
   Windows drops a thread's execution state when the thread/process ends.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import logging
import signal
import socket
import sys
import time
import traceback
from types import TracebackType

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSocketNotifier, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from awake import APP_NAME, BUNDLE_ID, __version__
from awake.core.agents import AgentCoordinator, AgentMonitor
from awake.core.agents.discovery import battery_state
from awake.core.application_state import ApplicationState
from awake.core.power_manager import PowerManager
from awake.core.session_manager import SessionManager
from awake.core.settings_manager import SettingsManager
from awake.database import Database, DatabaseError, SessionRepository, SettingsRepository
from awake.logging_setup import configure_logging, kv
from awake.paths import database_path
from awake.platforms import AUTOSTART_FLAG, autostart_command, create_autostart, create_power_backend

log = logging.getLogger("awake.app")

def _instance_key() -> str:
    """One running Awake per data folder: a separate ``AWAKE_DATA_DIR`` (portable / test
    installs) gets its own instance instead of handing off to the main one."""
    override = os.environ.get("AWAKE_DATA_DIR")
    if not override:
        return f"{BUNDLE_ID}.instance"
    digest = hashlib.sha1(os.path.abspath(override).encode("utf-8")).hexdigest()[:10]
    return f"{BUNDLE_ID}.instance.{digest}"


class _QuitWatcher(QObject):
    """Notices application-level quit requests (Cmd+Q, Dock → Quit) before windows close."""

    def __init__(self, on_quit) -> None:  # noqa: ANN001
        super().__init__()
        self._on_quit = on_quit

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Quit:
            self._on_quit()
        return False


class SingleInstance:
    """Ensures one running Awake per user; a second launch just shows the first window."""

    def __init__(self, key: str | None = None) -> None:
        self._key = key or _instance_key()
        self._server: QLocalServer | None = None

    def notify_existing(self) -> bool:
        socket_ = QLocalSocket()
        socket_.connectToServer(self._key)
        if socket_.waitForConnected(300):
            socket_.write(b"show")
            socket_.waitForBytesWritten(300)
            socket_.disconnectFromServer()
            return True
        return False

    def listen(self, on_message) -> None:  # noqa: ANN001
        QLocalServer.removeServer(self._key)  # clear a stale socket left by a crash
        self._server = QLocalServer()
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self._server.listen(self._key):
            log.warning(kv("single_instance_listen_failed", error=self._server.errorString()))
            return
        self._server.newConnection.connect(lambda: self._accept(on_message))

    def _accept(self, on_message) -> None:  # noqa: ANN001
        assert self._server is not None
        conn = self._server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: (on_message(bytes(conn.readAll()).decode(errors="ignore")),
                                            conn.disconnectFromServer()))


class AwakeApplication:
    def __init__(self, argv: list[str]) -> None:
        self.started_at = time.perf_counter()
        self.argv = argv
        self.qt = QApplication(argv)
        self.qt.setApplicationName(APP_NAME)
        self.qt.setApplicationDisplayName(APP_NAME)
        self.qt.setApplicationVersion(__version__)
        self.qt.setOrganizationName(APP_NAME)
        self.qt.setQuitOnLastWindowClosed(False)
        self._shutdown_done = False
        self._signal_notifier: QSocketNotifier | None = None
        self.window = None
        self.tray = None
        self.dock = None

    # ------------------------------------------------------------------ startup

    def run(self) -> int:
        instance = SingleInstance()
        if instance.notify_existing():
            log.info(kv("already_running_handoff"))
            return 0

        try:
            self.db = Database(database_path())
            self.session_repo = SessionRepository(self.db)
            self.session_repo.recover_stale()
            self.session_repo.prune()
        except DatabaseError as exc:
            log.critical(kv("startup_database_failed", error=exc))
            QMessageBox.critical(None, APP_NAME, f"Awake could not open its database and will quit.\n\n{exc}")
            return 1

        self.settings = SettingsManager(SettingsRepository(self.db))
        backend = create_power_backend()
        self.backend = backend
        self.power = PowerManager(backend)
        self.sessions = SessionManager(self.power, self.session_repo)
        self.state = ApplicationState(self.sessions, self.settings)

        # AI-agent detection: its own power owner, independent of manual sessions.
        self.agent_coordinator = AgentCoordinator(self.power, battery_state)
        self.agent_coordinator.set_battery_guard(self.settings.get("agent_battery_guard"))
        self.agent_monitor = AgentMonitor()
        self.agent_monitor.snapshot_ready.connect(self.agent_coordinator.update)

        # UI imports are deferred so the core can be imported without widgets.
        from awake.ui import icons
        from awake.ui.main_window import MainWindow
        from awake.ui.styles.theme import theme_manager
        from awake.ui.tray import TrayController

        self.qt.setWindowIcon(icons.app_icon())
        themes = theme_manager()
        themes.set_dark(self.settings.get("dark_mode"))

        self.window = MainWindow(self.state, self.session_repo, backend.name, TrayController.is_available,
                                 self.agent_coordinator)
        self.window.apply_theme()
        themes.theme_changed.connect(self.window.apply_theme)

        if TrayController.is_available():
            self.tray = TrayController(self.state, self.show_window, self.quit, self.agent_coordinator)
            self.window.on_hidden_to_tray = self._on_hidden_to_tray
        else:
            log.warning(kv("tray_unavailable", note="closing the window will quit Awake"))

        if sys.platform == "darwin":
            from awake.platforms.macos import DockController

            self.dock = DockController()

        self.state.error.connect(self._show_error)
        self.agent_coordinator.error_occurred.connect(lambda msg: self._show_error("AI agents", msg))
        self.settings.changed.connect(self._on_setting_changed)
        self._sync_autostart()

        instance.listen(lambda _msg: self.show_window(None))
        self.qt.aboutToQuit.connect(self.shutdown)
        self._quit_watcher = _QuitWatcher(self.window.prepare_quit)
        self.qt.installEventFilter(self._quit_watcher)
        atexit.register(self.shutdown)
        self._install_signal_handlers()

        start_hidden = self.tray is not None and self.settings.get("start_minimized")
        if start_hidden:
            self._set_dock_visible(False)
        else:
            self.window.show()
        self.state.apply_startup_behaviour()
        self._sync_agent_detection()

        QTimer.singleShot(0, self._log_startup_complete)
        return self.qt.exec()

    def _log_startup_complete(self) -> None:
        log.info(kv("startup_complete", seconds=f"{time.perf_counter() - self.started_at:.3f}",
                    backend=self.backend.name, tray=self.tray is not None))

    # ------------------------------------------------------------------ window / dock

    def show_window(self, page: str | None) -> None:
        if self.window is None:
            return
        self._set_dock_visible(True)
        self.window.bring_to_front(page)

    def _on_hidden_to_tray(self) -> None:
        self._set_dock_visible(False)
        if self.tray is not None:
            self.tray.show_running_hint()

    def _set_dock_visible(self, visible: bool) -> None:
        if self.dock is not None:
            self.dock.set_visible(visible)

    # ------------------------------------------------------------------ settings side effects

    def _on_setting_changed(self, key: str, value: object) -> None:
        if key == "dark_mode":
            from awake.ui.styles.theme import theme_manager

            theme_manager().set_dark(bool(value))
        elif key == "launch_at_startup":
            self._sync_autostart()
        elif key == "agent_detection_enabled":
            self._sync_agent_detection()
        elif key == "agent_battery_guard":
            self.agent_coordinator.set_battery_guard(bool(value))

    def _sync_agent_detection(self) -> None:
        enabled = bool(self.settings.get("agent_detection_enabled"))
        self.agent_coordinator.set_enabled(enabled)
        if enabled:
            self.agent_monitor.start()
        else:
            self.agent_monitor.stop()

    def _sync_autostart(self) -> None:
        autostart = create_autostart()
        if autostart is None:
            return
        wanted = bool(self.settings.get("launch_at_startup"))
        try:
            if wanted:
                # Always rewrite so the path follows the app if it was moved/updated.
                autostart.set_enabled(True, autostart_command())
            elif autostart.is_enabled():
                autostart.set_enabled(False, [])
        except OSError as exc:
            log.error(kv("autostart_update_failed", enabled=wanted, error=exc))
            self._show_error("Launch at startup", f"Awake could not update the login item: {exc}")
            if wanted:
                self.settings.set("launch_at_startup", False)

    # ------------------------------------------------------------------ errors

    def _show_error(self, title: str, message: str) -> None:
        if self.window is not None and self.window.isVisible():
            QMessageBox.warning(self.window, title, message)
        elif self.tray is not None:
            self.tray.notify(title, message)
        else:
            QMessageBox.warning(None, title, message)

    # ------------------------------------------------------------------ quit / shutdown

    def quit(self) -> None:
        if self.window is not None:
            self.window.prepare_quit()
        self.qt.quit()

    def shutdown(self) -> None:
        """Stop sessions, release native requests, close the database. Idempotent."""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        log.info(kv("shutdown_started"))
        try:
            # Agents first: a late poll must not re-acquire after release_all().
            if hasattr(self, "agent_coordinator"):
                self.agent_coordinator.shutdown()
                self.agent_monitor.stop()
            if hasattr(self, "sessions"):
                self.sessions.shutdown()
            stop_worker = getattr(getattr(self, "backend", None), "shutdown", None)
            if callable(stop_worker):
                stop_worker()
        except Exception as exc:  # noqa: BLE001 - must still close the DB and log
            log.exception(kv("shutdown_error", error=exc))
        finally:
            if self.tray is not None:
                self.tray.hide()
            if hasattr(self, "db"):
                self.db.close()
            log.info(kv("shutdown_complete"))

    @staticmethod
    def _drain_signal_socket(sock: socket.socket) -> None:
        # The notifier only exists to wake the event loop so Python runs the
        # signal handler; the bytes themselves are meaningless.
        try:
            while sock.recv(64):
                pass
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as exc:
            log.warning(kv("signal_socket_read_failed", error=exc))

    def _install_signal_handlers(self) -> None:
        """Route SIGINT/SIGTERM into the Qt event loop without polling."""
        try:
            read_sock, write_sock = socket.socketpair()
            read_sock.setblocking(False)
            write_sock.setblocking(False)
            signal.set_wakeup_fd(write_sock.fileno())
            self._signal_sockets = (read_sock, write_sock)
            self._signal_notifier = QSocketNotifier(read_sock.fileno(), QSocketNotifier.Type.Read)
            self._signal_notifier.activated.connect(lambda *_: self._drain_signal_socket(read_sock))
        except (OSError, ValueError) as exc:
            log.warning(kv("signal_wakeup_unavailable", error=exc))

        def handle(signum: int, _frame: object) -> None:
            log.info(kv("signal_received", signal=signal.Signals(signum).name))
            self.quit()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handle)
            except (OSError, ValueError) as exc:
                log.warning(kv("signal_handler_failed", signal=sig, error=exc))


def _excepthook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
    log.critical(kv("unhandled_exception", type=exc_type.__name__, error=exc) + "\n"
                 + "".join(traceback.format_exception(exc_type, exc, tb)))
    app = QCoreApplication.instance()
    if isinstance(app, QApplication) and not QCoreApplication.closingDown():
        QMessageBox.critical(None, APP_NAME, f"An unexpected error occurred:\n\n{exc}\n\nDetails were written to the log.")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    log_file = configure_logging()
    sys.excepthook = _excepthook
    log.info(kv("startup", version=__version__, platform=sys.platform, python=sys.version.split()[0],
                launched_at_login=AUTOSTART_FLAG in argv, log=log_file))
    app = AwakeApplication(argv)
    code = app.run()
    app.shutdown()
    log.info(kv("exit", code=code))
    return code
