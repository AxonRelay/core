"""Every approval names the exact artifact it decided on (issue #24).

The hash chain (test_ledger_integrity.py) proves an approval *event* was not
edited. It did not say *what* was approved: an entry stayed valid while the
draft it referred to was ambiguous. Now each entry carries an artifact binding
— the draft's resource reference, version, content commitment, algorithm and
producer — and all of it is inside the hash. These tests pin:

* an approval cannot exist without an artifact target;
* a decision made against a draft that has since changed is refused;
* an edited draft becomes a new version *before* the approval is recorded;
* altering any binding field, or the payload-version marker, is detected;
* rows from before the binding verify as what they are and are reported as
  not artifact-bound rather than as tampered;
* what the binding does **not** prove — the stored draft bytes are checked
  against the commitment separately, and the ledger never holds external
  artifact content.
"""

import asyncio
import hashlib
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app import crud, ledger, models
from app.mcp import server
from app.mcp.serializers import approval_to_dict, draft_to_dict
from app.schema import ApprovalResponse, DraftResponse


def _task(db, title="bound"):
    return crud.create_task(db, thread_id=f"thread-{title}", title=title)


def _task_with_draft(db, content="draft v1", title="bound"):
    task = _task(db, title)
    draft = crud.add_draft(db, task_id=task.id, content=content)
    return task, draft


# ---------------------------------------------------------------- commitments


def test_add_draft_records_a_content_commitment(db):
    task, draft = _task_with_draft(db, "hello")
    assert draft.commitment == hashlib.sha256(b"hello").hexdigest()
    assert draft.commitment == ledger.compute_artifact_commitment("hello")
    assert draft.commitment_algorithm == ledger.COMMITMENT_ALGORITHM
    assert draft.producer_actor_id is None


def test_add_draft_accepts_a_matching_source_commitment_and_refuses_a_wrong_one(db, self_actor):
    task = _task(db)
    ok = crud.add_draft(
        db,
        task_id=task.id,
        content="x",
        commitment=ledger.compute_artifact_commitment("x"),
        producer_actor_id=self_actor.id,
    )
    assert ok.producer_actor_id == self_actor.id

    with pytest.raises(crud.CommitmentMismatchError):
        crud.add_draft(db, task_id=task.id, content="y", commitment=ledger.compute_artifact_commitment("not y"))
    assert [d.version for d in crud.get_drafts(db, task.id)] == [1]


def test_add_draft_on_an_unknown_task_is_refused_before_touching_the_table(db):
    with pytest.raises(crud.TaskNotFoundError):
        crud.add_draft(db, task_id=999_999, content="orphan")


