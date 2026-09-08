"""Tamper-evidence for the approval ledger.

Each approval is chained to the previous one for the same task with a SHA-256
hash. Recomputing the chain detects any after-the-fact edit to a recorded
approval (a core ask of agent-governance audit trails).

Two payload versions exist:

* **v1** (migration 004 → 008): ``sha256(canonical(prev_hash, task_id, reviewer,
  action, comment, created_at))``. It protects the approval *event* but does not
  say which artifact was approved.
* **v2** (migration 009 →): the same fields plus an **artifact binding** — the
  resource reference and version of the draft that was reviewed, the draft's
  content commitment (``sha256`` of its UTF-8 bytes) with the algorithm label,
  and the Actor that produced it. Altering any of those after the fact breaks
  the chain just as editing the comment does. Rows record which payload they
  were hashed with (``Approval.hash_version``), so a v1 row is verified as v1
  and reported as *not artifact-bound* rather than as tampered.

Three guarantees that are easy to conflate, and what this module gives:

* **Tamper-evident events** — yes. Anyone who can write the database could also
  recompute the whole chain; this raises the bar from "silent edit" to "must
  rewrite every subsequent entry" and makes verification cheap.
* **Artifact integrity** — only relative to the commitment. The ledger proves an
  approval targeted content with a given hash; whether the stored draft still
  matches that hash is a separate check (``Draft.commitment``), and the ledger
  never stores external artifact contents.
* **Regulatory-grade signing / timestamping** (qualified signatures, TSA,
  21 CFR Part 11, non-repudiation) — out of scope, as before.
* **The operational fields added by migration 013** (``decision_key``,
  ``resumed_at``, ``edited_artifact``) — outside the chain, deliberately.
  They describe how a decision was *handled*, not what was decided:
  ``resumed_at`` is written after the entry, so no entry hash could cover it
  without being invalidated by its own legitimate update. The consequence is
  worth stating rather than leaving implicit: someone who can write the
  database can change what the server does next — flip ``resumed_at`` back to
  NULL and an old decision is re-delivered — at lower cost than editing the
  decision itself, which still requires rewriting every subsequent entry. Both
  fields are exposed on every approval payload so the state is at least
  observable; the chain is what makes an edited *decision* evident, and it has
  never been what stops a database writer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime

#: Label recorded next to every commitment so a future algorithm change is
#: distinguishable from a mismatch. Kept in sync with migration 009's backfill.
COMMITMENT_ALGORITHM = "sha256-utf8-v1"

#: Payload version written by ``record_approval`` today.
ENTRY_HASH_VERSION = 2
#: First payload version that carries an artifact binding. Verification and
#: ``Approval.artifact_bound`` compare against this, not against the current
#: version, so a future v3 does not make every v2 row look tampered.
ARTIFACT_BINDING_SINCE = 2
#: Shape of a commitment as it travels over the wire (REST, MCP, envelopes).
SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"


def compute_artifact_commitment(content: str) -> str:
    """SHA-256 of the artifact's UTF-8 bytes — the commitment a producer computes.

    In the full-text (local PoC) mode AxonRelay holds the content and computes
    this itself; in a metadata-only mode a source-side producer computes the
    same value and sends only the digest.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def artifact_ref(task_id: int, version: int) -> str:
    """Stable reference for a draft: the MCP resource URI that serves it."""
    return f"axonrelay://tasks/{task_id}/drafts/{version}"


@dataclass(frozen=True)
class ArtifactBinding:
    """What an approval was recorded against. Every field is hashed."""

    ref: str | None
    version: int | None
    commitment: str | None
    commitment_algorithm: str | None
    producer_actor_id: int | None


def compute_entry_hash(
    prev_hash: str | None,
    *,
    task_id: int,
    reviewer_actor_id: int | None,
    action: str,
    comment: str | None,
    created_at: datetime,
    artifact: ArtifactBinding | None = None,
    version: int | None = None,
) -> str:
    """Deterministic hash of one approval, chained to ``prev_hash``.

    Without ``artifact`` this is the v1 payload (used to verify rows recorded
    before migration 009). With it, the payload carries the version marker
    (``version``, defaulting to the current ``ENTRY_HASH_VERSION``) and the
    binding, so a v2 row whose ``hash_version`` is flipped back to v1 still
    fails verification — the version marker is inside the hashed bytes.

    ``created_at`` is formatted with explicit microsecond precision so the hash
    does not depend on a DB round-trip dropping or padding the fractional second
    (it stays stable across SQLite and Postgres). ``prev_hash`` and ``comment``
    are passed through raw — JSON encodes ``None`` as null, so a None comment is
    distinct from an empty-string comment (no tamper blind spot).
    """
    payload: dict = {
        "prev": prev_hash,
        "task_id": task_id,
        "reviewer_actor_id": reviewer_actor_id,
        "action": action,
        "comment": comment,
        "created_at": created_at.isoformat(timespec="microseconds"),
    }
    if artifact is not None:
        # The version marker inside the hash is the *row's* version when
        # verifying (so an older row is recomputed exactly as it was written)
        # and the current version when recording.
        payload["v"] = version or ENTRY_HASH_VERSION
        payload["artifact"] = asdict(artifact)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
