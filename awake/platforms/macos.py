"""macOS integration: IOKit power assertions, login item and Dock visibility.

Power management uses ``IOPMAssertionCreateWithName`` / ``IOPMAssertionRelease``
from IOKit (see ``IOKit/pwr_mgt/IOPMLib.h``) through ctypes. Compared with
spawning ``caffeinate``:

* no child process can be orphaned — the kernel releases every assertion a
  process holds when that process exits, including on crashes or SIGKILL;
* the assertion is named "Awake" in ``pmset -g assertions`` and Activity Monitor;
* failures are reported synchronously as an ``IOReturn`` code.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import plistlib
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from awake import APP_NAME, BUNDLE_ID
from awake.core.power_manager import PowerBackend, PowerError
from awake.database.models import PowerState
from awake.logging_setup import kv

log = logging.getLogger("awake.platform.macos")

_IOKIT_PATH = "/System/Library/Frameworks/IOKit.framework/IOKit"
_CF_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

K_IOPM_ASSERTION_LEVEL_ON = 255          # kIOPMAssertionLevelOn
K_IORETURN_SUCCESS = 0                   # kIOReturnSuccess
K_CFSTRING_ENCODING_UTF8 = 0x08000100    # kCFStringEncodingUTF8

ASSERT_SYSTEM = "PreventUserIdleSystemSleep"    # kIOPMAssertionTypePreventUserIdleSystemSleep
ASSERT_DISPLAY = "PreventUserIdleDisplaySleep"  # kIOPMAssertionTypePreventUserIdleDisplaySleep


class _NativeLibs:
    """Loaded IOKit + CoreFoundation with explicit ctypes signatures."""

    def __init__(self) -> None:
        self.iokit = ctypes.cdll.LoadLibrary(_IOKIT_PATH)
        self.cf = ctypes.cdll.LoadLibrary(_CF_PATH)

        self.cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        self.cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        self.cf.CFRelease.restype = None
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]

        self.iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int32
        self.iokit.IOPMAssertionCreateWithName.argtypes = [
            ctypes.c_void_p,                    # CFStringRef AssertionType
            ctypes.c_uint32,                    # IOPMAssertionLevel
            ctypes.c_void_p,                    # CFStringRef AssertionName
            ctypes.POINTER(ctypes.c_uint32),    # IOPMAssertionID *
        ]
        self.iokit.IOPMAssertionRelease.restype = ctypes.c_int32
        self.iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]

    def create_assertion(self, assertion_type: str, name: str) -> tuple[int, int]:
        """Returns ``(IOReturn, assertion_id)``."""
        cf_type = self.cf.CFStringCreateWithCString(None, assertion_type.encode(), K_CFSTRING_ENCODING_UTF8)
        cf_name = self.cf.CFStringCreateWithCString(None, name.encode(), K_CFSTRING_ENCODING_UTF8)
        if not cf_type or not cf_name:
            for ref in (cf_type, cf_name):
                if ref:
                    self.cf.CFRelease(ref)
            return -1, 0
        try:
            assertion_id = ctypes.c_uint32(0)
            result = self.iokit.IOPMAssertionCreateWithName(
                cf_type, K_IOPM_ASSERTION_LEVEL_ON, cf_name, ctypes.byref(assertion_id)
            )
            return int(result), int(assertion_id.value)
        finally:
            self.cf.CFRelease(cf_type)
            self.cf.CFRelease(cf_name)

    def release_assertion(self, assertion_id: int) -> int:
        return int(self.iokit.IOPMAssertionRelease(assertion_id))


class MacPowerBackend(PowerBackend):
    """Holds at most one system and one display assertion."""

    name = "macos-iokit"

    def __init__(self, libs: Any | None = None, assertion_name: str = APP_NAME) -> None:
        self._libs = libs if libs is not None else _NativeLibs()
        self._assertion_name = assertion_name
        self._ids: dict[str, int] = {}
        self._lock = threading.Lock()

    def _create(self, assertion_type: str) -> None:
        with self._lock:
            if assertion_type in self._ids:
                return  # already held — never create duplicates
            result, assertion_id = self._libs.create_assertion(assertion_type, self._assertion_name)
            if result != K_IORETURN_SUCCESS or assertion_id == 0:
                log.error(kv("iokit_assertion_failed", type=assertion_type, ioreturn=hex(result & 0xFFFFFFFF)))
                raise PowerError(f"macOS refused the {assertion_type} request (IOReturn {result:#x}).")
            self._ids[assertion_type] = assertion_id
            log.info(kv("iokit_assertion_created", type=assertion_type, id=assertion_id))

    def _release(self, assertion_type: str) -> None:
        with self._lock:
            assertion_id = self._ids.pop(assertion_type, None)
            if assertion_id is None:
                return
            result = self._libs.release_assertion(assertion_id)
            if result != K_IORETURN_SUCCESS:
                # The id is forgotten either way; the kernel will drop it at process exit.
                log.error(kv("iokit_release_failed", type=assertion_type, id=assertion_id, ioreturn=result))
                raise PowerError(f"macOS could not release the {assertion_type} assertion (IOReturn {result:#x}).")
            log.info(kv("iokit_assertion_released", type=assertion_type, id=assertion_id))

    def prevent_system_sleep(self) -> None:
        self._create(ASSERT_SYSTEM)

    def allow_system_sleep(self) -> None:
        self._release(ASSERT_SYSTEM)

    def prevent_display_sleep(self) -> None:
        self._create(ASSERT_DISPLAY)

    def allow_display_sleep(self) -> None:
        self._release(ASSERT_DISPLAY)

    def release_all(self) -> None:
        errors: list[PowerError] = []
        for assertion_type in (ASSERT_DISPLAY, ASSERT_SYSTEM):
            try:
                self._release(assertion_type)
            except PowerError as exc:
                errors.append(exc)
        if errors:
            raise errors[0]

    def get_status(self) -> PowerState:
        with self._lock:
            display = ASSERT_DISPLAY in self._ids
            return PowerState(
                system_awake=ASSERT_SYSTEM in self._ids or display,
                display_awake=display,
                backend=self.name,
            )


# --------------------------------------------------------------------------- login item


class MacAutostart:
    """Launch at login through a per-user LaunchAgent (no admin rights needed)."""

    def __init__(self, agents_dir: Path | None = None) -> None:
        self._dir = agents_dir or Path.home() / "Library" / "LaunchAgents"
        self.label = f"{BUNDLE_ID}.launcher"

    @property
    def plist_path(self) -> Path:
        return self._dir / f"{self.label}.plist"

    def is_enabled(self) -> bool:
        return self.plist_path.exists()

    def set_enabled(self, enabled: bool, program_arguments: list[str]) -> None:
        if enabled:
            self._dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "Label": self.label,
                "ProgramArguments": program_arguments,
                "RunAtLoad": True,
                "ProcessType": "Interactive",
            }
            with self.plist_path.open("wb") as fh:
                plistlib.dump(payload, fh)
            log.info(kv("autostart_enabled", path=self.plist_path))
        elif self.plist_path.exists():
            self.plist_path.unlink()
            log.info(kv("autostart_disabled", path=self.plist_path))


def launch_command(extra_args: list[str]) -> list[str]:
    """Command that relaunches this app (packaged executable or ``python main.py``)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *extra_args]
    main_py = Path(__file__).resolve().parents[2] / "main.py"
    return [sys.executable, str(main_py), *extra_args]


