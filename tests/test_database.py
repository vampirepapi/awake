from __future__ import annotations

from datetime import timedelta

import pytest

from awake.database import Database, DatabaseError, EndReason, Session, SessionRepository, SessionStatus
from awake.database.database import SCHEMA_VERSION
from awake.database.models import new_session_id
from tests.fakes import FakeClock


def make_session(clock: FakeClock, **overrides) -> Session:
    data = dict(
        id=new_session_id(),
        started_at=clock.now,
        duration_seconds=3600,
        system_awake=True,
        display_awake=False,
    )
    data.update(overrides)
    return Session(**data)


def test_initialization_creates_schema(db: Database) -> None:
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sessions", "settings"} <= tables
    assert db.schema_version == SCHEMA_VERSION


def test_reopen_is_idempotent(tmp_path) -> None:
    path = tmp_path / "awake.db"
    Database(path).close()
    db = Database(path)
    assert db.schema_version == SCHEMA_VERSION
    db.close()


def test_newer_schema_is_refused(tmp_path) -> None:
    path = tmp_path / "awake.db"
    db = Database(path)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    db.close()
    with pytest.raises(DatabaseError):
        Database(path)


def test_session_round_trip(session_repo: SessionRepository, clock: FakeClock) -> None:
    session = make_session(clock, display_awake=True)
    session_repo.insert(session)
    assert session_repo.get(session.id) == session

    clock.advance(60)
    ended = session.with_changes(status=SessionStatus.STOPPED, ended_at=clock.now, end_reason=EndReason.USER_STOPPED)
    session_repo.update(ended)
    assert session_repo.get(session.id) == ended


def test_sessions_survive_reopen(tmp_path, clock: FakeClock) -> None:
    path = tmp_path / "awake.db"
    db = Database(path)
    session = make_session(clock, duration_seconds=None)
    SessionRepository(db).insert(session)
    db.close()
    db = Database(path)
    assert SessionRepository(db).get(session.id) == session
    db.close()


def test_history_retrieval_order_and_limits(session_repo: SessionRepository, clock: FakeClock) -> None:
    ids = []
    for minutes in (30, 60, 120, 15):
        s = make_session(clock, status=SessionStatus.COMPLETED, ended_at=clock.now + timedelta(minutes=minutes),
                         end_reason=EndReason.EXPIRED, duration_seconds=minutes * 60 if minutes != 15 else 1800)
        session_repo.insert(s)
        ids.append(s.id)
        clock.advance(3600)
    active = make_session(clock)
    session_repo.insert(active)

    recent = session_repo.recent(3)
    assert [s.id for s in recent] == [ids[3], ids[2], ids[1]]  # newest first, active excluded
    assert len(session_repo.all()) == 5
    assert [s.id for s in session_repo.all(descending=False)][0] == ids[0]
    with pytest.raises(ValueError):
        session_repo.all(sort="started_at; DROP TABLE sessions")  # type: ignore[arg-type]


def test_stale_active_sessions_are_recovered(session_repo: SessionRepository, clock: FakeClock) -> None:
    session = make_session(clock)
    session_repo.insert(session)
    assert session_repo.recover_stale() == 1
    stored = session_repo.get(session.id)
    assert stored.status is SessionStatus.INTERRUPTED
    assert stored.end_reason is EndReason.APP_TERMINATED
    assert stored.ended_at is None
    assert session_repo.recover_stale() == 0


def test_old_history_is_pruned(session_repo: SessionRepository, clock: FakeClock) -> None:
    old = make_session(clock, status=SessionStatus.COMPLETED, ended_at=clock.now, end_reason=EndReason.EXPIRED)
    session_repo.insert(old)
    clock.advance(400 * 24 * 3600)
    fresh = make_session(clock, status=SessionStatus.COMPLETED, ended_at=clock.now, end_reason=EndReason.EXPIRED)
    session_repo.insert(fresh)
    assert session_repo.prune(now=clock.now) == 1
    assert session_repo.get(old.id) is None and session_repo.get(fresh.id) is not None


def test_clear_keeps_active_session(session_repo: SessionRepository, clock: FakeClock) -> None:
    session_repo.insert(make_session(clock, status=SessionStatus.STOPPED, ended_at=clock.now,
                                     end_reason=EndReason.USER_STOPPED))
    active = make_session(clock)
    session_repo.insert(active)
    assert session_repo.clear() == 1
    assert session_repo.count() == 1 and session_repo.get(active.id) is not None


def test_queries_are_parameterised(session_repo: SessionRepository, clock: FakeClock) -> None:
    nasty = "x'); DROP TABLE sessions; --"
    session_repo.insert(make_session(clock, id=nasty))
    assert session_repo.get(nasty) is not None
    assert session_repo.count() == 1
