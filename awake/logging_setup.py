"""Application logging.

Log lines are structured as ``key=value`` pairs after a short event name so they
are easy to grep and parse, e.g.::

    2026-11-01 10:31:02,114 INFO awake.session event=session_started id=... duration=3600

Only operational metadata is logged — never user content.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from awake.paths import log_dir

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def kv(event: str, **fields: object) -> str:
    """Format a structured log message: ``event=<event> k=v ...``."""
    parts = [f"event={event}"]
    for key, value in fields.items():
        text = str(value)
        if " " in text or not text:
            text = repr(text)
        parts.append(f"{key}={text}")
    return " ".join(parts)


def configure_logging(level: int = logging.INFO, directory: Path | None = None) -> Path:
    """Configure root logging to a rotating file plus stderr. Returns the log file path."""
    target_dir = directory or log_dir()
    log_file = target_dir / "awake.log"

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT)

    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if sys.stderr is not None:  # windowed PyInstaller builds have no stderr
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    return log_file