# --------------------------------------------------------------------------- Dock icon


class DockController:
    """Shows the Dock icon only while the main window is open.

    Calls ``[NSApp setActivationPolicy:]`` through the Objective-C runtime so no
    PyObjC dependency is needed. Every failure is logged and leaves the Dock
    icon visible (the safe default).
    """

    POLICY_REGULAR = 0     # NSApplicationActivationPolicyRegular
    POLICY_ACCESSORY = 1   # NSApplicationActivationPolicyAccessory

    def __init__(self) -> None:
        self._objc: Any = None
        try:
            objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib")
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            self._objc = objc
        except OSError as exc:
            log.warning(kv("objc_runtime_unavailable", error=exc))

    def _send(self, receiver: int, selector: str, *args: tuple[Any, Any]) -> int | None:
        # objc_msgSend must be called with an exact (non-variadic) prototype on arm64.
        msg = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, *[t for t, _ in args])(
            ("objc_msgSend", self._objc)
        )
        return msg(receiver, self._objc.sel_registerName(selector.encode()), *[v for _, v in args])

    def set_visible(self, visible: bool) -> None:
        if self._objc is None:
            return
        try:
            ns_app_cls = self._objc.objc_getClass(b"NSApplication")
            ns_app = self._send(ns_app_cls, "sharedApplication")
            if not ns_app:
                return
            policy = self.POLICY_REGULAR if visible else self.POLICY_ACCESSORY
            self._send(ns_app, "setActivationPolicy:", (ctypes.c_long, policy))
            if visible:
                self._send(ns_app, "activateIgnoringOtherApps:", (ctypes.c_bool, True))
        except Exception as exc:  # noqa: BLE001 - cosmetic feature; must never crash the app
            log.warning(kv("dock_policy_failed", visible=visible, error=exc))


