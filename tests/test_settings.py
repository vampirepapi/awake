from __future__ import annotations

import pytest

from awake.core.settings_manager import SETTINGS, SettingsManager
from awake.database import Database, SettingsRepository


def test_defaults(qapp, settings_repo: SettingsRepository) -> None:
    settings = SettingsManager(settings_repo)
    assert settings.get("close_to_tray") is True
    assert settings.get("keep_display_awake_default") is False
    assert settings.get("default_duration") == 3600
    assert settings.get("startup_behaviour") == "off"
    assert set(settings.as_dict()) == set(SETTINGS)


def test_settings_persist_across_restart(qapp, tmp_path) -> None:
    path = tmp_path / "awake.db"
    db = Database(path)
    settings = SettingsManager(SettingsRepository(db))
    settings.set("dark_mode", False)
    settings.set("default_duration", None)
    settings.set("startup_behaviour", "start")
    db.close()

    db = Database(path)
    reloaded = SettingsManager(SettingsRepository(db))
    assert reloaded.get("dark_mode") is False
    assert reloaded.get("default_duration") is None
    assert reloaded.get("startup_behaviour") == "start"
    db.close()


def test_change_signal_only_on_real_change(qapp, settings_repo) -> None:
    settings = SettingsManager(settings_repo)
    seen = []
    settings.changed.connect(lambda k, v: seen.append((k, v)))
    settings.set("notify_on_start", True)
    settings.set("notify_on_start", True)
    assert seen == [("notify_on_start", True)]


@pytest.mark.parametrize(
    ("key", "value"),
    [("dark_mode", "yes"), ("default_duration", 42), ("default_duration", True), ("startup_behaviour", "maybe")],
)
def test_invalid_values_rejected(qapp, settings_repo, key, value) -> None:
    settings = SettingsManager(settings_repo)
    with pytest.raises(ValueError):
        settings.set(key, value)


def test_unknown_key_rejected(qapp, settings_repo) -> None:
    with pytest.raises(KeyError):
        SettingsManager(settings_repo).set("telemetry", True)


def test_corrupt_stored_value_falls_back_to_default(qapp, settings_repo) -> None:
    settings_repo.save("dark_mode", "not json")
    settings_repo.save("default_duration", "999")
    settings = SettingsManager(settings_repo)
    assert settings.get("dark_mode") is True
    assert settings.get("default_duration") == 3600
