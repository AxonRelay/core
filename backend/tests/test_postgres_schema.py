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
import time
from datetime import datetime

import pytest
from sqlalchemy import Enum, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import coordination, crud, ledger, models

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
    models.ClaimResourceEnum,
    models.ClaimStatusEnum,
    models.RelayKindEnum,
    models.SafeActionEnum,
    models.SafeOutcomeEnum,
    models.FocusCodeEnum,
    models.ClaimReasonCodeEnum,
    models.RelayCodeEnum,
    models.AckCodeEnum,
]

COORDINATION_TABLES = {"workspaces", "sessions", "claims", "relays", "relay_receipts", "safe_events", "credentials"}


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


def _run_alembic(url: str, target: str) -> None:
    """`alembic upgrade <target>` against `url`, in a subprocess (see migrated_engine)."""
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=backend_dir,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.fail(f"alembic upgrade {target} failed:\n{completed.stdout}\n{completed.stderr}")


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
    _run_alembic(TEST_URL, "head")

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


@pytest.fixture(scope="module")
def legacy_engine():
    parsed = make_url(TEST_URL)
    # str(URL) masks the password ("***"); render it for real or the
    # subprocess and the engine cannot authenticate (CI has a password,
    # a local trust-auth cluster does not, which is how this hid).
    legacy_url = parsed.set(database=f"{parsed.database}_legacy").render_as_string(hide_password=False)
    _recreate_database(legacy_url)
    _run_alembic(legacy_url, "008")

    engine = create_engine(legacy_url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO actors (type, name, created_at) VALUES ('HUMAN', 'legacy', now())"))
        conn.execute(
            text(
                "INSERT INTO tasks (thread_id, title, status, created_at, updated_at) "
                "VALUES ('legacy-thread', 'legacy', 'WAITING_APPROVAL', now(), now())"
            )
        )
        conn.execute(
            text("INSERT INTO drafts (task_id, version, content, created_at) VALUES (1, 1, 'old bytes', now())")
        )
        # A second task whose unlocked pre-009 add_draft raced itself into
        # two drafts with the same version number.
        conn.execute(
            text(
                "INSERT INTO tasks (thread_id, title, status, created_at, updated_at) "
                "VALUES ('legacy-dup', 'dup', 'DRAFT', now(), now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO drafts (task_id, version, content, created_at) VALUES "
                "(2, 1, 'first', now()), (2, 1, 'raced duplicate', now()), (2, 2, 'later', now())"
            )
        )
        created_at = "2026-08-01 12:00:00.000000"
        entry_hash = ledger.compute_entry_hash(
            None,
            task_id=1,
            reviewer_actor_id=1,
            action="approved",
            comment="pre-009",
            created_at=datetime.fromisoformat(created_at),
        )
        conn.execute(
            text(
                "INSERT INTO approvals (task_id, reviewer_actor_id, action, comment, created_at, prev_hash, entry_hash) "
                "VALUES (1, 1, 'approved', 'pre-009', :created_at, NULL, :entry_hash)"
            ),
            {"created_at": created_at, "entry_hash": entry_hash},
        )
    engine.dispose()

    _run_alembic(legacy_url, "head")
    engine = create_engine(legacy_url)
    try:
        yield engine
    finally:
        engine.dispose()


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

    def test_the_artifact_binding_schema_is_in_place(self, migrated_engine):
        """Migration 009: a draft version names one artifact, and both ledger tables are indexed on task_id."""
        with migrated_engine.connect() as conn:
            constraints = {
                r[0]
                for r in conn.execute(
                    text("SELECT conname FROM pg_constraint WHERE conrelid = 'drafts'::regclass AND contype = 'u'")
                ).all()
            }
            indexes = {
                r[0]
                for r in conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename IN ('drafts', 'approvals')")
                ).all()
            }
            approval_columns = {
                r[0]
                for r in conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'approvals'")
                ).all()
            }
        assert "uq_drafts_task_version" in constraints

        assert {"ix_drafts_task_id", "ix_approvals_task_id"} <= indexes
        assert {
            "hash_version",
            "artifact_ref",
            "artifact_version",
            "artifact_commitment",
            "artifact_commitment_algorithm",
            "producer_actor_id",
        } <= approval_columns

    def test_a_credential_is_unique_by_digest_and_dies_with_its_actor(self, migrated_engine):
        """Migration 011: the token digest is the lookup key, and a deleted Actor takes its credentials."""
        with migrated_engine.connect() as conn:
            uniques = {
                r[0]
                for r in conn.execute(
                    text("SELECT conname FROM pg_constraint WHERE conrelid = 'credentials'::regclass AND contype = 'u'")
                ).all()
            }
            cascade = conn.execute(
                text("SELECT confdeltype FROM pg_constraint WHERE conrelid = 'credentials'::regclass AND contype = 'f'")
            ).scalar()
            columns = {
                r[0]
                for r in conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'credentials'")
                ).all()
            }
        assert "uq_credentials_token_hash" in uniques
        assert cascade == "c"  # ON DELETE CASCADE
        assert "token" not in columns  # only the digest is stored
        assert "token_hash" in columns

    def test_the_minimization_columns_are_in_place(self, migrated_engine):
        """Migration 012: opaque ids are unique, and each free-text field has a code beside it."""
        with migrated_engine.connect() as conn:
            indexes = {
                r[0]
                for r in conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename IN ('actors', 'workspaces')")
                ).all()
            }
            columns = {
                (r[0], r[1])
                for r in conn.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_name IN ('actors','workspaces','sessions','claims','relays','relay_receipts')"
                    )
                ).all()
            }
        assert {"ix_actors_opaque_id", "ix_workspaces_opaque_id"} <= indexes
        assert {
            ("actors", "opaque_id"),
            ("workspaces", "opaque_id"),
            ("sessions", "focus_code"),
            ("claims", "reason_code"),
            ("relays", "code"),
            ("relay_receipts", "ack_code"),
        } <= columns


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
        crud.add_draft(setup, task_id=task.id, content="the draft under review")
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

    def test_racing_editors_each_get_their_own_version_and_one_chain(self, migrated_engine):
        """Approvals that carry a modified draft race on the *draft* table too.

        The draft append happens under the same task row lock as the ledger
        write, so eight editors must produce eight distinct new versions, each
        approval bound to the version it created, on one linear chain. Without
        the lock the unique constraint would reject the losers instead.
        """
        factory = sessionmaker(bind=migrated_engine, autoflush=False)

        setup = factory()
        actor = models.Actor(type=models.ActorTypeEnum.HUMAN, name="editor")
        task = models.Task(thread_id="parity-editors", title="edit", status=models.TaskStatusEnum.WAITING_APPROVAL)
        setup.add_all([actor, task])
        setup.commit()
        crud.add_draft(setup, task_id=task.id, content="v1")
        task_id, actor_id = task.id, actor.id
        setup.close()

        barrier = threading.Barrier(self.WRITERS)
        failures: list[str] = []

        def edit(index: int) -> None:
            session = factory()
            try:
                barrier.wait(timeout=30)
                crud.record_approval(session, task_id, actor_id, "approved", modified_draft=f"edit by {index}")
            except Exception as exc:  # noqa: BLE001 - reported as a test failure
                failures.append(f"{type(exc).__name__}: {exc}")
            finally:
                session.close()

        threads = [threading.Thread(target=edit, args=(i,)) for i in range(self.WRITERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert failures == []

        check = factory()
        try:
            drafts = crud.get_drafts(check, task_id)
            assert [d.version for d in drafts] == list(range(1, self.WRITERS + 2))
            assert all(d.producer_actor_id == actor_id for d in drafts[1:])

            approvals = crud.get_approvals(check, task_id)
            assert sorted(a.artifact_version for a in approvals) == list(range(2, self.WRITERS + 2))
            by_version = {d.version: d for d in drafts}
            assert all(a.artifact_commitment == by_version[a.artifact_version].commitment for a in approvals)

            verdict = crud.verify_approval_chain(check, task_id)
            assert verdict["valid"] is True
            assert verdict["artifact_bound"] == self.WRITERS
        finally:
            check.close()


class TestConcurrentResourceClaims:
    """The row lock in claim_resource, exercised with real concurrent connections.

    Without it, N sessions in sibling worktrees can all read "stash is free"
    and all be granted an exclusive lease on the one stash stack they share -
    which is precisely the incident the resource claims exist to prevent.
    """

    CLAIMANTS = 8

    def test_only_one_racing_stash_claim_is_granted(self, migrated_engine, monkeypatch):
        factory = sessionmaker(bind=migrated_engine, autoflush=False)

        # Hold every writer inside the check-then-insert window for a moment so
        # the race is reproducible rather than timing-dependent. With the row
        # lock the sleep happens while the lock is held, so the others queue
        # behind it and see the committed claim; without the lock they all
        # read "free" together. (Verified: this test fails when the lock in
        # claim_resource is removed.)
        real_find = coordination.find_resource_conflicts

        def slow_find(*args, **kwargs):
            found = real_find(*args, **kwargs)
            time.sleep(0.3)
            return found

        monkeypatch.setattr(coordination, "find_resource_conflicts", slow_find)

        setup = factory()
        session_ids = []
        for index in range(self.CLAIMANTS):
            session = coordination.register_session(
                setup,
                actor_name=f"claimant-{index}",
                host="parity-host",
                repo="AxonRelay/parity",
                clone_path=f"/parity/core-wt{index}",
                git_dir="/parity/core/.git",
            )
            session_ids.append(session.id)
        setup.close()

        barrier = threading.Barrier(self.CLAIMANTS)
        outcomes: list[bool] = []
        failures: list[str] = []
        lock = threading.Lock()

        def claim(session_id: int) -> None:
            session = factory()
            try:
                barrier.wait(timeout=30)
                result = coordination.claim_resource(
                    session, session_id=session_id, resource=models.ClaimResourceEnum.STASH
                )
                with lock:
                    outcomes.append(result["granted"])
            except Exception as exc:  # noqa: BLE001 - reported as a test failure
                with lock:
                    failures.append(f"{type(exc).__name__}: {exc}")
            finally:
                session.close()

        threads = [threading.Thread(target=claim, args=(sid,)) for sid in session_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert failures == []
        assert len(outcomes) == self.CLAIMANTS
        assert outcomes.count(True) == 1, outcomes


class TestArtifactBindingMigration:
    """Migration 009 applied to a database that already holds ledger rows.

    `migrated_engine` starts from nothing, which cannot show what 009 does to
    existing data. This class builds a second database at revision 008, writes
    rows the way the app wrote them then (a draft without a commitment, a v1
    approval), and upgrades to head.
    """

    def test_existing_drafts_get_a_commitment_and_existing_approvals_stay_v1(self, legacy_engine):
        session = sessionmaker(bind=legacy_engine, autoflush=False)()
        try:
            draft = crud.get_drafts(session, 1)[0]
            assert draft.commitment == ledger.compute_artifact_commitment("old bytes")
            assert draft.commitment_algorithm == ledger.COMMITMENT_ALGORITHM
            assert draft.producer_actor_id is None

            (approval,) = crud.get_approvals(session, 1)
            assert approval.hash_version == 1  # stamped by 009: hashed with the v1 payload
            assert approval.artifact_bound is False
            assert approval.artifact_commitment is None
        finally:
            session.close()

    def test_duplicate_draft_versions_are_renumbered_before_the_constraint(self, legacy_engine):
        session = sessionmaker(bind=legacy_engine, autoflush=False)()
        try:
            drafts = crud.get_drafts(session, 2)
            assert [(d.version, d.content) for d in drafts] == [(1, "first"), (2, "raced duplicate"), (3, "later")]
        finally:
            session.close()

    def test_the_old_chain_verifies_as_v1_and_new_entries_extend_it_as_v2(self, legacy_engine):
        session = sessionmaker(bind=legacy_engine, autoflush=False)()
        try:
            before = crud.verify_approval_chain(session, 1)
            assert before == {
                "valid": True,
                "broken_at": None,
                "count": 1,
                "legacy": 0,
                "artifact_bound": 0,
                "unbound": 1,
            }

            new = crud.record_approval(session, 1, 1, "approved", "post-009")
            assert new.artifact_bound is True
            assert new.artifact_version == 1
            assert new.prev_hash == crud.get_approvals(session, 1)[0].entry_hash

            after = crud.verify_approval_chain(session, 1)
            assert after["valid"] is True
            assert (after["artifact_bound"], after["unbound"]) == (1, 1)
        finally:
            session.close()


class TestCredentialsOnPostgres:
    """Authentication against the real engine: the digest lookup and the cascade."""

    def test_a_credential_authenticates_and_a_revoked_one_does_not(self, pg_session):
        from app import authz

        actor = models.Actor(type=models.ActorTypeEnum.AI, name="pg-cred-actor")
        pg_session.add(actor)
        pg_session.commit()

        token = authz.issue_token()
        row = models.Credential(
            actor_id=actor.id,
            label="pg",
            token_hash=authz.token_digest(token),
            scopes=authz.format_scopes({authz.Scope.LEDGER_READ}),
        )
        pg_session.add(row)
        pg_session.commit()

        principal = authz.authenticate(pg_session, token)
        assert principal is not None
        assert principal.actor_id == actor.id
        assert principal.scopes == {authz.Scope.LEDGER_READ}

        row.revoked_at = datetime(2026, 9, 7, 0, 0, 0)
        pg_session.commit()
        assert authz.authenticate(pg_session, token) is None

    def test_deleting_the_actor_deletes_its_credentials(self, pg_session):
        from app import authz

        actor = models.Actor(type=models.ActorTypeEnum.AI, name="pg-cred-cascade")
        pg_session.add(actor)
        pg_session.commit()
        token = authz.issue_token()
        pg_session.add(
            models.Credential(
                actor_id=actor.id,
                label="pg-cascade",
                token_hash=authz.token_digest(token),
                scopes=authz.format_scopes({authz.Scope.LEDGER_READ}),
            )
        )
        pg_session.commit()

        pg_session.delete(actor)
        pg_session.commit()

        # The database enforces this, not the ORM: the module-scoped engine is
        # shared, so the label is unique to this test.
        assert pg_session.query(models.Credential).filter_by(label="pg-cascade").count() == 0
        assert authz.authenticate(pg_session, token) is None

    def test_two_credentials_cannot_share_a_digest(self, pg_session):
        from sqlalchemy.exc import IntegrityError

        from app import authz

        actor = models.Actor(type=models.ActorTypeEnum.AI, name="pg-cred-dup")
        pg_session.add(actor)
        pg_session.commit()
        digest = authz.token_digest(authz.issue_token())
        for _ in range(2):
            pg_session.add(models.Credential(actor_id=actor.id, label="dup", token_hash=digest, scopes=""))
        with pytest.raises(IntegrityError):
            pg_session.commit()
        pg_session.rollback()


class TestMinimizationMigration:
    """Migration 012 against rows that already exist."""

    def test_existing_rows_are_backfilled_with_unique_opaque_ids(self, legacy_engine):
        session = sessionmaker(bind=legacy_engine, autoflush=False)()
        try:
            actors = session.query(models.Actor).all()
            assert actors, "the legacy fixture seeds an actor"
            assert all(a.opaque_id for a in actors)
            assert len({a.opaque_id for a in actors}) == len(actors)
        finally:
            session.close()

    def test_a_second_opaque_id_cannot_collide(self, pg_session):
        from sqlalchemy.exc import IntegrityError

        from app import disclosure

        shared = disclosure.new_opaque_id()
        for name in ("dup-a", "dup-b"):
            pg_session.add(models.Actor(type=models.ActorTypeEnum.AI, name=name, opaque_id=shared))
        with pytest.raises(IntegrityError):
            pg_session.commit()
        pg_session.rollback()
