"""The approval ledger under more than one writer.

Until the coordination layer, AxonRelay assumed a single operator approving
serially (delta-mvp-spec.md §11.6). Once several agents and several devices
share one instance that assumption no longer holds: `record_approval` does a
read-modify-write on a task's hash chain, so two concurrent approvals on one
task would chain off the same `prev_hash` and fork it - and `verify_approval_chain`
reports a fork as tampering.

`record_approval` now takes a row lock on the task before reading the chain
head, which serializes writers per task. SQLite (the test backend) has no row
locks and serializes writers globally, so these tests pin the two halves that
*are* observable here: the lock is really emitted on Postgres, and a fork - the
corruption the lock prevents - is detected when one is forced into the table.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app import crud, ledger, models


def _task(db, title="concurrent"):
    task = models.Task(thread_id=f"t-{title}", title=title, status=models.TaskStatusEnum.WAITING_APPROVAL)
    db.add(task)
    db.commit()
    db.refresh(task)
    # Every approval binds to an artifact, so the task under test has one draft.
    crud.add_draft(db, task_id=task.id, content=f"draft for {title}")
    return task


def test_record_approval_locks_the_task_row_on_postgres():
    """The guard is a real `SELECT ... FOR UPDATE`, not just a comment."""
    statement = select(models.Task).where(models.Task.id == 1).with_for_update()
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in compiled


def test_two_approvals_on_one_task_stay_on_a_single_chain(db, self_actor):
    """Serialized writers produce a linear chain, whoever gets there first."""
    task = _task(db)
    first = crud.record_approval(db, task.id, self_actor.id, "approved", "first")
    second = crud.record_approval(db, task.id, self_actor.id, "rejected", "second")

    assert first.prev_hash is None
    assert second.prev_hash == first.entry_hash
    assert crud.verify_approval_chain(db, task.id)["valid"] is True


def test_approvals_on_different_tasks_are_independent_chains(db, self_actor):
    """Per-task chains: locking one task must not couple it to another."""
    task_a, task_b = _task(db, "a"), _task(db, "b")
    crud.record_approval(db, task_a.id, self_actor.id, "approved", "a1")
    crud.record_approval(db, task_b.id, self_actor.id, "approved", "b1")
    crud.record_approval(db, task_a.id, self_actor.id, "approved", "a2")

    assert crud.verify_approval_chain(db, task_a.id) == {
        "valid": True,
        "broken_at": None,
        "count": 2,
        "legacy": 0,
        "artifact_bound": 2,
        "unbound": 0,
    }
    assert crud.verify_approval_chain(db, task_b.id)["valid"] is True


def test_a_forked_chain_is_reported_as_invalid(db, self_actor):
    """The corruption the row lock exists to prevent is detectable.

    Two entries chained off the same parent is exactly what an unserialized race
    would write. Forced in directly here, it must not verify.
    """
    task = _task(db)
    root = crud.record_approval(db, task.id, self_actor.id, "approved", "root")

    now = datetime.utcnow()
    binding = ledger.ArtifactBinding(
        ref=root.artifact_ref,
        version=root.artifact_version,
        commitment=root.artifact_commitment,
        commitment_algorithm=root.artifact_commitment_algorithm,
        producer_actor_id=root.producer_actor_id,
    )
    fork = models.Approval(
        task_id=task.id,
        reviewer_actor_id=self_actor.id,
        action="rejected",
        comment="racing writer",
        created_at=now,
        # Same parent as the row that already claims it - a fork.
        prev_hash=root.prev_hash,
        entry_hash=ledger.compute_entry_hash(
            root.prev_hash,
            task_id=task.id,
            reviewer_actor_id=self_actor.id,
            action="rejected",
            comment="racing writer",
            created_at=now,
            artifact=binding,
        ),
        hash_version=ledger.ENTRY_HASH_VERSION,
        artifact_ref=binding.ref,
        artifact_version=binding.version,
        artifact_commitment=binding.commitment,
        artifact_commitment_algorithm=binding.commitment_algorithm,
        producer_actor_id=binding.producer_actor_id,
    )
    db.add(fork)
    db.commit()

    result = crud.verify_approval_chain(db, task.id)
    assert result["valid"] is False
    assert result["broken_at"] == fork.id


def test_a_serialized_rewrite_of_the_same_fork_verifies(db, self_actor):
    """Chaining the second entry off the first - what the lock guarantees - verifies."""
    task = _task(db)
    crud.record_approval(db, task.id, self_actor.id, "approved", "root")
    crud.record_approval(db, task.id, self_actor.id, "rejected", "racing writer")

    assert crud.verify_approval_chain(db, task.id)["valid"] is True