def test_a_draft_version_names_exactly_one_artifact(db):
    task, draft = _task_with_draft(db)
    db.add(models.Draft(task_id=task.id, version=draft.version, content="impostor"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ------------------------------------------------------------------- binding


def test_an_approval_cannot_be_recorded_without_an_artifact(db, self_actor):
    task = _task(db)
    with pytest.raises(crud.ArtifactRequiredError):
        crud.record_approval(db, task.id, self_actor.id, "approved", "blind")
    assert crud.get_approvals(db, task.id) == []


def test_an_approval_binds_to_the_latest_draft(db, self_actor):
    task, _ = _task_with_draft(db, "v1")
    latest = crud.add_draft(db, task_id=task.id, content="v2")

    approval = crud.record_approval(db, task.id, self_actor.id, "approved", "ok")

    assert approval.hash_version == ledger.ENTRY_HASH_VERSION
    assert approval.artifact_bound is True
    assert approval.artifact_ref == f"axonrelay://tasks/{task.id}/drafts/2"
    assert approval.artifact_version == 2
    assert approval.artifact_commitment == latest.commitment == ledger.compute_artifact_commitment("v2")
    assert approval.artifact_commitment_algorithm == ledger.COMMITMENT_ALGORITHM
    assert approval.producer_actor_id is None  # produced by the graph, not a known Actor

    verdict = crud.verify_approval_chain(db, task.id)
    assert verdict == {
        "valid": True,
        "broken_at": None,
        "count": 1,
        "legacy": 0,
        "artifact_bound": 1,
        "unbound": 0,
    }


def test_a_decision_on_the_draft_that_was_shown_is_recorded(db, self_actor):
    task, draft = _task_with_draft(db)
    approval = crud.record_approval(
        db,
        task.id,
        self_actor.id,
        "rejected",
        "needs work",
        artifact_version=draft.version,
        expected_commitment=draft.commitment,
    )
    assert approval.artifact_version == draft.version
    assert approval.artifact_commitment == draft.commitment


def test_a_decision_on_a_superseded_version_is_refused_and_nothing_is_recorded(db, self_actor):
    task, shown = _task_with_draft(db, "what the reviewer read")
    crud.add_draft(db, task_id=task.id, content="what arrived meanwhile")

    with pytest.raises(crud.StaleArtifactError):
        crud.record_approval(db, task.id, self_actor.id, "approved", "lgtm", artifact_version=shown.version)
    assert crud.get_approvals(db, task.id) == []


def test_a_decision_on_a_superseded_commitment_is_refused(db, self_actor):
    task, shown = _task_with_draft(db, "what the reviewer read")
    crud.add_draft(db, task_id=task.id, content="what arrived meanwhile")

    with pytest.raises(crud.StaleArtifactError):
        crud.record_approval(db, task.id, self_actor.id, "approved", expected_commitment=shown.commitment)
    assert crud.get_approvals(db, task.id) == []


def test_a_modified_draft_becomes_a_new_version_before_the_approval_binds_to_it(db, self_actor):
    task, shown = _task_with_draft(db, "original")

    approval = crud.record_approval(
        db,
        task.id,
        self_actor.id,
        "approved",
        "fixed a typo",
        artifact_version=shown.version,
        modified_draft="original, corrected",
    )

    drafts = crud.get_drafts(db, task.id)
    assert [d.version for d in drafts] == [1, 2]
    edited = drafts[-1]
    assert edited.content == "original, corrected"
    assert edited.producer_actor_id == self_actor.id  # the reviewer produced this version
    assert edited.commitment == ledger.compute_artifact_commitment("original, corrected")

    assert approval.artifact_version == 2
    assert approval.artifact_commitment == edited.commitment
    assert approval.producer_actor_id == self_actor.id
    assert approval.created_at >= edited.created_at  # the artifact exists before the approval is stamped


def test_an_unchanged_modified_draft_does_not_create_a_version(db, self_actor):
    task, shown = _task_with_draft(db, "same")
    approval = crud.record_approval(db, task.id, self_actor.id, "approved", modified_draft="same")
    assert [d.version for d in crud.get_drafts(db, task.id)] == [1]
    assert approval.artifact_version == shown.version


def test_a_modified_draft_against_a_stale_target_is_not_stored(db, self_actor):
    task, shown = _task_with_draft(db, "read")
    crud.add_draft(db, task_id=task.id, content="moved on")
    with pytest.raises(crud.StaleArtifactError):
        crud.record_approval(
            db, task.id, self_actor.id, "approved", artifact_version=shown.version, modified_draft="my edit"
        )
    assert [d.content for d in crud.get_drafts(db, task.id)] == ["read", "moved on"]


# ------------------------------------------------------------ tamper-evidence


@pytest.mark.parametrize(
    "field, forged",
    [
        ("artifact_ref", "axonrelay://tasks/1/drafts/99"),
        ("artifact_version", 99),
        ("artifact_commitment", "0" * 64),
        ("artifact_commitment_algorithm", "md5"),
        ("producer_actor_id", 4242),
    ],
)
def test_altering_any_artifact_binding_field_is_detected(db, self_actor, field, forged):
    task, _ = _task_with_draft(db)
    victim = crud.record_approval(db, task.id, self_actor.id, "approved", "a")
    crud.record_approval(db, task.id, self_actor.id, "approved", "b")
    assert crud.verify_approval_chain(db, task.id)["valid"] is True

    setattr(victim, field, forged)
    db.commit()

    verdict = crud.verify_approval_chain(db, task.id)
    assert verdict["valid"] is False
    assert verdict["broken_at"] == victim.id


def test_downgrading_the_payload_version_marker_is_detected(db, self_actor):
    """A v2 row relabelled as v1 must not verify as v1: the marker is inside the hash."""
    task, _ = _task_with_draft(db)
    victim = crud.record_approval(db, task.id, self_actor.id, "approved", "a")

    victim.hash_version = None
    db.commit()

    verdict = crud.verify_approval_chain(db, task.id)
    assert verdict["valid"] is False
    assert verdict["broken_at"] == victim.id


def test_the_hash_covers_the_binding(db):
    base = {
        "task_id": 1,
        "reviewer_actor_id": 2,
        "action": "approved",
        "comment": "ok",
        "created_at": datetime(2026, 9, 7, 0, 0, 0),
    }
    binding = ledger.ArtifactBinding(
        ref="axonrelay://tasks/1/drafts/1",
        version=1,
        commitment="a" * 64,
        commitment_algorithm=ledger.COMMITMENT_ALGORITHM,
        producer_actor_id=None,
    )
    v2 = ledger.compute_entry_hash(None, artifact=binding, **base)
    assert v2 == ledger.compute_entry_hash(None, artifact=binding, **base)
    assert v2 != ledger.compute_entry_hash(None, **base)  # v1 payload differs
    other = ledger.ArtifactBinding(**{**binding.__dict__, "version": 2})
    assert v2 != ledger.compute_entry_hash(None, artifact=other, **base)


# --------------------------------------------------------------- legacy rows


def _legacy_v1_approval(db, task, actor, comment, prev_hash=None):
    """A row as record_approval wrote it between migrations 004 and 008."""
    now = datetime.utcnow()
    row = models.Approval(
        task_id=task.id,
        reviewer_actor_id=actor.id,
        action="approved",
        comment=comment,
        created_at=now,
        prev_hash=prev_hash,
        entry_hash=ledger.compute_entry_hash(
            prev_hash,
            task_id=task.id,
            reviewer_actor_id=actor.id,
            action="approved",
            comment=comment,
            created_at=now,
        ),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_pre_binding_rows_verify_and_are_reported_as_not_artifact_bound(db, self_actor):
    task, _ = _task_with_draft(db)
    old = _legacy_v1_approval(db, task, self_actor, "before 009")
    new = crud.record_approval(db, task.id, self_actor.id, "approved", "after 009")

    assert old.artifact_bound is False
    assert old.hash_version is None
    assert new.prev_hash == old.entry_hash  # one chain across the payload change
    assert crud.verify_approval_chain(db, task.id) == {
        "valid": True,
        "broken_at": None,
        "count": 2,
        "legacy": 0,
        "artifact_bound": 1,
        "unbound": 1,
    }


def test_a_legacy_row_is_still_tamper_evident_as_an_event(db, self_actor):
    task, _ = _task_with_draft(db)
    old = _legacy_v1_approval(db, task, self_actor, "before 009")
    crud.record_approval(db, task.id, self_actor.id, "approved", "after 009")

    old.comment = "rewritten"
    db.commit()

    verdict = crud.verify_approval_chain(db, task.id)
    assert verdict["valid"] is False and verdict["broken_at"] == old.id


def test_an_unbacked_draft_is_committed_to_at_approval_time(db, self_actor):
    """A draft row with no commitment (predating 009) gets one before it is bound."""
    task = _task(db)
    db.add(models.Draft(task_id=task.id, version=1, content="old bytes"))
    db.commit()

    approval = crud.record_approval(db, task.id, self_actor.id, "approved")

    draft = crud.get_drafts(db, task.id)[0]
    assert draft.commitment == ledger.compute_artifact_commitment("old bytes")
    assert approval.artifact_commitment == draft.commitment


# ------------------------------------------------- what the binding is not


def test_the_ledger_proves_which_bytes_were_approved_not_that_they_are_still_stored(db, self_actor):
    """Event tamper-evidence and artifact integrity are separate checks.

    Editing the *draft* row leaves the approval chain valid — the chain never
    hashed the content, only its commitment — while the commitment now
    disagrees with the stored bytes. That disagreement is the artifact check.
    """
    task, draft = _task_with_draft(db, "approved bytes")
    approval = crud.record_approval(db, task.id, self_actor.id, "approved")

    draft.content = "swapped after approval"
    db.commit()

    assert crud.verify_approval_chain(db, task.id)["valid"] is True
    assert ledger.compute_artifact_commitment(draft.content) != approval.artifact_commitment
    assert ledger.compute_artifact_commitment("approved bytes") == approval.artifact_commitment


# --------------------------------------------------------- surfaces expose it


def test_rest_and_mcp_responses_expose_the_binding(db, self_actor):
    task, draft = _task_with_draft(db)
    approval = crud.record_approval(db, task.id, self_actor.id, "approved", "ok")

    rest = ApprovalResponse.model_validate(approval).model_dump()
    mcp = approval_to_dict(approval)
    for payload in (rest, mcp):
        assert payload["artifact_bound"] is True
        assert payload["artifact_ref"] == approval.artifact_ref
        assert payload["artifact_version"] == 1
        assert payload["artifact_commitment"] == draft.commitment
        assert payload["artifact_commitment_algorithm"] == ledger.COMMITMENT_ALGORITHM
        assert payload["hash_version"] == 2
        assert payload["entry_hash"] == approval.entry_hash

    assert DraftResponse.model_validate(draft).commitment == draft.commitment
    assert draft_to_dict(draft)["commitment"] == draft.commitment


def test_mcp_decision_tools_accept_a_decision_target():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    for name in ("approve_task", "reject_task"):
        props = tools[name].input_schema["properties"]
        assert {"artifact_version", "expected_commitment"} <= set(props), name
