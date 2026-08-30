"""Coordination service - the shared workspace for a fleet of agents.

The ledger (`app.ledger`, `app.crud`) records decisions after the fact. This
module handles the problem that appears *before* the decision, once several
agents work at the same time across several machines and several clones of the
same repository:

1. **Presence** - who is working where, on which branch, on what.
2. **Territory** - advisory, expiring leases on paths, so an agent can see a
   collision coming instead of discovering it in a merge conflict.
3. **Relays** - durable, addressed, pull-delivered messages between sessions.

The delivery model is deliberately *semi-synchronous*: nothing here interrupts a
peer. An agent publishes what it is doing, and reads what others published, when
it next takes a turn. That matches how coding agents actually run - in bursts,
on their own clock - and it degrades safely, because a peer that never reads its
inbox blocks nobody.

All three are pull-based and idempotent, so an agent that crashes and restarts
re-registers into the same session and picks its state back up.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app import models, territory

#: A session that has not sent a heartbeat within this window is reported as
#: stale. Its claims stay live until they expire - staleness is a hint to the
#: reader, not a revocation.
STALE_AFTER_MINUTES = 30

#: Default lifetime of a territory claim.
DEFAULT_CLAIM_TTL_MINUTES = 60

#: Upper bound on a claim's lifetime, so a typo cannot park a lease for a week.
MAX_CLAIM_TTL_MINUTES = 24 * 60


# =============================================================================
# Presence: workspaces and sessions
# =============================================================================


def get_or_create_actor(db: DBSession, name: str, actor_type: models.ActorTypeEnum) -> models.Actor:
    """Find an Actor by (type, name), creating it if this is its first appearance.

    An AI actor also gets a 1:1 AgentDefinition so it shows up in the existing
    agent tooling rather than being a bare Actor row the rest of the app cannot
    describe.
    """
    actor = (
        db.query(models.Actor)
        .filter(models.Actor.type == actor_type, models.Actor.name == name)
        .order_by(models.Actor.id.asc())
        .first()
    )
    if actor:
        return actor

    actor = models.Actor(type=actor_type, name=name)
    db.add(actor)
    db.flush()

    if actor_type == models.ActorTypeEnum.AI:
        db.add(
            models.AgentDefinition(
                actor_id=actor.id,
                agent_type=models.AgentTypeEnum.ASSISTANT,
                description=f"Coding agent registered via the coordination layer ({name}).",
            )
        )
    db.commit()
    db.refresh(actor)
    return actor


def get_or_create_workspace(
    db: DBSession,
    host: str,
    repo: str,
    clone_path: str,
    label: str | None = None,
) -> models.Workspace:
    """Find the Workspace for this checkout, creating it on first sight.

    Identity is (host, repo, clone_path): two clones of the same repo on the
    same machine are distinct workspaces, and the same clone seen again after a
    restart is the same workspace.
    """
    workspace = (
        db.query(models.Workspace)
        .filter(
            models.Workspace.host == host,
            models.Workspace.repo == repo,
            models.Workspace.clone_path == clone_path,
        )
        .first()
    )
    now = datetime.utcnow()
    if workspace:
        workspace.last_seen_at = now
        if label:
            workspace.label = label
        db.commit()
        db.refresh(workspace)
        return workspace

    workspace = models.Workspace(
        host=host, repo=repo, clone_path=clone_path, label=label, created_at=now, last_seen_at=now
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace


def register_session(
    db: DBSession,
    *,
    actor_name: str,
    host: str,
    repo: str,
    clone_path: str,
    actor_type: models.ActorTypeEnum = models.ActorTypeEnum.AI,
    branch: str | None = None,
    focus: str | None = None,
    label: str | None = None,
) -> models.Session:
    """Start or resume this actor's session in this workspace.

    Idempotent: an actor that is already active in the workspace resumes that
    session (refreshing branch / focus / heartbeat) instead of forking a second
    one, so its claims and unread relays survive an agent restart.
    """
    actor = get_or_create_actor(db, actor_name, actor_type)
    workspace = get_or_create_workspace(db, host=host, repo=repo, clone_path=clone_path, label=label)

    now = datetime.utcnow()
    session = (
        db.query(models.Session)
        .filter(
            models.Session.actor_id == actor.id,
            models.Session.workspace_id == workspace.id,
            models.Session.status == models.SessionStatusEnum.ACTIVE,
        )
        .order_by(models.Session.id.desc())
        .first()
    )
    if session:
        session.last_heartbeat_at = now
        if branch is not None:
            session.branch = branch
        if focus is not None:
            session.focus = focus
    else:
        session = models.Session(
            actor_id=actor.id,
            workspace_id=workspace.id,
            branch=branch,
            focus=focus,
            status=models.SessionStatusEnum.ACTIVE,
            started_at=now,
            last_heartbeat_at=now,
        )
        db.add(session)

    db.commit()
    db.refresh(session)
    return session


def heartbeat_session(
    db: DBSession,
    session_id: int,
    *,
    focus: str | None = None,
    branch: str | None = None,
) -> models.Session | None:
    """Refresh a session's liveness and, optionally, what it is working on."""
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session:
        return None
    session.last_heartbeat_at = datetime.utcnow()
    if focus is not None:
        session.focus = focus
    if branch is not None:
        session.branch = branch
    db.commit()
    db.refresh(session)
    return session


