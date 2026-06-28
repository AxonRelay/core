"""Pytest fixtures.

Tests run against an isolated in-memory SQLite database so they need neither a
running Postgres nor the LangGraph Platform. The schema is created from the
SQLAlchemy models, which is enough to exercise the application-layer ledger /
projection logic.

Caveats — this fixture does NOT guarantee parity with production Postgres:
it is built from `Base.metadata`, not the Alembic migrations (003), so it does
not validate migration correctness; Enum columns degrade to VARCHAR and JSON to
TEXT on SQLite. We enable `PRAGMA foreign_keys=ON` so FK constraints behave like
Postgres, but server-side defaults (e.g. `NOW()`) and enum check semantics may
differ. Treat these as logic tests, not schema-parity tests.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

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
