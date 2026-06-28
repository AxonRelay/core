"""Tamper-evidence of the approval hash chain (app/ledger.py + crud)."""

from datetime import datetime

from app import crud, ledger, models


def _task(db):
    return crud.create_task(db, thread_id="thread-1", title="t")


def test_chain_links_each_entry_to_the_previous(db, self_actor):
    task = _task(db)
    a1 = crud.record_approval(db, task_id=task.id, reviewer_actor_id=self_actor.id, action="rejected", comment="redo")
    a2 = crud.record_approval(db, task_id=task.id, reviewer_actor_id=self_actor.id, action="approved", comment="ok")

    assert a1.prev_hash is None  # first entry starts the chain
    assert a1.entry_hash is not None
    assert a2.prev_hash == a1.entry_hash  # second links to the first

    result = crud.verify_approval_chain(db, task.id)
    assert result == {"valid": True, "broken_at": None, "count": 2, "legacy": 0}


def test_empty_chain_is_valid(db):
    task = _task(db)
    assert crud.verify_approval_chain(db, task.id) == {"valid": True, "broken_at": None, "count": 0, "legacy": 0}


def test_legacy_unhashed_rows_are_skipped_then_chain_verifies(db, self_actor):
    """Pre-004 rows (entry_hash NULL) are counted as legacy, not treated as tampering."""
    task = _task(db)
    # Simulate a legacy approval recorded before the hash chain existed.
    legacy = models.Approval(task_id=task.id, reviewer_actor_id=self_actor.id, action="approved", comment="old")
    db.add(legacy)
    db.commit()
    # New, hash-chained approvals recorded afterwards.
    crud.record_approval(db, task_id=task.id, reviewer_actor_id=self_actor.id, action="rejected", comment="redo")
    crud.record_approval(db, task_id=task.id, reviewer_actor_id=self_actor.id, action="approved", comment="ok")

    result = crud.verify_approval_chain(db, task.id)
    assert result == {"valid": True, "broken_at": None, "count": 3, "legacy": 1}


def test_tampering_with_a_recorded_comment_is_detected(db, self_actor):
    task = _task(db)
    crud.record_approval(db, task_id=task.id, reviewer_actor_id=self_actor.id, action="approved", comment="ship it")
    crud.record_approval(db, task_id=task.id, reviewer_actor_id=self_actor.id, action="approved", comment="and again")
    assert crud.verify_approval_chain(db, task.id)["valid"] is True

    # Tamper: edit a stored approval's comment directly, bypassing record_approval.
    victim = crud.get_approvals(db, task.id)[0]
    victim.comment = "totally different decision"
    db.commit()

    result = crud.verify_approval_chain(db, task.id)
    assert result["valid"] is False
    assert result["broken_at"] == victim.id


def test_compute_entry_hash_is_deterministic_and_chain_sensitive():
    base = {
        "task_id": 1,
        "reviewer_actor_id": 2,
        "action": "approved",
        "comment": "ok",
        "created_at": datetime(2026, 6, 28, 0, 0, 0),
    }
    h1 = ledger.compute_entry_hash(None, **base)
    assert h1 == ledger.compute_entry_hash(None, **base)  # deterministic
    assert ledger.compute_entry_hash("abc", **base) != h1  # depends on prev_hash
    assert ledger.compute_entry_hash(None, **{**base, "comment": "tampered"}) != h1  # depends on content


def test_compute_entry_hash_distinguishes_none_from_empty_comment():
    base = {
        "task_id": 1,
        "reviewer_actor_id": 2,
        "action": "approved",
        "created_at": datetime(2026, 6, 28, 0, 0, 0),
    }
    assert ledger.compute_entry_hash(None, comment=None, **base) != ledger.compute_entry_hash(None, comment="", **base)


def test_models_has_hash_columns():
    # Guards the migration/model staying in sync with the ledger.
    cols = models.Approval.__table__.columns.keys()
    assert "prev_hash" in cols and "entry_hash" in cols
