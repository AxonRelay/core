"""Pytest fixtures.

Tests run against an isolated in-memory SQLite database so they need neither a
running Postgres nor the LangGraph Platform. The schema is created from the
SQLAlchemy models, which is enough to exercise the application-layer ledger /
projection logic.

Caveats — this fixture does NOT guarantee parity with production Postgres:
it is built from `Base.metadata`, not the Alembic migrations, so it does not
validate migration correctness; Enum columns degrade to VARCHAR and JSON to
TEXT on SQLite. We enable `PRAGMA foreign_keys=ON` so FK constraints behave like
Postgres, but server-side defaults (e.g. `NOW()`) and enum check semantics may
differ. Treat these as logic tests, not schema-parity tests.

That gap is not hypothetical: it hid a broken migration chain and an enum-label
mismatch that made every Actor insert fail on Postgres. Schema parity is covered
separately by `test_postgres_schema.py`, which applies the real migrations to a
real database and runs in CI against postgres:16.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base


@pytest.fixture
def db():
    # One shared connection for the whole test: an in-memory SQLite database is
    # per-connection, and the REST TestClient serves requests on another
    # thread, which would otherwise see an empty database of its own.
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def self_actor(db):
    """The single human Actor that represents the operator."""
    actor = models.Actor(type=models.ActorTypeEnum.HUMAN, name="self")
    db.add(actor)
    db.commit()
    db.refresh(actor)
    return actor


@pytest.fixture
def client(db):
    """The REST API wired to the in-memory test database.

    `app.main` binds its `get_db` dependency to the real engine at import; this
    overrides it for the duration of the test so requests hit the same SQLite
    session the test inspects.
    """
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def mcp_db(db, monkeypatch):
    """Point the MCP tools at the in-memory test database.

    The tools open their own `SessionLocal()` through `server._session()`,
    which bypasses the `db` fixture; this swaps that contextmanager for one
    that yields the test session, so a tool call and the test see one database.
    """
    from contextlib import contextmanager

    from app.mcp import server

    @contextmanager
    def _test_session():
        yield db

    monkeypatch.setattr(server, "_session", _test_session)
    return db