def end_session(db: DBSession, session_id: int) -> models.Session | None:
    """Close a session and release every claim it still holds.

    Releasing on exit is what keeps the board honest when an agent finishes
    cleanly; ``expires_at`` is the fallback for the ones that do not.
    """
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session:
        return None

    now = datetime.utcnow()
    held = (
        db.query(models.Claim)
        .filter(models.Claim.session_id == session_id, models.Claim.status == models.ClaimStatusEnum.HELD)
        .all()
    )
    for claim in held:
        claim.status = models.ClaimStatusEnum.RELEASED
        claim.released_at = now

    session.status = models.SessionStatusEnum.ENDED
    session.ended_at = now
    db.commit()
    db.refresh(session)
    return session


def is_stale(session: models.Session, now: datetime | None = None) -> bool:
    """True when a session has gone quiet for longer than the staleness window."""
    now = now or datetime.utcnow()
    return (now - session.last_heartbeat_at) > timedelta(minutes=STALE_AFTER_MINUTES)


def list_sessions(
    db: DBSession,
    *,
    repo: str | None = None,
    include_ended: bool = False,
) -> list[models.Session]:
    """Sessions on the board, most recently active first."""
    query = db.query(models.Session).join(models.Workspace, models.Session.workspace_id == models.Workspace.id)
    if not include_ended:
        query = query.filter(models.Session.status == models.SessionStatusEnum.ACTIVE)
    if repo:
        query = query.filter(models.Workspace.repo == repo)
    return query.order_by(models.Session.last_heartbeat_at.desc()).all()


# =============================================================================
# Territory: advisory claims
# =============================================================================


def live_claims(db: DBSession, *, repo: str | None = None, now: datetime | None = None) -> list[models.Claim]:
    """Claims that are currently in force: HELD and not yet expired."""
    now = now or datetime.utcnow()
    query = db.query(models.Claim).filter(
        models.Claim.status == models.ClaimStatusEnum.HELD,
        models.Claim.expires_at > now,
    )
    if repo:
        query = query.filter(models.Claim.repo == repo)
    return query.order_by(models.Claim.created_at.asc()).all()


def _conflicts_with(existing: models.Claim, mode: models.ClaimModeEnum) -> bool:
    """Reader/writer rule: two SHARED claims coexist, anything else collides."""
    return not (existing.mode == models.ClaimModeEnum.SHARED and mode == models.ClaimModeEnum.SHARED)


