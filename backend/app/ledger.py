"""Tamper-evidence for the approval ledger.

Each approval is chained to the previous one for the same task with a SHA-256
hash. Recomputing the chain detects any after-the-fact edit to a recorded
approval (a core ask of agent-governance audit trails).

Two payload versions exist:

* **v1** (migration 004 → 008): ``sha256(canonical(prev_hash, task_id, reviewer,
  action, comment, created_at))``. It protects the approval *event* but does not
  say which artifact was approved.
* **v2** (migrations 009 → 012): the same fields plus an **artifact binding** —
  the resource reference and version of the draft that was reviewed, the
  draft's content commitment (``sha256`` of its UTF-8 bytes) with the algorithm
  label, and the Actor that produced it. Altering any of those after the fact
  breaks the chain just as editing the comment does.
* **v3** (migration 013 →): the same, plus ``decision_key`` — the name of the
  round a decision answered, which is what makes a replayed `tools/call` record
  once instead of twice (ADR-013). It is inside the hash because the server
  *acts* on it: a value verification could not see could be cleared to defeat
  the de-duplication, and unlike editing a decision that would cost the
  attacker nothing.

Rows record which payload they were hashed with (``Approval.hash_version``), so
a v1 row is verified as v1 and reported as *not artifact-bound* rather than as
tampered, and a v2 row keeps verifying unchanged.

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

A note on what "raises the bar" is allowed to mean. The first guarantee above
says a database writer could recompute the whole chain; the cost of an edit is
a rewrite of every later entry. A field that changes the server's behaviour and
sits *outside* the payload has no such cost at all, which is a difference in
kind rather than degree — so ``decision_key`` (migration 013) is inside the v3
payload. That was the reason to keep the ledger's operational surface as small
as it is: a column the server acts on, that verification cannot see, is a
liability, and the way to avoid one is usually to not need it.
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
ENTRY_HASH_VERSION = 3
#: First payload version that carries the decision key. Older rows have no key
#: and are hashed without the field, so they verify exactly as they were written.
DECISION_KEY_SINCE = 3
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
    decision_key: str | None = None,
) -> str:
    """Deterministic hash of one approval, chained to ``prev_hash``.

    Without ``artifact`` this is the v1 payload (used to verify rows recorded
    before migration 009). With it, the payload carries the version marker
    (``version``, defaulting to the current ``ENTRY_HASH_VERSION``) and the
    binding, so a v2 row whose ``hash_version`` is flipped back to v1 still
    fails verification — the version marker is inside the hashed bytes. From
    v3 the payload also carries ``decision_key``; a v2 row omits the field
    entirely rather than hashing it as null, so it verifies unchanged.

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
        # Only from v3. A v2 row must hash exactly the bytes it was written
        # with, so the field is absent rather than null for those.
        if (version or ENTRY_HASH_VERSION) >= DECISION_KEY_SINCE:
            payload["decision_key"] = decision_key
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
