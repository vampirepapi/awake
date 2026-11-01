"""Filesystem locations used by Awake.

Kept free of Qt imports so the database layer and tests can use it directly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from awake import APP_NAME

_PACKAGE_DIR = Path(__file__).resolve().parent


def app_data_dir() -> Path:
    """Return (and create) the per-user application-data directory.

    macOS:   ~/Library/Application Support/Awake
    Windows: %APPDATA%\\Awake

    ``AWAKE_DATA_DIR`` overrides the location (used by tests and for
    portable/diagnostic runs).
    """
    override = os.environ.get("AWAKE_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        base = Path(appdata) / APP_NAME
    else:  # Linux is out of scope for v0.1 but keep development possible.
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        base = Path(xdg) / APP_NAME.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def database_path() -> Path:
    return app_data_dir() / "awake.db"


def log_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = app_data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def asset_path(*parts: str) -> Path:
    """Path to a bundled asset (works from source and from PyInstaller builds)."""
    return _PACKAGE_DIR.joinpath("assets", *parts)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