def find_conflicts(
    db: DBSession,
    *,
    repo: str,
    paths: list[str],
    mode: models.ClaimModeEnum = models.ClaimModeEnum.EXCLUSIVE,
    exclude_session_id: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Live claims by *other* sessions whose paths can touch ``paths``.

    A session never conflicts with itself - re-claiming your own territory is a
    renewal, not a collision.
    """
    now = now or datetime.utcnow()
    conflicts: list[dict[str, Any]] = []
    for claim in live_claims(db, repo=repo, now=now):
        if exclude_session_id is not None and claim.session_id == exclude_session_id:
            continue
        if not _conflicts_with(claim, mode):
            continue
        pairs = territory.any_overlap(paths, list(claim.paths or []))
        if pairs:
            conflicts.append(
                {
                    "claim_id": claim.id,
                    "session_id": claim.session_id,
                    "mode": str(claim.mode),
                    "paths": list(claim.paths or []),
                    "overlapping_paths": sorted({theirs for _, theirs in pairs}),
                    "reason": claim.reason,
                    "expires_at": claim.expires_at.isoformat(),
                    "holder": describe_holder(claim.session),
                }
            )
    return conflicts


def describe_holder(session: models.Session | None) -> dict[str, Any] | None:
    """Who holds a claim, in the terms a peer needs to go find them."""
    if not session:
        return None
    workspace = session.workspace
    return {
        "session_id": session.id,
        "actor": session.actor.name if session.actor else None,
        "host": workspace.host if workspace else None,
        "clone_path": workspace.clone_path if workspace else None,
        "branch": session.branch,
        "focus": session.focus,
        "stale": is_stale(session),
    }


def claim_territory(
    db: DBSession,
    *,
    session_id: int,
    paths: list[str],
    repo: str | None = None,
    mode: models.ClaimModeEnum = models.ClaimModeEnum.EXCLUSIVE,
    reason: str | None = None,
    ttl_minutes: int = DEFAULT_CLAIM_TTL_MINUTES,
    force: bool = False,
) -> dict[str, Any]:
    """Take an advisory lease on ``paths``, reporting any collision.

    On a conflict the claim is **refused** unless ``force=True``. Refusing by
    default is what turns this from a log into a coordination mechanism; forcing
    stays available because a human operator overriding a stale agent is a normal
    move, and the override is recorded on the claim (``forced_over``) rather than
    passing silently.

    Returns ``{"granted": bool, "claim": ..., "conflicts": [...]}``.
    """
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session:
        raise ValueError(f"Session {session_id} not found")
    if session.status != models.SessionStatusEnum.ACTIVE:
        raise ValueError(f"Session {session_id} is not active")

    normalized = _normalize_paths(paths)
    repo = repo or (session.workspace.repo if session.workspace else None)
    if not repo:
        raise ValueError("repo could not be resolved for this session")

    ttl = max(1, min(int(ttl_minutes), MAX_CLAIM_TTL_MINUTES))
    now = datetime.utcnow()
    conflicts = find_conflicts(db, repo=repo, paths=normalized, mode=mode, exclude_session_id=session_id, now=now)

    if conflicts and not force:
        return {"granted": False, "claim": None, "conflicts": conflicts}

    claim = models.Claim(
        session_id=session_id,
        repo=repo,
        paths=normalized,
        mode=mode,
        reason=reason,
        status=models.ClaimStatusEnum.HELD,
        forced_over=[c["claim_id"] for c in conflicts] or None,
        created_at=now,
        expires_at=now + timedelta(minutes=ttl),
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return {"granted": True, "claim": claim, "conflicts": conflicts}


def _normalize_paths(paths: list[str]) -> list[str]:
    """Canonicalize and de-duplicate a claim's path patterns, order preserved."""
    if not paths:
        raise ValueError("A claim needs at least one path")
    seen: list[str] = []
    for raw in paths:
        norm = territory.normalize_path(raw)
        if norm not in seen:
            seen.append(norm)
    return seen


def release_claim(db: DBSession, claim_id: int) -> models.Claim | None:
    """Release one claim. Already-released claims are returned unchanged."""
    claim = db.query(models.Claim).filter(models.Claim.id == claim_id).first()
    if not claim:
        return None
    if claim.status == models.ClaimStatusEnum.HELD:
        claim.status = models.ClaimStatusEnum.RELEASED
        claim.released_at = datetime.utcnow()
        db.commit()
        db.refresh(claim)
    return claim


def release_session_claims(db: DBSession, session_id: int) -> int:
    """Release every claim a session still holds. Returns how many were released."""
    now = datetime.utcnow()
    held = (
        db.query(models.Claim)
        .filter(models.Claim.session_id == session_id, models.Claim.status == models.ClaimStatusEnum.HELD)
        .all()
    )
    for claim in held:
        claim.status = models.ClaimStatusEnum.RELEASED
        claim.released_at = now
    db.commit()
    return len(held)


# =============================================================================
# Relays: durable, addressed, pull-delivered messages
# =============================================================================


def send_relay(
    db: DBSession,
    *,
    subject: str,
    body: str | None = None,
    from_session_id: int | None = None,
    to_actor_id: int | None = None,
    to_workspace_id: int | None = None,
    to_repo: str | None = None,
    kind: models.RelayKindEnum = models.RelayKindEnum.NOTE,
    in_reply_to_id: int | None = None,
) -> models.Relay:
    """Post a message to an audience.

    Leaving every ``to_*`` empty broadcasts to the fleet. Addressing is by
    audience rather than by a live connection, so the message waits for a
    recipient that is currently offline instead of being dropped.
    """
    from_actor_id = None
    if from_session_id is not None:
        sender = db.query(models.Session).filter(models.Session.id == from_session_id).first()
        if not sender:
            raise ValueError(f"Session {from_session_id} not found")
        from_actor_id = sender.actor_id

    relay = models.Relay(
        from_session_id=from_session_id,
        from_actor_id=from_actor_id,
        to_actor_id=to_actor_id,
        to_workspace_id=to_workspace_id,
        to_repo=to_repo,
        kind=kind,
        subject=subject,
        body=body,
        in_reply_to_id=in_reply_to_id,
        created_at=datetime.utcnow(),
    )
    db.add(relay)
    db.commit()
    db.refresh(relay)
    return relay


def _addressed_to(session: models.Session):
    """SQL predicate for "this relay is addressed to this session"."""
    workspace = session.workspace
    clauses = [
        models.Relay.to_actor_id == session.actor_id,
        models.Relay.to_workspace_id == session.workspace_id,
        # Broadcast: no audience narrowed at all.
        (models.Relay.to_actor_id.is_(None))
        & (models.Relay.to_workspace_id.is_(None))
        & (models.Relay.to_repo.is_(None)),
    ]
    if workspace:
        clauses.append(models.Relay.to_repo == workspace.repo)
    return or_(*clauses)


def read_inbox(
    db: DBSession,
    session_id: int,
    *,
    include_acked: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Relays addressed to this session, oldest first, marking them read.

    Reading is recorded per recipient (`RelayReceipt`), so one peer acking a
    broadcast does not hide it from the others. A session never receives its own
    messages.
    """
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session:
        raise ValueError(f"Session {session_id} not found")

    candidates = (
        db.query(models.Relay)
        .filter(_addressed_to(session))
        .filter(or_(models.Relay.from_session_id.is_(None), models.Relay.from_session_id != session_id))
        .order_by(models.Relay.created_at.asc(), models.Relay.id.asc())
        .all()
    )

    receipts = {
        r.relay_id: r for r in db.query(models.RelayReceipt).filter(models.RelayReceipt.session_id == session_id).all()
    }

    now = datetime.utcnow()
    out: list[dict[str, Any]] = []
    for relay in candidates:
        receipt = receipts.get(relay.id)
        if receipt and receipt.acked_at and not include_acked:
            continue
        if receipt is None:
            receipt = models.RelayReceipt(relay_id=relay.id, session_id=session_id, read_at=now)
            db.add(receipt)
        elif receipt.read_at is None:
            receipt.read_at = now
        out.append(relay_to_inbox_entry(relay, receipt))
        if len(out) >= limit:
            break

    db.commit()
    return out


def relay_to_inbox_entry(relay: models.Relay, receipt: models.RelayReceipt | None) -> dict[str, Any]:
    """One inbox row: the message plus this recipient's state on it."""
    sender = relay.from_session
    return {
        "relay_id": relay.id,
        "kind": str(relay.kind),
        "subject": relay.subject,
        "body": relay.body,
        "in_reply_to_id": relay.in_reply_to_id,
        "created_at": relay.created_at.isoformat(),
        "from": {
            "session_id": relay.from_session_id,
            "actor": relay.from_actor.name if relay.from_actor else None,
            "host": sender.workspace.host if sender and sender.workspace else None,
            "repo": sender.workspace.repo if sender and sender.workspace else None,
            "clone_path": sender.workspace.clone_path if sender and sender.workspace else None,
        },
        "acked": bool(receipt and receipt.acked_at),
    }


def ack_relay(db: DBSession, *, relay_id: int, session_id: int, note: str | None = None) -> models.RelayReceipt:
    """Acknowledge a relay as this session, optionally with a reply note.

    Acking removes it from this session's inbox but leaves it in everyone
    else's, and the receipt is what makes "who has seen this" answerable later.
    """
    receipt = (
        db.query(models.RelayReceipt)
        .filter(models.RelayReceipt.relay_id == relay_id, models.RelayReceipt.session_id == session_id)
        .first()
    )
    now = datetime.utcnow()
    if receipt is None:
        receipt = models.RelayReceipt(relay_id=relay_id, session_id=session_id, read_at=now)
        db.add(receipt)
    receipt.acked_at = now
    if note is not None:
        receipt.ack_note = note
    db.commit()
    db.refresh(receipt)
    return receipt


# =============================================================================
# The board: one read that answers "what is going on right now"
# =============================================================================


def board(db: DBSession, *, repo: str | None = None) -> dict[str, Any]:
    """A snapshot of the fleet: who is active, what is claimed, what is unread.

    Built as a single call so an agent can orient itself at the start of a turn
    with one tool call rather than four.
    """
    now = datetime.utcnow()
    sessions = list_sessions(db, repo=repo)
    claims = live_claims(db, repo=repo, now=now)

    unacked = (
        db.query(models.Relay)
        .outerjoin(models.RelayReceipt, models.RelayReceipt.relay_id == models.Relay.id)
        .filter(models.RelayReceipt.acked_at.is_(None))
        .order_by(models.Relay.created_at.desc())
        .limit(20)
        .all()
    )
    if repo:
        unacked = [r for r in unacked if r.to_repo in (None, repo)]

    return {
        "generated_at": now.isoformat(),
        "repo": repo,
        "sessions": [
            {
                "session_id": s.id,
                "actor": s.actor.name if s.actor else None,
                "actor_type": str(s.actor.type) if s.actor else None,
                "host": s.workspace.host if s.workspace else None,
                "repo": s.workspace.repo if s.workspace else None,
                "clone_path": s.workspace.clone_path if s.workspace else None,
                "branch": s.branch,
                "focus": s.focus,
                "stale": is_stale(s, now),
                "last_heartbeat_at": s.last_heartbeat_at.isoformat(),
            }
            for s in sessions
        ],
        "claims": [
            {
                "claim_id": c.id,
                "repo": c.repo,
                "paths": list(c.paths or []),
                "mode": str(c.mode),
                "reason": c.reason,
                "expires_at": c.expires_at.isoformat(),
                "forced_over": c.forced_over,
                "holder": describe_holder(c.session),
            }
            for c in claims
        ],
        "open_relays": [
            {
                "relay_id": r.id,
                "kind": str(r.kind),
                "subject": r.subject,
                "to_repo": r.to_repo,
                "to_actor_id": r.to_actor_id,
                "to_workspace_id": r.to_workspace_id,
                "from_actor": r.from_actor.name if r.from_actor else None,
                "created_at": r.created_at.isoformat(),
            }
            for r in unacked
        ],
    }
