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
from sqlalchemy import text
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


def test_a_modified_draft_is_always_a_new_version_even_when_identical(db, self_actor):
    """Mirrors the graph, which appends the modified draft to its state unconditionally."""
    task, shown = _task_with_draft(db, "same")
    approval = crud.record_approval(db, task.id, self_actor.id, "approved", modified_draft="same")
    assert [d.version for d in crud.get_drafts(db, task.id)] == [1, 2]
    assert approval.artifact_version == 2
    assert approval.artifact_commitment == shown.commitment  # same bytes, same digest, new version


def test_a_modified_draft_against_a_stale_target_is_not_stored(db, self_actor):
    task, shown = _task_with_draft(db, "read")
    crud.add_draft(db, task_id=task.id, content="moved on")
    with pytest.raises(crud.StaleArtifactError):
        crud.record_approval(
            db, task.id, self_actor.id, "approved", artifact_version=shown.version, modified_draft="my edit"
        )
    assert [d.content for d in crud.get_drafts(db, task.id)] == ["read", "moved on"]


def test_a_stale_decision_on_an_unbacked_draft_persists_nothing(db, self_actor):
    """The lazy commitment fill-in happens after the checks, so a refusal leaves the row untouched."""
    task = _task(db)
    db.add(models.Draft(task_id=task.id, version=1, content="old bytes"))
    db.commit()
    with pytest.raises(crud.StaleArtifactError):
        crud.record_approval(db, task.id, self_actor.id, "approved", expected_commitment="f" * 64)
    db.rollback()
    assert crud.get_drafts(db, task.id)[0].commitment is None


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


@pytest.mark.parametrize("relabel", [1, None])
def test_downgrading_the_payload_version_marker_is_detected(db, self_actor, relabel):
    """A v2 row relabelled as v1 must not verify as v1: the marker is inside the hash."""
    task, _ = _task_with_draft(db)
    victim = crud.record_approval(db, task.id, self_actor.id, "approved", "a")

    victim.hash_version = relabel
    db.commit()

    verdict = crud.verify_approval_chain(db, task.id)
    assert verdict["valid"] is False
    assert verdict["broken_at"] == victim.id


def test_a_future_payload_version_does_not_flag_older_rows(db, self_actor, monkeypatch):
    """Dispatch is on the row's own version, not on equality with the current one.

    Recording under today's version and then advancing the module's current
    version must leave the row verifiable: it is recomputed as what it is, not
    as what the code now writes. Pinned against a version beyond the newest
    payload so the test keeps its meaning after the next bump.
    """
    task, _ = _task_with_draft(db)
    recorded = crud.record_approval(db, task.id, self_actor.id, "approved", "a", decision_key="k1")
    assert recorded.hash_version == ledger.ENTRY_HASH_VERSION
    monkeypatch.setattr(ledger, "ENTRY_HASH_VERSION", ledger.ENTRY_HASH_VERSION + 1)
    assert crud.verify_approval_chain(db, task.id)["valid"] is True


def test_a_v2_row_verifies_without_the_decision_key_field(db, self_actor, monkeypatch):
    """The v3 field must be absent from a v2 payload, not hashed as null.

    Recomputing an older row with `decision_key=None` in the payload would
    change its bytes and turn every migrated entry invalid at once.
    """
    task, draft = _task_with_draft(db)
    monkeypatch.setattr(ledger, "ENTRY_HASH_VERSION", 2)
    v2 = crud.record_approval(db, task.id, self_actor.id, "approved", "a")
    assert v2.hash_version == 2
    monkeypatch.undo()
    assert crud.verify_approval_chain(db, task.id)["valid"] is True


