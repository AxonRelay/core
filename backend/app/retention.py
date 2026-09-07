"""Retention and deletion for operational coordination metadata (issue #26).

The coordination plane accumulates rows that describe *how* work happened and
stop being useful the moment it has: sessions that ended, claims that expired
or were released, relays every recipient has acknowledged. Keeping them forever
is not neutral — on a shared instance each one is a record of who was working
where and when, long after anyone needs it.

So they expire. What does **not** expire, ever, is the evidence:

* `approvals` and `drafts` — the ledger. An approval names the artifact it
  decided on (ADR-009); deleting either would make the hash chain unverifiable,
  which is the opposite of the point.
* `tasks`, `actors`, `agent_definitions` — what the ledger's rows refer to.
* `safe_events` — the append-only envelope feed (ADR-010), which is itself the
  minimal evidence a shared instance is meant to keep.
* `credentials` — access control, removed by revoking, not by ageing.

`sweep` therefore names the four tables it may touch and nothing else, and a
test asserts that a sweep of a database full of ledger rows changes none of
them. The windows are deliberately generous: this is a coordination board, and
deleting a session somebody is about to reconnect to is worse than keeping it a
week too long.

    python -m app.retention --dry-run     # what would go, and why
    python -m app.retention               # apply
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session as DBSession

from app import models

logger = logging.getLogger("axonrelay.retention")

#: An ended session is kept this long: long enough to explain a collision that
#: happened yesterday, short enough that a shared board is not a work diary.
ENDED_SESSION_DAYS = 30

#: A claim that has expired or been released has done its job. It is kept a
#: little while so "who held this when I was refused?" is still answerable.
FINISHED_CLAIM_DAYS = 14

#: A relay every recipient has acknowledged is a delivered message.
ACKED_RELAY_DAYS = 30

#: An unacknowledged relay is still someone's inbox item, so it lives much
#: longer — but not forever, because an agent that never came back would
#: otherwise pin it permanently. A broadcast always takes this window: there is
#: no list of everyone it was for, so "delivered" cannot be established.
UNACKED_RELAY_DAYS = 180

#: The only tables a sweep may delete from.
SWEEPABLE = ("sessions", "claims", "relays", "relay_receipts")

#: Tables a sweep must never touch. Asserted by the tests, not just documented.
PRESERVED = (
    "approvals",
    "drafts",
    "tasks",
    # Who was responsible for a task, and the artifacts it points at: both are
    # what an approval row means, so they age with the ledger, not with the board.
    "task_assignments",
    "external_links",
    "actors",
    "agent_definitions",
    "safe_events",
    "credentials",
    # A workspace is the stable identity sessions and claims hang off. Removing
    # one would renumber a checkout that is still in use.
    "workspaces",
)


@dataclass
class SweepResult:
    """What a sweep removed, per table."""

    sessions: int = 0
    claims: int = 0
    relays: int = 0
    relay_receipts: int = 0

    def total(self) -> int:
        return self.sessions + self.claims + self.relays + self.relay_receipts

    def as_dict(self) -> dict[str, int]:
        return {
            "sessions": self.sessions,
            "claims": self.claims,
            "relays": self.relays,
            "relay_receipts": self.relay_receipts,
        }


def sweep(db: DBSession, *, now: datetime | None = None, dry_run: bool = False) -> SweepResult:
    """Delete operational metadata past its window. Never touches the ledger.

    Everything doomed is decided **before** anything is deleted, because the
    tables cascade into each other: a session owns its claims and its relay
    receipts at the database level, so deleting sessions first would absorb
    rows this function is supposed to count, and would silently strip the
    receipts that record who had already acknowledged a relay — making a
    delivered message look unread again for the rest of its long window.
    """
    now = now or datetime.utcnow()

    # --- sessions that have been finished long enough to forget -------------
    doomed_sessions = (
        db.query(models.Session)
        .filter(
            models.Session.status == models.SessionStatusEnum.ENDED,
            models.Session.ended_at.isnot(None),
            models.Session.ended_at < now - timedelta(days=ENDED_SESSION_DAYS),
        )
        .all()
    )
    doomed_session_ids = {s.id for s in doomed_sessions}

    # --- claims whose work is over -----------------------------------------
    # The window runs from when the claim *finished*, not from when it was
    # taken: a long-lived claim released a minute ago is exactly the one a
    # peer is about to ask about ("who held this when I was refused?").
    def finished_at(claim: models.Claim) -> datetime:
        return claim.released_at or claim.expires_at

    doomed_claims = [
        claim
        for claim in db.query(models.Claim).all()
        if (claim.status == models.ClaimStatusEnum.RELEASED or claim.expires_at < now)
        and finished_at(claim) < now - timedelta(days=FINISHED_CLAIM_DAYS)
    ]
    doomed_claims += [
        claim
        for claim in db.query(models.Claim).filter(models.Claim.session_id.in_(doomed_session_ids or {-1})).all()
        if claim not in doomed_claims
    ]

    # --- relays that have been delivered, or have waited long enough --------
    acked_cutoff = now - timedelta(days=ACKED_RELAY_DAYS)
    stale_cutoff = now - timedelta(days=UNACKED_RELAY_DAYS)
    doomed_relays = []
    for relay in db.query(models.Relay).filter(models.Relay.created_at < acked_cutoff).all():
        if relay.created_at < stale_cutoff:
            doomed_relays.append(relay)
            continue
        # A receipt exists only for a session that has *read* the relay, so
        # "every receipt is acked" says nothing about recipients who never
        # opened it. A broadcast therefore only ever leaves on the long
        # window; a directed relay may leave once its addressees have acked.
        broadcast = relay.to_actor_id is None and relay.to_workspace_id is None and relay.to_repo is None
        receipts = list(relay.receipts or [])
        delivered = bool(receipts) and all(r.acked_at is not None for r in receipts)
        surviving = [r for r in receipts if r.session_id not in doomed_session_ids]
        if delivered and (not broadcast or not surviving):
            # Directed and acknowledged; or acknowledged by recipients whose
            # sessions are going, which would leave the relay with no receipts
            # at all - and a relay with no receipts reads as unacknowledged, so
            # keeping it would show a delivered message as open again.
            doomed_relays.append(relay)

    doomed_relay_ids = {r.id for r in doomed_relays}

    # --- receipts: those of doomed relays, and those of doomed sessions -----
    doomed_receipts = [
        receipt
        for receipt in db.query(models.RelayReceipt).all()
        if receipt.relay_id in doomed_relay_ids or receipt.session_id in doomed_session_ids
    ]

    result = SweepResult(
        sessions=len(doomed_sessions),
        claims=len(doomed_claims),
        relays=len(doomed_relays),
        relay_receipts=len(doomed_receipts),
    )
    if dry_run:
        return result

    for row in doomed_receipts:
        db.delete(row)
    for row in doomed_claims:
        db.delete(row)
    for row in doomed_relays:
        db.delete(row)
    for row in doomed_sessions:
        db.delete(row)
    db.commit()
    logger.info("retention sweep removed %s", result.as_dict())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.retention", description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report what would be deleted and change nothing")
    args = parser.parse_args(argv)

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        result = sweep(db, dry_run=args.dry_run)
    finally:
        db.close()
    verb = "would remove" if args.dry_run else "removed"
    print(f"retention {verb}: {result.as_dict()} (total {result.total()})")
    print(
        "windows: ended sessions "
        f"{ENDED_SESSION_DAYS}d, finished claims {FINISHED_CLAIM_DAYS}d, "
        f"acked relays {ACKED_RELAY_DAYS}d, unacked relays {UNACKED_RELAY_DAYS}d"
    )
    print("never swept: " + ", ".join(PRESERVED))
    return 0


if __name__ == "__main__":
    sys.exit(main())
