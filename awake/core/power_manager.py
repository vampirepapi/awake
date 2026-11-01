"""Platform-independent power management.

Two layers:

``PowerBackend``
    The native adapter interface (one implementation per OS, see
    ``awake.platforms``). It holds at most one system-sleep and one
    display-sleep request on behalf of the whole process.

``PowerManager``
    Reference-counts *owners* on top of a backend. Each owner (today only the
    SessionManager; later e.g. an AgentMonitor) declares what it needs, and the
    manager applies the union of all requests. Releasing one owner can therefore
    never drop a requirement another owner still holds.

Neither layer knows anything about sessions, timers or the UI.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

from awake.database.models import PowerState
from awake.logging_setup import kv

log = logging.getLogger("awake.power")


class PowerError(RuntimeError):
    """A native power-management call failed. Awake must not report itself active."""


class PowerBackend(ABC):
    """Native power adapter.

    Contract:
    * ``prevent_*`` raise :class:`PowerError` on failure and are idempotent.
    * ``allow_*`` release only what this backend acquired, and are idempotent.
    * Keeping the display awake implies keeping the system awake on both
      supported operating systems — the OS cannot idle-sleep while it is forced
      to keep the display on. The reverse is not true: system-only requests let
      the display turn off.
    """

    name: str = "abstract"

    @abstractmethod
    def prevent_system_sleep(self) -> None: ...

    @abstractmethod
    def allow_system_sleep(self) -> None: ...

    @abstractmethod
    def prevent_display_sleep(self) -> None: ...

    @abstractmethod
    def allow_display_sleep(self) -> None: ...

    @abstractmethod
    def release_all(self) -> None: ...

    @abstractmethod
    def get_status(self) -> PowerState: ...


@dataclass(frozen=True, slots=True)
class PowerRequest:
    system: bool = False
    display: bool = False

    def union(self, other: "PowerRequest") -> "PowerRequest":
        return PowerRequest(self.system or other.system, self.display or other.display)


class PowerManager:
    """Applies the union of all owners' power requests to a backend."""

    def __init__(self, backend: PowerBackend) -> None:
        self._backend = backend
        self._owners: dict[str, PowerRequest] = {}
        self._applied = PowerRequest()
        self._lock = threading.RLock()

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def owners(self) -> dict[str, PowerRequest]:
        with self._lock:
            return dict(self._owners)

    def status(self) -> PowerState:
        return self._backend.get_status()

    def acquire(self, owner: str, *, system: bool, display: bool) -> None:
        """Create or replace ``owner``'s request. Raises PowerError if it cannot be honoured.

        On failure the previous state (for every owner) is restored.
        """
        if not (system or display):
            raise ValueError("A power request must keep the system or the display awake.")
        with self._lock:
            updated = {**self._owners, owner: PowerRequest(system, display)}
            self._apply(self._union(updated))
            self._owners = updated
            log.info(kv("power_acquired", owner=owner, system=system, display=display))

    def release(self, owner: str) -> None:
        """Drop ``owner``'s request. Other owners keep theirs."""
        with self._lock:
            if owner not in self._owners:
                return
            remaining = {k: v for k, v in self._owners.items() if k != owner}
            # Forget the owner even if the native release fails: we must never
            # keep claiming a request on its behalf.
            self._owners = remaining
            self._apply(self._union(remaining))
            log.info(kv("power_released", owner=owner))

    def release_all(self) -> None:
        """Release every request (used on application shutdown)."""
        with self._lock:
            self._owners.clear()
            try:
                self._backend.release_all()
            finally:
                self._applied = PowerRequest()
            log.info(kv("power_released_all", backend=self._backend.name))

    @staticmethod
    def _union(owners: dict[str, PowerRequest]) -> PowerRequest:
        result = PowerRequest()
        for request in owners.values():
            result = result.union(request)
        return result

    def _apply(self, target: PowerRequest) -> None:
        """Move the backend from ``self._applied`` to ``target``, rolling back on failure."""
        current = self._applied
        done: list[str] = []
        try:
            # Acquire new requests before releasing old ones so there is never a
            # gap in which the machine could fall asleep.
            if target.system and not current.system:
                self._backend.prevent_system_sleep()
                done.append("system")
            if target.display and not current.display:
                self._backend.prevent_display_sleep()
                done.append("display")
        except PowerError:
            log.error(kv("power_acquire_failed", backend=self._backend.name, target=target))
            for kind in reversed(done):
                self._safe_allow(kind)
            raise

        if current.display and not target.display:
            self._backend.allow_display_sleep()
        if current.system and not target.system:
            self._backend.allow_system_sleep()
        self._applied = target

    def _safe_allow(self, kind: str) -> None:
        try:
            if kind == "system":
                self._backend.allow_system_sleep()
            else:
                self._backend.allow_display_sleep()
        except PowerError as exc:
            log.error(kv("power_rollback_failed", kind=kind, error=exc))
