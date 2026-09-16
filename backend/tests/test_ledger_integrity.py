"""Tamper-evidence of the approval hash chain (app/ledger.py + crud)."""

from datetime import datetime

from sqlalchemy import text

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


def test_nulling_a_hashed_rows_hash_is_detected_not_treated_as_legacy(db, self_actor):
    """Blanking out a hashed row's entry_hash (after the chain started) is tampering."""
    task = _task(db)
    crud.record_approval(db, task_id=task.id, reviewer_actor_id=self_actor.id, action="approved", comment="a")
    crud.record_approval(db, task_id=task.id, reviewer_actor_id=self_actor.id, action="approved", comment="b")
    assert crud.verify_approval_chain(db, task.id)["valid"] is True

    # Tamper: null out the last (hashed) row's entry_hash to try to drop it silently.
    victim = crud.get_approvals(db, task.id)[-1]
    victim.entry_hash = None
    db.commit()

    result = crud.verify_approval_chain(db, task.id)
    assert result["valid"] is False
    assert result["broken_at"] == victim.id


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


def test_approval_hash_binds_the_validated_draft_and_evidence_manifest(db, self_actor):
    task = _task(db)
    manifest = [{"evidence_ref": "E-1", "quote_sha256": "a" * 64, "source_url": "https://example.test"}]
    approval = crud.record_approval(
        db,
        task.id,
        self_actor.id,
        "approved",
        "ship",
        approved_draft_sha256="b" * 64,
        evidence_manifest=manifest,
    )
    assert crud.verify_approval_chain(db, task.id)["valid"] is True

    approval.evidence_manifest = [{**manifest[0], "quote_sha256": "c" * 64}]
    db.commit()
    assert crud.verify_approval_chain(db, task.id)["broken_at"] == approval.id


def test_approved_content_is_reconstructable_and_tamper_evident(db, self_actor):
    task = _task(db)
    content = "The exact approved draft [E-1]."
    approval = crud.record_approval(
        db,
        task.id,
        self_actor.id,
        "approved",
        approved_content=content,
        approved_draft_sha256=ledger.hash_text(content),
        evidence_manifest=[],
        approval_episode_id="checkpoint:content-tamper",
        decision_key="d" * 64,
    )

    assert approval.approved_content == content
    assert crud.verify_approval_chain(db, task.id)["valid"] is True
    approval.approved_content = "silently changed"
    db.commit()
    assert crud.verify_approval_chain(db, task.id)["broken_at"] == approval.id


def test_nulling_reconstructable_approved_content_is_detected(db, self_actor):
    task = _task(db)
    content = "Approved bytes must remain reconstructable."
    approval = crud.record_approval(
        db,
        task.id,
        self_actor.id,
        "approved",
        approved_content=content,
        approved_draft_sha256=ledger.hash_text(content),
        evidence_manifest=[],
        approval_episode_id="checkpoint:null-test",
        decision_key="c" * 64,
    )

    # Simulate storage corruption / a privileged writer bypassing the schema
    # constraint. Verification must still detect the missing payload.
    db.execute(text("PRAGMA ignore_check_constraints = ON"))
    db.execute(text("UPDATE approvals SET approved_content = NULL WHERE id = :id"), {"id": approval.id})
    db.commit()
    db.expire_all()

    assert crud.verify_approval_chain(db, task.id)["broken_at"] == approval.id


def test_nulling_decision_key_cannot_bypass_approved_content_verification(db, self_actor):
    task = _task(db)
    content = "Original approved bytes."
    approval = crud.record_approval(
        db,
        task.id,
        self_actor.id,
        "approved",
        approved_content=content,
        approved_draft_sha256=ledger.hash_text(content),
        approval_episode_id="checkpoint:key-tamper",
        decision_key="9" * 64,
    )
    db.execute(text("PRAGMA ignore_check_constraints = ON"))
    db.execute(
        text("UPDATE approvals SET decision_key = NULL, approved_content = 'rewritten' WHERE id = :id"),
        {"id": approval.id},
    )
    db.commit()
    db.expire_all()

    assert crud.verify_approval_chain(db, task.id)["broken_at"] == approval.id


def test_decision_key_makes_delivery_retry_reuse_the_same_ledger_event(db, self_actor):
    task = _task(db)
    content = "same approval"
    first = crud.record_approval(
        db,
        task.id,
        self_actor.id,
        "approved",
        approved_content=content,
        approved_draft_sha256=ledger.hash_text(content),
        approval_episode_id="checkpoint:retry",
        decision_key="e" * 64,
    )
    crud.mark_approval_delivery(db, first, "failed", "offline")
    retry = crud.record_approval(
        db,
        task.id,
        self_actor.id,
        "approved",
        approved_content=content,
        approved_draft_sha256=ledger.hash_text(content),
        approval_episode_id="checkpoint:retry",
        decision_key="e" * 64,
    )

    assert retry.id == first.id
    assert retry.delivery_status == "failed"
    assert db.query(models.Approval).count() == 1


def test_one_approval_episode_cannot_record_conflicting_decisions(db, self_actor):
    task = _task(db)
    crud.record_approval(
        db,
        task.id,
        self_actor.id,
        "rejected",
        approval_episode_id="checkpoint:one-choice",
        decision_key="1" * 64,
    )

    try:
        crud.record_approval(
            db,
            task.id,
            self_actor.id,
            "rejected",
            comment="different decision payload",
            approval_episode_id="checkpoint:one-choice",
            decision_key="2" * 64,
        )
    except ValueError as error:
        assert "already contains a different decision" in str(error)
    else:
        raise AssertionError("conflicting decision should be rejected")


def test_tampering_with_approval_episode_identity_breaks_the_chain(db, self_actor):
    task = _task(db)
    approval = crud.record_approval(
        db,
        task.id,
        self_actor.id,
        "rejected",
        approval_episode_id="checkpoint:original",
        decision_key="3" * 64,
    )
    approval.approval_episode_id = "checkpoint:rewritten"
    db.commit()

    assert crud.verify_approval_chain(db, task.id)["broken_at"] == approval.id


def test_identical_decisions_in_different_approval_episodes_have_distinct_keys():
    common = {
        "task_id": 1,
        "action": "approved",
        "comment": "ship",
        "approved_draft_sha256": "a" * 64,
        "evidence_manifest": [],
    }
    first = ledger.compute_decision_key(approval_episode_id="checkpoint:first", **common)
    retry = ledger.compute_decision_key(approval_episode_id="checkpoint:first", **common)
    later_episode = ledger.compute_decision_key(approval_episode_id="checkpoint:second", **common)

    assert retry == first
    assert later_episode != first


def test_delivery_claim_is_atomic_and_unknown_is_not_automatically_retryable(db, self_actor):
    task = _task(db)
    approval = crud.record_approval(
        db,
        task.id,
        self_actor.id,
        "rejected",
        approval_episode_id="checkpoint:claim",
        decision_key="f" * 64,
    )

    assert crud.claim_approval_delivery(db, approval) is True
    assert approval.delivery_status == "delivering"
    assert crud.claim_approval_delivery(db, approval) is False

    crud.mark_approval_delivery(db, approval, "unknown", "run may have started")
    assert crud.claim_approval_delivery(db, approval) is False
    assert approval.delivery_status == "unknown"
