"""Turns agent snapshots into a power request, with the rules that make it trustworthy.

* Holds a **system-only** request (the display may sleep) under its own
  PowerManager owner, so it never interferes with a manual session.
* **Grace period**: after the last busy session goes idle, keep holding for
  ``grace_seconds`` (default 60 s) so back-to-back turns don't flap.
* **Cap**: release after ``cap_seconds`` (default 4 h) of continuous holding, and
  stay released until every agent has been idle at least once.
* **Battery guard**: don't hold while on battery below ``battery_floor`` percent.
* A failed native request is reported once and retried on the next snapshot.

Deadlines use :class:`TimerManager` (wall-clock), so sleep/drift can't stretch them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, Signal

from awake.core.agents.models import AgentSession, AgentSnapshot
from awake.core.power_manager import PowerError, PowerManager
from awake.core.timer_manager import Clock, TimerManager
from awake.database.models import utcnow
from awake.logging_setup import kv

log = logging.getLogger("awake.agents")

POWER_OWNER = "agents"
DEFAULT_GRACE_SECONDS = 60
DEFAULT_CAP_SECONDS = 4 * 3600
DEFAULT_BATTERY_FLOOR = 20.0

BatteryProbe = Callable[[], "tuple[float, bool] | None"]


class AgentHoldState(StrEnum):
    DISABLED = "disabled"         # feature turned off
    IDLE = "idle"                 # no agent working; not holding
    HOLDING = "holding"           # an agent is working; holding the system awake
    GRACE = "grace"               # agents just finished; still holding briefly
    LOW_BATTERY = "low_battery"   # agent working, but battery guard prevents holding
    CAPPED = "capped"             # held for the maximum time; released until agents go idle
    ERROR = "error"               # the OS refused the request; will retry


@dataclass(frozen=True, slots=True)
class AgentActivity:
    state: AgentHoldState
    busy: tuple[AgentSession, ...] = ()
    snapshot: AgentSnapshot = AgentSnapshot()

    @property
    def holding(self) -> bool:
        return self.state in (AgentHoldState.HOLDING, AgentHoldState.GRACE)


class AgentCoordinator(QObject):
    changed = Signal(object)        # AgentActivity
    error_occurred = Signal(str)    # user-facing, once per failure streak

    def __init__(
        self,
        power: PowerManager,
        battery: BatteryProbe,
        clock: Clock = utcnow,
        grace_seconds: int = DEFAULT_GRACE_SECONDS,
        cap_seconds: int = DEFAULT_CAP_SECONDS,
        battery_floor: float = DEFAULT_BATTERY_FLOOR,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._power = power
        self._battery = battery
        self._clock = clock
        self._grace = grace_seconds
        self._cap = cap_seconds
        self._battery_floor = battery_floor
        self._battery_guard = True
        self._enabled = True
        self._holding = False
        self._capped = False
        self._error_reported = False
        self._shut_down = False
        self._snapshot = AgentSnapshot()
        self._state = AgentHoldState.IDLE
        self._grace_timer = TimerManager(clock=clock, parent=self)
        self._grace_timer.expired.connect(self._on_grace_expired)
        self._cap_timer = TimerManager(clock=clock, parent=self)
        self._cap_timer.expired.connect(self._on_cap_reached)

    # ------------------------------------------------------------------ queries

    @property
    def activity(self) -> AgentActivity:
        return AgentActivity(self._state, self._snapshot.busy_sessions, self._snapshot)

    @property
    def holding(self) -> bool:
        return self._holding

    # ------------------------------------------------------------------ configuration

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        log.info(kv("agent_detection_enabled" if enabled else "agent_detection_disabled"))
        if not enabled:
            self._grace_timer.cancel()
            self._cap_timer.cancel()
            self._capped = False
            self._release()
            self._snapshot = AgentSnapshot()
        self._evaluate()

    def set_battery_guard(self, enabled: bool) -> None:
        self._battery_guard = enabled
        self._evaluate()

    # ------------------------------------------------------------------ input

    def update(self, snapshot: AgentSnapshot) -> None:
        if self._shut_down:
            return
        self._snapshot = snapshot if self._enabled else AgentSnapshot()
        self._evaluate()

    def shutdown(self) -> None:
        """Stop for good and release. Call before SessionManager.shutdown()."""
        if self._shut_down:
            return
        self._shut_down = True
        self._grace_timer.cancel()
        self._cap_timer.cancel()
        self._release()

    # ------------------------------------------------------------------ rules

    def _evaluate(self) -> None:
        if self._shut_down:
            return
        if not self._enabled:
            self._set_state(AgentHoldState.DISABLED)
            return
        busy = self._snapshot.any_busy
        if not busy:
            self._capped = False  # every agent idle at least once → cap resets
            self._cap_timer.cancel()
            if self._holding and not self._grace_timer.is_running:
                self._grace_timer.schedule(self._now_plus(self._grace))
                log.info(kv("agent_grace_started", seconds=self._grace))
            self._set_state(AgentHoldState.GRACE if self._holding else AgentHoldState.IDLE)
            return

        self._grace_timer.cancel()
        if self._capped:
            self._set_state(AgentHoldState.CAPPED)
            return
        if self._battery_too_low():
            if self._holding:
                log.info(kv("agent_release_low_battery", floor=self._battery_floor))
            self._release()
            self._set_state(AgentHoldState.LOW_BATTERY)
            return
        if not self._holding:
            try:
                self._power.acquire(POWER_OWNER, system=True, display=False)
            except PowerError as exc:
                log.error(kv("agent_acquire_failed", error=exc))
                if not self._error_reported:
                    self._error_reported = True
                    self.error_occurred.emit(f"Awake couldn't keep your computer awake for the agent: {exc}")
                self._set_state(AgentHoldState.ERROR)
                return
            self._holding = True
            self._error_reported = False
            self._cap_timer.schedule(self._now_plus(self._cap))
            log.info(kv("agent_hold_started", busy=len(self._snapshot.busy_sessions)))
        self._set_state(AgentHoldState.HOLDING)

    def _on_grace_expired(self) -> None:
        if self._snapshot.any_busy:
            return
        log.info(kv("agent_hold_released", reason="idle"))
        self._release()
        self._set_state(AgentHoldState.IDLE)

    def _on_cap_reached(self) -> None:
        log.warning(kv("agent_hold_capped", seconds=self._cap))
        self._capped = True
        self._release()
        self._evaluate()

    def _battery_too_low(self) -> bool:
        if not self._battery_guard:
            return False
        state = self._battery()
        if state is None:
            return False
        percent, plugged = state
        return not plugged and percent < self._battery_floor

    def _release(self) -> None:
        self._grace_timer.cancel()
        if not self._holding:
            return
        self._holding = False
        self._cap_timer.cancel()
        try:
            self._power.release(POWER_OWNER)
        except PowerError as exc:
            log.error(kv("agent_release_failed", error=exc))

    def _now_plus(self, seconds: int) -> datetime:
        return self._clock() + timedelta(seconds=seconds)

    def _set_state(self, state: AgentHoldState) -> None:
        self._state = state
        self.changed.emit(self.activity)
