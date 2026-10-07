"""Evidence Clips, shared half: commitments and provenance (ADR-014).

A clip is an operator-selected excerpt attached to one task. This module keeps
the part of it a shared ledger may hold: the SHA-256 of the excerpt's exact
UTF-8 text, where it came from, when, and who captured it. The text, the page
title and any model annotations never arrive here. They live with the
capturing client (app/evidence_local.py) and are resolved against the digest
on that side, so a Context Pack is assembled locally from a manifest this
module serves.

Not a memory or RAG layer: clips are explicit, task-scoped, and immutable.
"""

from __future__ import annotations

import re
from collections import Counter

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import ledger, models

#: Citation form a draft uses for a clip, e.g. ``[E-12]``.
EVIDENCE_REF = re.compile(r"\[E-(\d+)\]")

MAX_LIST = 500


class EvidenceError(ValueError):
    """A refused evidence operation. Messages name fields, never submitted values."""


class EvidenceNotFound(EvidenceError):
    """The task or clip does not exist."""


class EvidenceConflict(EvidenceError):
    """The same (task, source, digest) was already captured with different metadata."""


def create_clip(
    db: Session,
    *,
    task_id: int,
    captured_by_actor_id: int | None,
    source_url: str,
    source_type: str,
    content_sha256: str,
    content_algorithm: str = ledger.COMMITMENT_ALGORITHM,
) -> tuple[models.EvidenceClip, bool]:
    """Record a clip commitment. Returns ``(clip, created)``.

    Re-sending the same capture returns the stored row with ``created=False``
    (a double click, a retry after a lost response). The same source and digest
    under a different ``source_type`` is a conflict rather than a silent
    return: the first writer must not be able to decide the second one's
    sensitivity label.
    """
    if not db.get(models.Task, task_id):
        raise EvidenceNotFound(f"Task {task_id} not found")
    if content_algorithm != ledger.COMMITMENT_ALGORITHM:
        raise EvidenceError(f"content_algorithm must be {ledger.COMMITMENT_ALGORITHM}")
    try:
        resolved_type = models.EvidenceSourceTypeEnum(source_type)
    except ValueError:
        raise EvidenceError("source_type must be public or personal") from None

    # The model validators refuse an unsafe URL or a malformed digest before
    # anything touches the session; their messages never repeat the value.
    clip = models.EvidenceClip(
        task_id=task_id,
        captured_by_actor_id=captured_by_actor_id,
        source_url=source_url,
        source_type=resolved_type,
        content_sha256=content_sha256,
        content_algorithm=content_algorithm,
    )
    existing = _find(db, task_id, clip.source_url, content_sha256)
    if existing is None:
        db.add(clip)
        try:
            db.commit()
        except IntegrityError:
            # A concurrent identical capture won the unique constraint.
            db.rollback()
            existing = _find(db, task_id, clip.source_url, content_sha256)
            if existing is None:
                raise
        else:
            db.refresh(clip)
            return clip, True
    if existing.source_type != resolved_type:
        raise EvidenceConflict("this source and digest were already captured with a different source_type")
    return existing, False


def _find(db: Session, task_id: int, source_url: str, content_sha256: str) -> models.EvidenceClip | None:
    return (
        db.query(models.EvidenceClip)
        .filter(
            models.EvidenceClip.task_id == task_id,
            models.EvidenceClip.source_url == source_url,
            models.EvidenceClip.content_sha256 == content_sha256,
        )
        .first()
    )


def get_clip(db: Session, clip_id: int) -> models.EvidenceClip | None:
    return db.get(models.EvidenceClip, clip_id)


def list_clips(db: Session, task_id: int, *, skip: int = 0, limit: int = 100) -> list[models.EvidenceClip]:
    """A task's clips, newest first."""
    if not db.get(models.Task, task_id):
        raise EvidenceNotFound(f"Task {task_id} not found")
    return (
        db.query(models.EvidenceClip)
        .filter(models.EvidenceClip.task_id == task_id)
        .order_by(models.EvidenceClip.captured_at.desc(), models.EvidenceClip.id.desc())
        .offset(max(0, skip))
        .limit(max(1, min(limit, MAX_LIST)))
        .all()
    )


def record_feedback(db: Session, clip_id: int, actor_id: int | None, verdict: str) -> models.EvidenceFeedback:
    """Append one verdict. Earlier verdicts are never changed."""
    clip = db.get(models.EvidenceClip, clip_id)
    if not clip:
        raise EvidenceNotFound(f"Evidence clip {clip_id} not found")
    try:
        resolved = models.EvidenceFeedbackVerdictEnum(verdict)
    except ValueError:
        raise EvidenceError("verdict must be relevant, irrelevant, or misleading") from None
    event = models.EvidenceFeedback(evidence_clip_id=clip.id, actor_id=actor_id, verdict=resolved)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def clip_to_dict(clip: models.EvidenceClip) -> dict:
    """The whole shared record of a clip. There is nothing else to disclose."""
    counts = Counter(str(event.verdict) for event in clip.feedback_events)
    return {
        "id": clip.id,
        "evidence_ref": clip.evidence_ref,
        "task_id": clip.task_id,
        "captured_by_actor_id": clip.captured_by_actor_id,
        "source_url": clip.source_url,
        "source_type": str(clip.source_type),
        "content_sha256": clip.content_sha256,
        "content_algorithm": clip.content_algorithm,
        "captured_at": clip.captured_at.isoformat(),
        "feedback": {verdict.value: counts.get(verdict.value, 0) for verdict in models.EvidenceFeedbackVerdictEnum},
    }


def feedback_to_dict(event: models.EvidenceFeedback) -> dict:
    return {
        "id": event.id,
        "evidence_ref": event.evidence_clip.evidence_ref,
        "actor_id": event.actor_id,
        "verdict": str(event.verdict),
        "created_at": event.created_at.isoformat(),
    }


def validate_draft_references(db: Session, task_id: int, content: str) -> dict:
    """Check that every ``[E-n]`` a draft cites is a clip of *this* task.

    ``manifest`` lists the cited clips once each, in first-citation order, with
    the digest a reader resolves the excerpt against. A clip of another task is
    reported as missing: citing it would point a reviewer at evidence gathered
    for something else.
    """
    if not db.get(models.Task, task_id):
        raise EvidenceNotFound(f"Task {task_id} not found")
    referenced = [f"E-{value}" for value in EVIDENCE_REF.findall(content)]
    ids = {int(ref[2:]) for ref in referenced}
    clips = (
        db.query(models.EvidenceClip)
        .filter(models.EvidenceClip.task_id == task_id, models.EvidenceClip.id.in_(ids))
        .all()
        if ids
        else []
    )
    by_ref = {clip.evidence_ref: clip for clip in clips}
    valid = [ref for ref in referenced if ref in by_ref]
    return {
        "referenced": referenced,
        "valid": valid,
        "missing": [ref for ref in referenced if ref not in by_ref],
        "manifest": [
            {
                "evidence_ref": ref,
                "content_sha256": by_ref[ref].content_sha256,
                "source_url": by_ref[ref].source_url,
            }
            for ref in dict.fromkeys(valid)
        ],
    }
