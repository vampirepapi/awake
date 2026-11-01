from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Must be set before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("AWAKE_DATA_DIR", tempfile.mkdtemp(prefix="awake-tests-"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from awake.database import Database, SessionRepository, SettingsRepository  # noqa: E402

from tests.fakes import FakeBackend, FakeClock  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "awake.db")
    yield database
    database.close()


@pytest.fixture
def session_repo(db: Database) -> SessionRepository:
    return SessionRepository(db)


@pytest.fixture
def settings_repo(db: Database) -> SettingsRepository:
    return SettingsRepository(db)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