def test_a_decision_key_planted_on_a_pre_hash_row_is_detected(db, self_actor):
    """A pre-004 row has no hash to break, which makes it the tempting place.

    Verification skips such rows as a legacy prefix, so the key check has to
    happen before that skip or this is the one row where a key can be planted
    for free - and the unique index would then refuse the genuine decision it
    belongs to.
    """
    task, draft = _task_with_draft(db)
    unhashed = models.Approval(
        task_id=task.id,
        reviewer_actor_id=self_actor.id,
        action="approved",
        created_at=datetime.utcnow(),
    )
    db.add(unhashed)
    db.commit()
    assert crud.verify_approval_chain(db, task.id)["legacy"] == 1
    assert crud.verify_approval_chain(db, task.id)["valid"] is True

    db.execute(text("PRAGMA ignore_check_constraints = ON"))
    unhashed.decision_key = "planted where there is no hash to break"
    db.commit()
    db.execute(text("PRAGMA ignore_check_constraints = OFF"))

    report = crud.verify_approval_chain(db, task.id)
    assert report["valid"] is False
    assert report["broken_at"] == unhashed.id


def test_claiming_v3_on_an_unhashed_row_does_not_launder_a_planted_key(db, self_actor):
    """`hash_version` is a column too, so a version check alone proves nothing.

    Setting it to 3 satisfies any "is this payload new enough" test and then
    falls straight through the legacy skip, since the row still has no hash to
    recompute. What actually makes a key meaningful is that the row is hashed
    at a version covering it, and both the constraint and the verifier say so.
    """
    task, draft = _task_with_draft(db)
    unhashed = models.Approval(
        task_id=task.id,
        reviewer_actor_id=self_actor.id,
        action="approved",
        created_at=datetime.utcnow(),
    )
    db.add(unhashed)
    db.commit()

    unhashed.hash_version = ledger.DECISION_KEY_SINCE
    unhashed.decision_key = "victim key, on a row with no hash to break"
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.execute(text("PRAGMA ignore_check_constraints = ON"))
    unhashed = crud.get_approvals(db, task.id)[0]
    unhashed.hash_version = ledger.DECISION_KEY_SINCE
    unhashed.decision_key = "victim key, on a row with no hash to break"
    db.commit()
    db.execute(text("PRAGMA ignore_check_constraints = OFF"))

    report = crud.verify_approval_chain(db, task.id)
    assert report["valid"] is False
    assert report["broken_at"] == unhashed.id


def test_a_decision_key_planted_on_a_pre_v3_row_is_detected(db, self_actor, monkeypatch):
    """Free to plant, because that row's hash does not cover the field.

    The harm is not the row itself: the unique index over (task_id,
    decision_key) would then refuse the genuine decision that key belongs to.
    Verification dispatches on the row's own version, so this is caught there
    rather than by the hash.
    """
    task, draft = _task_with_draft(db)
    monkeypatch.setattr(ledger, "ENTRY_HASH_VERSION", 2)
    legacy = crud.record_approval(db, task.id, self_actor.id, "approved", "a")
    monkeypatch.undo()
    assert crud.verify_approval_chain(db, task.id)["valid"] is True

    # The database refuses it outright.
    legacy.decision_key = "a key this payload never hashed"
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # And if that constraint were ever dropped, verification still catches it.
    db.execute(text("PRAGMA ignore_check_constraints = ON"))
    legacy = crud.get_approvals(db, task.id)[0]
    legacy.decision_key = "a key this payload never hashed"
    db.commit()
    db.execute(text("PRAGMA ignore_check_constraints = OFF"))

    report = crud.verify_approval_chain(db, task.id)
    assert report["valid"] is False
    assert report["broken_at"] == legacy.id


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


