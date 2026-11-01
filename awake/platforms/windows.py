"""Windows integration: SetThreadExecutionState and the HKCU Run key.

``SetThreadExecutionState`` state belongs to the calling thread and is dropped
when that thread exits. All calls are therefore made from one dedicated,
long-lived worker thread owned by :class:`WindowsPowerBackend`, independent of
which thread the UI or session manager runs on. No administrator rights are
required.

NOTE: this module is covered by mocked unit tests only. It has not been
verified on Windows hardware as part of v0.1 development — see README.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from awake import APP_NAME
from awake.core.power_manager import PowerBackend, PowerError
from awake.database.models import PowerState
from awake.logging_setup import kv

log = logging.getLogger("awake.platform.windows")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STOP = object()


def _load_kernel32() -> Any:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
    return kernel32


def flags_for(system: bool, display: bool) -> int:
    """Execution-state flags for a request. Display implies system."""
    flags = ES_CONTINUOUS
    if system or display:
        flags |= ES_SYSTEM_REQUIRED
    if display:
        flags |= ES_DISPLAY_REQUIRED
    return flags


class WindowsPowerBackend(PowerBackend):
    name = "windows-execution-state"

    def __init__(self, kernel32: Any | None = None, call_timeout: float = 5.0) -> None:
        self._kernel32 = kernel32 if kernel32 is not None else _load_kernel32()
        self._timeout = call_timeout
        self._system = False
        self._display = False
        self._lock = threading.Lock()
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread = threading.Thread(target=self._worker, name="awake-power", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ worker thread

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            flags, future = item
            try:
                previous = int(self._kernel32.SetThreadExecutionState(flags))
                # GetLastError is thread-local, so read it here on the worker thread.
                last_error = ctypes.get_last_error() if previous == 0 and hasattr(ctypes, "get_last_error") else 0
                future.set_result((previous, last_error))
            except BaseException as exc:  # noqa: BLE001 - forwarded to the caller
                future.set_exception(exc)

    def _call(self, flags: int) -> None:
        if not self._thread.is_alive():
            raise PowerError("The Windows power worker thread is not running.")
        future: Future[tuple[int, int]] = Future()
        self._queue.put((flags, future))
        try:
            previous, last_error = future.result(timeout=self._timeout)
        except Exception as exc:
            log.error(kv("execution_state_call_failed", flags=hex(flags), error=exc))
            raise PowerError(f"SetThreadExecutionState failed: {exc}") from exc
        if previous == 0:  # documented failure value
            log.error(kv("execution_state_rejected", flags=hex(flags), last_error=last_error))
            raise PowerError("Windows rejected the power request (SetThreadExecutionState returned NULL).")
        log.info(kv("execution_state_set", flags=hex(flags)))

    def _set(self, system: bool, display: bool) -> None:
        with self._lock:
            if (system, display) == (self._system, self._display):
                return  # no duplicate native calls
            self._call(flags_for(system, display))
            self._system, self._display = system, display

    # ------------------------------------------------------------------ PowerBackend

    def prevent_system_sleep(self) -> None:
        self._set(True, self._display)

    def allow_system_sleep(self) -> None:
        self._set(False, self._display)

    def prevent_display_sleep(self) -> None:
        self._set(self._system, True)

    def allow_display_sleep(self) -> None:
        self._set(self._system, False)

    def release_all(self) -> None:
        with self._lock:
            if self._system or self._display:
                try:
                    self._call(ES_CONTINUOUS)
                finally:
                    self._system = self._display = False

    def get_status(self) -> PowerState:
        with self._lock:
            return PowerState(self._system or self._display, self._display, backend=self.name)

    def shutdown(self) -> None:
        """Release requests and stop the worker thread."""
        try:
            self.release_all()
        finally:
            self._queue.put(_STOP)
            self._thread.join(timeout=self._timeout)


# --------------------------------------------------------------------------- autostart


class WindowsAutostart:
    """Launch at sign-in via ``HKCU\\...\\Run`` (per-user, no admin rights)."""

    def __init__(self, value_name: str = APP_NAME, winreg_module: Any | None = None) -> None:
        if winreg_module is None:
            import winreg as winreg_module  # type: ignore[no-redef]  # noqa: PLC0415
        self._winreg = winreg_module
        self._value_name = value_name

    def is_enabled(self) -> bool:
        wr = self._winreg
        try:
            with wr.OpenKey(wr.HKEY_CURRENT_USER, _RUN_KEY, 0, wr.KEY_READ) as key:
                wr.QueryValueEx(key, self._value_name)
                return True
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled: bool, program_arguments: list[str]) -> None:
        wr = self._winreg
        with wr.OpenKey(wr.HKEY_CURRENT_USER, _RUN_KEY, 0, wr.KEY_SET_VALUE) as key:
            if enabled:
                command = " ".join(f'"{part}"' for part in program_arguments)
                wr.SetValueEx(key, self._value_name, 0, wr.REG_SZ, command)
                log.info(kv("autostart_enabled", key=_RUN_KEY))
            else:
                try:
                    wr.DeleteValue(key, self._value_name)
                    log.info(kv("autostart_disabled", key=_RUN_KEY))
                except FileNotFoundError:
                    pass


def launch_command(extra_args: list[str]) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *extra_args]
    main_py = Path(__file__).resolve().parents[2] / "main.py"
    # pythonw.exe avoids a console window when launched at sign-in.
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return [str(pythonw if pythonw.exists() else exe), str(main_py), *extra_args]