class StatusItemWidener:
    """Lets the Qt menu-bar item grow to fit a wide image (icon + countdown).

    Qt creates its NSStatusItem with ``NSSquareStatusItemLength`` (-2), so any
    image wider than it is tall is shrunk to fit a square — a "☀ 1h 59m" image
    ends up about a third of its size. Setting ``length`` to
    ``NSVariableStatusItemLength`` (-1) makes the item as wide as its image, like
    the system clock.

    Qt does not expose its NSStatusItem, so it is found through this app's
    ``NSStatusBarWindow`` and its ``statusItem`` accessor. That accessor is not
    public API: every step is checked, and :meth:`apply` returns False when it
    is unavailable so callers can fall back to a square icon.
    """

    SQUARE_LENGTH = -2.0     # NSSquareStatusItemLength
    VARIABLE_LENGTH = -1.0   # NSVariableStatusItemLength

    def __init__(self) -> None:
        self._objc: Any = None
        self._warned = False
        try:
            objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib")
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.object_getClassName.restype = ctypes.c_char_p
            objc.object_getClassName.argtypes = [ctypes.c_void_p]
            self._objc = objc
        except OSError as exc:
            log.warning(kv("objc_runtime_unavailable", error=exc))

    def _send(self, restype: Any, receiver: int, selector: bytes, *args: tuple[Any, Any]) -> Any:
        fn = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *[t for t, _ in args])(
            ("objc_msgSend", self._objc)
        )
        return fn(receiver, self._objc.sel_registerName(selector), *[v for _, v in args])

    def _responds(self, obj: int, selector: bytes) -> bool:
        sel = self._objc.sel_registerName(selector)
        return bool(self._send(ctypes.c_bool, obj, b"respondsToSelector:", (ctypes.c_void_p, sel)))

    def apply(self) -> bool:
        """Make every square status item of this app variable-width. True if done."""
        if self._objc is None:
            return False
        try:
            ns_app = self._send(ctypes.c_void_p, self._objc.objc_getClass(b"NSApplication"), b"sharedApplication")
            windows = self._send(ctypes.c_void_p, ns_app, b"windows") if ns_app else None
            if not windows:
                return False
            found = False
            for index in range(self._send(ctypes.c_ulong, windows, b"count")):
                window = self._send(ctypes.c_void_p, windows, b"objectAtIndex:", (ctypes.c_ulong, index))
                if not window or b"StatusBarWindow" not in (self._objc.object_getClassName(window) or b""):
                    continue
                if not self._responds(window, b"statusItem"):
                    continue
                item = self._send(ctypes.c_void_p, window, b"statusItem")
                if not item or not self._responds(item, b"setLength:"):
                    continue
                found = True
                if self._send(ctypes.c_double, item, b"length") == self.SQUARE_LENGTH:
                    self._send(None, item, b"setLength:", (ctypes.c_double, self.VARIABLE_LENGTH))
                    log.info(kv("status_item_variable_width"))
            if not found and not self._warned:
                self._warned = True
                log.warning(kv("status_item_not_found", note="menu-bar countdown disabled"))
            return found
        except Exception as exc:  # noqa: BLE001 - cosmetic; never crash the app over it
            if not self._warned:
                self._warned = True
                log.warning(kv("status_item_widen_failed", error=exc))
            return False


def assertions_report() -> str:
    """Return ``pmset -g assertions`` output (diagnostics / manual verification)."""
    try:
        return subprocess.run(
            ["pmset", "-g", "assertions"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return f"pmset unavailable: {exc}"
