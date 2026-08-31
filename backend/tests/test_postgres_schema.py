"""Schema-parity checks that only a real Postgres can make.

The rest of the suite runs on in-memory SQLite, which is fast and needs no
service — but SQLite degrades `Enum` to `VARCHAR` and accepts any string, and it
never runs the Alembic migrations at all (conftest builds the schema from
`Base.metadata`). Two production-only defects hid in exactly that gap:

  * migration 002 emitted `CREATE TYPE` twice for the same enum, so the chain
    could not reach a fresh Postgres at all;
  * actortypeenum / agenttypeenum / assignmentroleenum were created with
    lowercase labels while `sa.Enum(PythonEnum)` persists member *names*, so
    inserting an Actor raised `invalid input value for enum actortypeenum:
    "HUMAN"`.

Both are the kind of thing that is invisible until someone points the app at
Postgres. These tests apply the real migration chain to a real database and
compare the result against the models.

Skipped unless a database is provided:

    AXONRELAY_TEST_POSTGRES_URL=postgresql://user:pw@host:port/db pytest

The URL's database is dropped and recreated, so point it at a scratch database.
"""

import os
import pathlib
import subprocess
import sys
import threading

import pytest
from sqlalchemy import Enum, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import crud, models

TEST_URL = os.environ.get("AXONRELAY_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL,
    reason="Set AXONRELAY_TEST_POSTGRES_URL to run the Postgres schema-parity tests",
)

MODEL_ENUMS = [
    models.ActorTypeEnum,
    models.AgentTypeEnum,
    models.AssignmentRoleEnum,
    models.TaskStatusEnum,
    models.SessionStatusEnum,
    models.ClaimModeEnum,
    models.ClaimStatusEnum,
    models.RelayKindEnum,
]

COORDINATION_TABLES = {"workspaces", "sessions", "claims", "relays", "relay_receipts"}


def _expected_head() -> str:
    """The highest revision id present in the migrations directory."""
    versions = pathlib.Path(__file__).resolve().parent.parent / "alembic" / "versions"
    revisions = sorted(f.name.split("_", 1)[0] for f in versions.glob("[0-9]*.py"))
    return revisions[-1]


def _recreate_database(url: str) -> None:
    """Drop and recreate the target database so migrations run from nothing."""
    parsed = make_url(url)
    name = parsed.database
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()


@pytest.fixture(scope="module")
def migrated_engine():
    """A fresh database with the whole Alembic chain applied to it.

    Alembic runs in a subprocess rather than in-process: `alembic/env.py`
    overrides `sqlalchemy.url` with `app.database.DATABASE_URL`, which is read at
    import time, so an in-process config override would be ignored and the
    migration would target whatever the parent process had configured. The
    subprocess is also exactly how an operator runs it.
    """
    _recreate_database(TEST_URL)

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env={**os.environ, "DATABASE_URL": TEST_URL},
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{completed.stdout}\n{completed.stderr}")

    engine = create_engine(TEST_URL)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def pg_session(migrated_engine):
    session = sessionmaker(bind=migrated_engine, autoflush=False)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _db_enum_labels(engine) -> dict[str, set[str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid")
        ).all()
    labels: dict[str, set[str]] = {}
    for typname, label in rows:
        labels.setdefault(typname, set()).add(label)
    return labels


class TestMigrationChain:
    def test_the_whole_chain_applies_to_an_empty_database(self, migrated_engine):
        """Regression: migration 002 used to emit CREATE TYPE twice and abort here.

        The expected head is derived from the migration directory rather than
        hardcoded, so adding a migration does not require editing this test —
        and a migration that fails to apply still fails it.
        """
        with migrated_engine.connect() as conn:
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert revision == _expected_head()

    def test_the_coordination_tables_exist(self, migrated_engine):
        with migrated_engine.connect() as conn:
            present = {
                r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).all()
            }
        assert present >= COORDINATION_TABLES

    def test_the_workspace_identity_constraint_is_enforced(self, migrated_engine):
        """(host, repo, clone_path) must be unique — sibling clones rely on it."""
        with migrated_engine.connect() as conn:
            names = {
                r[0]
                for r in conn.execute(
                    text("SELECT conname FROM pg_constraint WHERE conrelid = 'workspaces'::regclass AND contype = 'u'")
                ).all()
            }
        assert "uq_workspace_identity" in names