def _legacy_v1_approval(db, task, actor, comment, prev_hash=None, hash_version=1):
    """A row as record_approval wrote it between migrations 004 and 008.

    Migration 009 stamps such rows hash_version=1; a row it never reached
    (NULL) must verify the same way, so tests cover both.
    """
    now = datetime.utcnow()
    row = models.Approval(
        task_id=task.id,
        reviewer_actor_id=actor.id,
        action="approved",
        comment=comment,
        created_at=now,
        hash_version=hash_version,
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


@pytest.mark.parametrize("hash_version", [1, None], ids=["stamped-by-009", "never-stamped"])
def test_pre_binding_rows_verify_and_are_reported_as_not_artifact_bound(db, self_actor, hash_version):
    task, _ = _task_with_draft(db)
    old = _legacy_v1_approval(db, task, self_actor, "before 009", hash_version=hash_version)
    new = crud.record_approval(db, task.id, self_actor.id, "approved", "after 009")

    assert old.artifact_bound is False
    assert old.hash_version == hash_version
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
        assert payload["hash_version"] == ledger.ENTRY_HASH_VERSION
        assert payload["entry_hash"] == approval.entry_hash

    assert DraftResponse.model_validate(draft).commitment == draft.commitment
    assert draft_to_dict(draft)["commitment"] == draft.commitment


def test_mcp_decision_tools_accept_a_decision_target():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    for name in ("approve_task", "reject_task"):
        props = tools[name].input_schema["properties"]
        assert {"artifact_version", "expected_commitment"} <= set(props), name


# ------------------------------------------------- one decision, one entry


def test_a_decision_key_makes_the_write_idempotent(db, self_actor):
    """The same key twice is the same decision, not a second one (migration 013).

    The interactive path can be replayed by the transport - a 2026-07-28 answer
    round re-sends the whole `tools/call` - so the ledger, not the caller, is
    where "already decided" has to be settled.
    """
    task, draft = _task_with_draft(db)
    first = crud.record_approval(
        db, task.id, self_actor.id, "approved", comment="ok", decision_key="k1", artifact_version=draft.version
    )
    # Refused rather than answered: a caller that cannot tell "I wrote this"
    # from "somebody already had" will go on to do the work that follows the
    # write - here, resuming the graph - twice for one decision.
    with pytest.raises(crud.DuplicateDecisionError) as exc:
        crud.record_approval(
            db, task.id, self_actor.id, "approved", comment="ok", decision_key="k1", artifact_version=draft.version
        )
    assert exc.value.approval.id == first.id
    assert exc.value.approval.entry_hash == first.entry_hash
    assert [a.id for a in crud.get_approvals(db, task.id)] == [first.id]


def test_a_replayed_key_does_not_append_the_edit_either(db, self_actor):
    """The draft version an edit would create is inside the idempotent region."""
    task, draft = _task_with_draft(db)
    crud.record_approval(db, task.id, self_actor.id, "approved", decision_key="k1", modified_draft="edited")
    with pytest.raises(crud.DuplicateDecisionError):
        crud.record_approval(db, task.id, self_actor.id, "approved", decision_key="k1", modified_draft="edited")
    assert [d.version for d in crud.get_drafts(db, task.id)] == [1, 2]
    assert len(crud.get_approvals(db, task.id)) == 1


def test_two_distinct_decisions_are_both_recorded(db, self_actor):
    """Idempotency must not swallow a genuine second decision on the same task."""
    task, draft = _task_with_draft(db)
    crud.record_approval(db, task.id, self_actor.id, "rejected", decision_key="k1")
    second = crud.record_approval(db, task.id, self_actor.id, "approved", decision_key="k2")
    approvals = crud.get_approvals(db, task.id)
    assert [a.action for a in approvals] == ["rejected", "approved"]
    assert second.prev_hash == approvals[0].entry_hash


def test_unkeyed_decisions_do_not_collide_with_each_other(db, self_actor):
    """NULL keys are distinct in the index, so the old callers are unaffected."""
    task, draft = _task_with_draft(db)
    crud.record_approval(db, task.id, self_actor.id, "rejected")
    crud.record_approval(db, task.id, self_actor.id, "rejected")
    assert len(crud.get_approvals(db, task.id)) == 2
    assert all(a.decision_key is None for a in crud.get_approvals(db, task.id))


def test_the_unique_index_is_the_backstop_for_a_race(db, self_actor):
    """Two writers past the lookup must still not both land."""
    task, draft = _task_with_draft(db)
    recorded = crud.record_approval(db, task.id, self_actor.id, "approved", decision_key="k1")
    duplicate = models.Approval(
        task_id=task.id,
        reviewer_actor_id=self_actor.id,
        action="approved",
        created_at=datetime.utcnow(),
        decision_key="k1",
    )
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert [a.id for a in crud.get_approvals(db, task.id)] == [recorded.id]


def test_the_decision_key_is_inside_the_hash(db, self_actor):
    """The server acts on it, so verification has to be able to see it.

    Editing a recorded decision costs a rewrite of every later entry. A control
    field outside the payload would cost nothing - and clearing it defeats the
    replay de-duplication - so it is hashed like everything else the entry
    asserts.
    """
    task, draft = _task_with_draft(db)
    keyed = crud.record_approval(db, task.id, self_actor.id, "approved", decision_key="k1")
    expected = ledger.compute_entry_hash(
        None,
        task_id=task.id,
        reviewer_actor_id=self_actor.id,
        action="approved",
        comment=None,
        created_at=keyed.created_at,
        decision_key="k1",
        artifact=ledger.ArtifactBinding(
            ref=ledger.artifact_ref(task.id, draft.version),
            version=draft.version,
            commitment=draft.commitment,
            commitment_algorithm=draft.commitment_algorithm,
            producer_actor_id=draft.producer_actor_id,
        ),
    )
    assert keyed.entry_hash == expected
    assert crud.verify_approval_chain(db, task.id)["valid"] is True

    # And clearing it is detected, which is the whole point.
    keyed.decision_key = None
    db.commit()
    assert crud.verify_approval_chain(db, task.id)["valid"] is False


def test_two_reviewers_reaching_the_same_verdict_are_two_entries(db, self_actor):
    """The decider is part of a decision's identity, not just the decision.

    A task can carry more than one authorised approver. If the key named only
    the draft and the verdict, the second reviewer's decision would collide
    with the first, go unrecorded, and its caller would be handed somebody
    else's approval — in a ledger whose subject is who approved what.
    """
    from app.mcp.server import _scope_to_reviewer

    task, draft = _task_with_draft(db)
    reviewer_b = models.Actor(type=models.ActorTypeEnum.HUMAN, name="second reviewer")
    db.add(reviewer_b)
    db.commit()

    round_key = "same draft, same verdict, same words"
    assert _scope_to_reviewer(round_key, self_actor.id) != _scope_to_reviewer(round_key, reviewer_b.id)

    first = crud.record_approval(
        db, task.id, self_actor.id, "rejected", decision_key=_scope_to_reviewer(round_key, self_actor.id)
    )
    second = crud.record_approval(
        db, task.id, reviewer_b.id, "rejected", decision_key=_scope_to_reviewer(round_key, reviewer_b.id)
    )
    assert second.id != first.id
    assert second.decision_key != first.decision_key
    assert [a.reviewer_actor_id for a in crud.get_approvals(db, task.id)] == [self_actor.id, reviewer_b.id]
    assert second.prev_hash == first.entry_hash
    assert crud.verify_approval_chain(db, task.id)["valid"] is True


def test_the_same_reviewer_replaying_still_collapses(db, self_actor):
    """Scoping by reviewer must not weaken the retry protection it sits on."""
    from app.mcp.server import _scope_to_reviewer

    task, draft = _task_with_draft(db)
    key = _scope_to_reviewer("one round", self_actor.id)
    first = crud.record_approval(db, task.id, self_actor.id, "approved", decision_key=key)
    with pytest.raises(crud.DuplicateDecisionError) as exc:
        crud.record_approval(db, task.id, self_actor.id, "approved", decision_key=key)
    assert exc.value.approval.id == first.id
    assert len(crud.get_approvals(db, task.id)) == 1
