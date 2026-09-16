"""Tamper-evidence for the approval ledger.

Each approval is chained to the previous one for the same task with a SHA-256
hash: ``entry_hash = sha256(canonical(prev_hash, task_id, reviewer, action,
comment, approved draft/evidence manifest, created_at))``. Recomputing the chain detects any after-the-fact edit
to a recorded approval (a core ask of agent-governance audit trails).

This is *tamper-evident*, not tamper-proof: anyone who can write the database
could also recompute the whole chain. It raises the bar from "silent edit" to
"must rewrite every subsequent entry", and makes verification cheap. Regulatory
-grade signing / timestamping (TSA, 21 CFR Part 11) remains out of scope.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compute_decision_key(
    *,
    task_id: int,
    approval_episode_id: str,
    action: str,
    comment: str | None,
    approved_draft_sha256: str | None,
    evidence_manifest: list[dict] | None,
) -> str:
    canonical = json.dumps(
        {
            "task_id": task_id,
            "approval_episode_id": approval_episode_id,
            "action": action,
            "comment": comment,
            "approved_draft_sha256": approved_draft_sha256,
            "evidence_manifest": evidence_manifest,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hash_text(canonical)


def compute_entry_hash(
    prev_hash: str | None,
    *,
    task_id: int,
    reviewer_actor_id: int | None,
    action: str,
    comment: str | None,
    created_at: datetime,
    approved_draft_sha256: str | None = None,
    evidence_manifest: list[dict] | None = None,
    approval_episode_id: str | None = None,
    decision_key: str | None = None,
) -> str:
    """Deterministic hash of one approval, chained to ``prev_hash``.

    ``created_at`` is formatted with explicit microsecond precision so the hash
    does not depend on a DB round-trip dropping or padding the fractional second
    (it stays stable across SQLite and Postgres). ``prev_hash`` and ``comment``
    are passed through raw — JSON encodes ``None`` as null, so a None comment is
    distinct from an empty-string comment (no tamper blind spot).
    """
    payload = {
        "prev": prev_hash,
        "task_id": task_id,
        "reviewer_actor_id": reviewer_actor_id,
        "action": action,
        "comment": comment,
        "created_at": created_at.isoformat(timespec="microseconds"),
    }
    # Preserve hashes of approvals written before Evidence Clips existed: the
    # new fields participate only when an approval actually records them.
    if approved_draft_sha256 is not None or evidence_manifest is not None or approval_episode_id is not None:
        payload["approved_draft_sha256"] = approved_draft_sha256
        payload["evidence_manifest"] = evidence_manifest
        payload["approval_episode_id"] = approval_episode_id
        payload["decision_key"] = decision_key
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