class TestEnumParity:
    @pytest.mark.parametrize("enum_cls", MODEL_ENUMS, ids=lambda c: Enum(c).name)
    def test_model_enum_matches_the_database_type(self, migrated_engine, enum_cls):
        """SQLAlchemy persists member *names*; the DB labels must be those names.

        Regression: actortypeenum was created as {human, ai} while SQLAlchemy
        sends {HUMAN, AI}, so every Actor insert failed on Postgres.
        """
        sa_enum = Enum(enum_cls)
        labels = _db_enum_labels(migrated_engine)
        assert sa_enum.name in labels, f"enum type {sa_enum.name} missing from the database"
        assert set(sa_enum.enums) == labels[sa_enum.name]


class TestWritesActuallyLand:
    """Each of these raised `invalid input value for enum` before migration 007."""

    def test_an_actor_can_be_inserted(self, pg_session):
        actor = models.Actor(type=models.ActorTypeEnum.HUMAN, name="parity-human")
        pg_session.add(actor)
        pg_session.commit()
        assert actor.id is not None

    def test_an_agent_definition_can_be_inserted(self, pg_session):
        agent = crud.create_agent_definition(pg_session, name="parity-writer", agent_type=models.AgentTypeEnum.WRITER)
        assert agent.agent_type == models.AgentTypeEnum.WRITER

    def test_a_task_assignment_can_be_inserted(self, pg_session):
        actor = models.Actor(type=models.ActorTypeEnum.AI, name="parity-ai")
        task = models.Task(thread_id="parity-assign", title="t")
        pg_session.add_all([actor, task])
        pg_session.commit()

        assignment = crud.create_task_assignment(pg_session, task.id, actor.id, models.AssignmentRoleEnum.APPROVER)
        assert assignment.role == models.AssignmentRoleEnum.APPROVER

    def test_the_post_pivot_task_statuses_are_accepted(self, pg_session):
        """WAITING_REVIEW / NEEDS_REVISION were added to the type by migration 003."""
        for status in (models.TaskStatusEnum.WAITING_REVIEW, models.TaskStatusEnum.NEEDS_REVISION):
            task = models.Task(thread_id=f"parity-{status}", title="t", status=status)
            pg_session.add(task)
            pg_session.commit()
            assert task.status == status


class TestConcurrentApprovals:
    """The row lock in record_approval, exercised with real concurrent connections.

    SQLite cannot show this: it has no row locks and serializes writers globally,
    so the SQLite tests can only assert that `FOR UPDATE` is emitted. Here the
    writers genuinely race.
    """

    WRITERS = 8

    def test_racing_writers_produce_one_linear_chain(self, migrated_engine):
        factory = sessionmaker(bind=migrated_engine, autoflush=False)

        setup = factory()
        actor = models.Actor(type=models.ActorTypeEnum.HUMAN, name="racer")
        task = models.Task(thread_id="parity-race", title="race", status=models.TaskStatusEnum.WAITING_APPROVAL)
        setup.add_all([actor, task])
        setup.commit()
        task_id, actor_id = task.id, actor.id
        setup.close()

        barrier = threading.Barrier(self.WRITERS)
        failures: list[str] = []

        def write(index: int) -> None:
            session = factory()
            try:
                barrier.wait(timeout=30)
                crud.record_approval(session, task_id, actor_id, "approved", f"writer-{index}")
            except Exception as exc:  # noqa: BLE001 - reported as a test failure
                failures.append(f"{type(exc).__name__}: {exc}")
            finally:
                session.close()

        threads = [threading.Thread(target=write, args=(i,)) for i in range(self.WRITERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert failures == []

        check = factory()
        try:
            approvals = crud.get_approvals(check, task_id)
            assert len(approvals) == self.WRITERS

            # A fork shows up as two entries chaining off the same parent.
            prev_hashes = [a.prev_hash for a in approvals]
            assert len(set(prev_hashes)) == len(prev_hashes)

            assert all(approvals[i].prev_hash == approvals[i - 1].entry_hash for i in range(1, len(approvals)))
            assert crud.verify_approval_chain(check, task_id)["valid"] is True
        finally:
            check.close()
