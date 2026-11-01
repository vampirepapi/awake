"""Typed, persistent application settings backed by the SQLite settings table."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Signal

from awake.database.database import DatabaseError
from awake.database.repository import SettingsRepository
from awake.logging_setup import kv

log = logging.getLogger("awake.settings")


@dataclass(frozen=True, slots=True)
class SettingSpec:
    default: Any
    choices: tuple[Any, ...] | None = None  # for enum-like settings

    def validate(self, value: Any) -> Any:
        if self.choices is not None:
            if value not in self.choices or isinstance(value, bool) != isinstance(self.default, bool):
                raise ValueError(f"must be one of {self.choices!r}")
            return value
        if isinstance(self.default, bool):
            if not isinstance(value, bool):
                raise ValueError("must be true or false")
            return value
        raise ValueError("unsupported setting type")  # pragma: no cover


class StartupBehaviour:
    STAY_OFF = "off"
    START_SESSION = "start"
    ALL = (STAY_OFF, START_SESSION)


SETTINGS: dict[str, SettingSpec] = {
    # General
    "launch_at_startup": SettingSpec(False),
    "start_minimized": SettingSpec(False),
    "close_to_tray": SettingSpec(True),
    "dark_mode": SettingSpec(True),
    # Power management
    "default_duration": SettingSpec(3600, choices=(1800, 3600, 7200, None)),
    "keep_system_awake_default": SettingSpec(True),
    "keep_display_awake_default": SettingSpec(False),
    "startup_behaviour": SettingSpec(StartupBehaviour.STAY_OFF, choices=StartupBehaviour.ALL),
    # Notifications
    "notify_on_start": SettingSpec(False),
    "notify_on_stop": SettingSpec(False),
    "notify_on_expire": SettingSpec(True),
}


class SettingsManager(QObject):
    changed = Signal(str, object)  # key, new value

    def __init__(self, repository: SettingsRepository, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repo = repository
        self._values: dict[str, Any] = {key: spec.default for key, spec in SETTINGS.items()}
        self._load()

    def _load(self) -> None:
        try:
            stored = self._repo.load_all()
        except DatabaseError as exc:
            log.error(kv("settings_load_failed", error=exc))
            return
        for key, raw in stored.items():
            spec = SETTINGS.get(key)
            if spec is None:
                continue  # setting from a newer/older version — ignore
            try:
                self._values[key] = spec.validate(json.loads(raw))
            except (ValueError, json.JSONDecodeError) as exc:
                log.warning(kv("settings_invalid_value_ignored", key=key, error=exc))

    def get(self, key: str) -> Any:
        if key not in SETTINGS:
            raise KeyError(key)
        return self._values[key]

    def set(self, key: str, value: Any) -> None:
        spec = SETTINGS.get(key)
        if spec is None:
            raise KeyError(key)
        try:
            value = spec.validate(value)
        except ValueError as exc:
            raise ValueError(f"Invalid value for setting {key!r}: {exc}") from exc
        if self._values[key] == value and type(self._values[key]) is type(value):
            return
        self._repo.save(key, json.dumps(value))  # DatabaseError propagates to the caller
        self._values[key] = value
        log.info(kv("setting_changed", key=key, value=value))
        self.changed.emit(key, value)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)
