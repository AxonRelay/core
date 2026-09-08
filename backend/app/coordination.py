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

import posixpath
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app import disclosure, models, territory

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


def normalize_git_dir(git_dir: str | None, clone_path: str) -> str | None:
    """Canonicalise a `git rev-parse --git-common-dir` value for comparison.

    STASH and REFS conflicts are scoped by (host, git_dir), so two spellings of
    one directory must compare equal or sibling worktrees stop contending. Git
    itself is inconsistent here: in the main worktree `--git-common-dir` prints
    the *relative* `.git`, in a linked worktree an absolute path. A relative
    value is therefore resolved against `clone_path`, and both are normalised
    (`..`, `//`, trailing slash). Symlinks cannot be resolved server-side - the
    client should pass the result of `--path-format=absolute` through
    `realpath` when a checkout lives behind one.
    """
    if not git_dir:
        return None
    value = git_dir.strip()
    if not value:
        return None
    if not posixpath.isabs(value):
        value = posixpath.join(clone_path, value)
    value = posixpath.normpath(value)
    return value or None


def _lock_repo_workspaces(db: DBSession, repo: str) -> None:
    """Serialise claim writers for one repo with a row lock on its workspaces.

    Granting a claim is a check-then-insert: read the live claims, insert if
    nothing contends. Two transactions can both read "free" and both insert,
    and then two sessions each believe they hold an exclusive lease. The
    approval ledger has the same shape and locks the task row (`crud.record_
    approval`); here the unit of contention is the repo, so we lock every
    workspace row of the repo, in id order so two lockers cannot deadlock on
    each other. A workspace registered concurrently is not in our lock set,
    but its own claim locks every pre-existing row - including ours - so the
    two writers still serialise on a common row.

    `FOR UPDATE` is a no-op on SQLite, which serialises writers globally anyway.
    Under Postgres READ COMMITTED the conflict query that follows the lock sees
    the rows the previous holder committed.
    """
    db.query(models.Workspace).filter(models.Workspace.repo == repo).order_by(
        models.Workspace.id
    ).with_for_update().all()


def get_or_create_workspace(
    db: DBSession,
    host: str,
    repo: str,
    clone_path: str,
    label: str | None = None,
    git_dir: str | None = None,
) -> models.Workspace:
    """Find the Workspace for this checkout, creating it on first sight.

    Identity is (host, repo, clone_path): two clones of the same repo on the
    same machine are distinct workspaces, and the same clone seen again after a
    restart is the same workspace.
    """
    git_dir = normalize_git_dir(git_dir, clone_path)
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
        if git_dir:
            workspace.git_dir = git_dir
        db.commit()
        db.refresh(workspace)
        return workspace

    workspace = models.Workspace(
        host=host,
        repo=repo,
        clone_path=clone_path,
        git_dir=git_dir,
        label=label,
        created_at=now,
        last_seen_at=now,
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
    focus_code: models.FocusCodeEnum | None = None,
    label: str | None = None,
    git_dir: str | None = None,
) -> models.Session:
    """Start or resume this actor's session in this workspace.

    Idempotent: an actor that is already active in the workspace resumes that
    session (refreshing branch / focus / heartbeat) instead of forking a second
    one, so its claims and unread relays survive an agent restart.
    """
    actor = get_or_create_actor(db, actor_name, actor_type)
    workspace = get_or_create_workspace(db, host=host, repo=repo, clone_path=clone_path, label=label, git_dir=git_dir)

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
        if focus_code is not None:
            session.focus_code = focus_code
    else:
        session = models.Session(
            actor_id=actor.id,
            workspace_id=workspace.id,
            branch=branch,
            focus=focus,
            focus_code=focus_code,
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
    focus_code: models.FocusCodeEnum | None = None,
    branch: str | None = None,
) -> models.Session | None:
    """Refresh a session's liveness and, optionally, what it is working on."""
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session:
        return None
    session.last_heartbeat_at = datetime.utcnow()
    if focus is not None:
        session.focus = focus
    if focus_code is not None:
        session.focus_code = focus_code
    if branch is not None:
        session.branch = branch
    db.commit()
    db.refresh(session)
    return session


def end_session(db: DBSession, session_id: int) -> tuple[models.Session, list[int]] | None:
    """Close a session and release every claim it still holds.

    Releasing on exit is what keeps the board honest when an agent finishes
    cleanly; ``expires_at`` is the fallback for the ones that do not.

    Returns the session and the ids of the claims *this call* released - not
    every claim the session ever released, which would overstate what the exit
    freed.
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
    released_ids = [claim.id for claim in held]
    for claim in held:
        claim.status = models.ClaimStatusEnum.RELEASED
        claim.released_at = now

    session.status = models.SessionStatusEnum.ENDED
    session.ended_at = now
    db.commit()
    db.refresh(session)
    return session, released_ids


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
        if claim.resource is not None:
            continue  # resource claims are matched by domain, not by path overlap
        if exclude_session_id is not None and claim.session_id == exclude_session_id:
            continue
        if not _conflicts_with(claim, mode):
            continue
        pairs = territory.any_overlap(paths, list(claim.paths or []))
        if pairs:
            conflicts.append(
                disclosure.conflict_view(
                    claim,
                    holder=describe_holder(claim.session),
                    overlapping_paths=sorted({theirs for _, theirs in pairs}),
                )
            )
    return conflicts


def describe_holder(session: models.Session | None) -> dict[str, Any] | None:
    """Who holds a claim, in the terms a peer needs to go find them.

    What "the terms a peer needs" means depends on the mode, so the projection
    lives in app/disclosure.py with the rest of the boundary policy.
    """
    if not session:
        return None
    return disclosure.holder_view(session, stale=is_stale(session))


def claim_territory(
    db: DBSession,
    *,
    session_id: int,
    paths: list[str],
    repo: str | None = None,
    mode: models.ClaimModeEnum = models.ClaimModeEnum.EXCLUSIVE,
    reason: str | None = None,
    reason_code: models.ClaimReasonCodeEnum | None = None,
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
    _lock_repo_workspaces(db, repo)
    conflicts = find_conflicts(db, repo=repo, paths=normalized, mode=mode, exclude_session_id=session_id, now=now)

    if conflicts and not force:
        return {"granted": False, "claim": None, "conflicts": conflicts}
    if conflicts:
        _release_overridden(db, conflicts, now)

    claim = models.Claim(
        session_id=session_id,
        repo=repo,
        paths=normalized,
        mode=mode,
        reason=reason,
        reason_code=reason_code,
        status=models.ClaimStatusEnum.HELD,
        forced_over=[c["claim_id"] for c in conflicts] or None,
        created_at=now,
        expires_at=now + timedelta(minutes=ttl),
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return {"granted": True, "claim": claim, "conflicts": conflicts}


def _release_overridden(db: DBSession, conflicts: list[dict[str, Any]], now: datetime) -> None:
    """Retire the claims a forced claim displaces.

    A force is a statement that the new holder owns the resource *now*. Leaving
    the displaced claims HELD would contradict that: the guard would keep
    refusing the very session that just forced its way in, and the board would
    show two exclusive holders. The displaced claims are transitioned to
    RELEASED (never deleted) and the new claim's ``forced_over`` records which
    ones, so the override stays visible to the session it displaced.
    """
    ids = [c["claim_id"] for c in conflicts]
    if not ids:
        return
    for claim in db.query(models.Claim).filter(models.Claim.id.in_(ids)).all():
        if claim.status == models.ClaimStatusEnum.HELD:
            claim.status = models.ClaimStatusEnum.RELEASED
            claim.released_at = now


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


# =============================================================================
# Git resources: the shared singletons a path claim cannot describe
# =============================================================================
#
# A checkout has exactly one working tree, one index, one HEAD, one stash stack.
# `git stash pop` names no path at all, so no path claim can guard it - which is
# how one session ends up applying another's parked work. These claims cover
# that, and each resource carries its own sharing boundary (see
# models.ClaimResourceEnum).


def _shares_git_dir(a: models.Workspace | None, b: models.Workspace | None) -> bool:
    """Do two workspaces share one `.git` (and therefore one stash stack)?

    Same host and same `git_dir` means sibling worktrees of one clone. When
    either `git_dir` is unknown we fall back to (host, repo), which over-reports
    rather than missing a collision - the same bias as path overlap.
    """
    if a is None or b is None:
        return False
    if a.id == b.id:
        return True
    if a.host != b.host:
        return False
    if a.git_dir and b.git_dir:
        return a.git_dir == b.git_dir
    return a.repo == b.repo


def _resource_domains_overlap(
    resource: models.ClaimResourceEnum,
    a: models.Workspace | None,
    b: models.Workspace | None,
) -> bool:
    """Whether two workspaces contend for `resource`.

    WORKTREE is per checkout; STASH and REFS are per clone and therefore reach
    across sibling worktrees; REMOTE is per repo, because every clone on every
    host pushes to the same remote refs.
    """
    if a is None or b is None:
        return False
    if resource == models.ClaimResourceEnum.WORKTREE:
        return a.id == b.id
    if resource == models.ClaimResourceEnum.REMOTE:
        return a.repo == b.repo
    return _shares_git_dir(a, b)


def find_resource_conflicts(
    db: DBSession,
    *,
    session_id: int,
    resource: models.ClaimResourceEnum,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Live claims on `resource` held by other sessions that contend with this one."""
    now = now or datetime.utcnow()
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session:
        raise ValueError(f"Session {session_id} not found")

    held = (
        db.query(models.Claim)
        .filter(
            models.Claim.status == models.ClaimStatusEnum.HELD,
            models.Claim.expires_at > now,
            models.Claim.resource == resource,
            models.Claim.session_id != session_id,
        )
        .order_by(models.Claim.created_at.asc())
        .all()
    )

    conflicts = []
    for claim in held:
        other = claim.session.workspace if claim.session else None
        if not _resource_domains_overlap(resource, session.workspace, other):
            continue
        conflicts.append(disclosure.conflict_view(claim, holder=describe_holder(claim.session)))
    return conflicts


def claim_resource(
    db: DBSession,
    *,
    session_id: int,
    resource: models.ClaimResourceEnum,
    reason: str | None = None,
    reason_code: models.ClaimReasonCodeEnum | None = None,
    ttl_minutes: int = DEFAULT_CLAIM_TTL_MINUTES,
    force: bool = False,
) -> dict[str, Any]:
    """Take an exclusive lease on a shared git resource.

    Always exclusive: there is no useful "shared read" of a stash stack you are
    about to mutate. Refused on conflict unless ``force``, like a path claim.
    Writers for one repo are serialised by a row lock (`_lock_repo_workspaces`)
    so two sessions cannot both be granted the same singleton.
    """
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session:
        raise ValueError(f"Session {session_id} not found")
    if session.status != models.SessionStatusEnum.ACTIVE:
        raise ValueError(f"Session {session_id} is not active")

    ttl = max(1, min(int(ttl_minutes), MAX_CLAIM_TTL_MINUTES))
    now = datetime.utcnow()
    if session.workspace:
        _lock_repo_workspaces(db, session.workspace.repo)
    conflicts = find_resource_conflicts(db, session_id=session_id, resource=resource, now=now)

    if conflicts and not force:
        return {"granted": False, "claim": None, "conflicts": conflicts}
    if conflicts:
        _release_overridden(db, conflicts, now)

    claim = models.Claim(
        session_id=session_id,
        repo=session.workspace.repo if session.workspace else "",
        paths=[],
        resource=resource,
        mode=models.ClaimModeEnum.EXCLUSIVE,
        reason=reason,
        reason_code=reason_code,
        status=models.ClaimStatusEnum.HELD,
        forced_over=[c["claim_id"] for c in conflicts] or None,
        created_at=now,
        expires_at=now + timedelta(minutes=ttl),
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return {"granted": True, "claim": claim, "conflicts": conflicts}


def guard_git_operation(
    db: DBSession,
    *,
    session_id: int,
    resource: models.ClaimResourceEnum,
    host: str | None = None,
    clone_path: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Answer "may this session touch `resource` right now?" for the git wrapper.

    Read-only and side-effect free: it reports who is in the way, and the caller
    (`tools/gitsafe`) decides. A session that holds the claim itself is allowed;
    so is one where nobody holds it, because claiming is advisory and we do not
    want the guard to block work that was never coordinated.

    A session id says who is asking, not where. `git -C other-clone reset
    --hard` run with a session registered elsewhere would otherwise be judged
    against the wrong workspace's claims. When the wrapper reports where the
    command actually runs (``host``, ``clone_path``) and that is not the
    session's own checkout, the caller is treated as an *unregistered* actor in
    that checkout - refused whenever anyone holds the resource there.
    """
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session:
        raise ValueError(f"Session {session_id} not found")

    if host and clone_path:
        workspace = session.workspace
        if workspace is None or workspace.host != host or workspace.clone_path != clone_path:
            result = guard_unregistered_caller(db, host=host, clone_path=clone_path, resource=resource, repo=repo)
            result["session_id"] = session_id
            result["caller"]["session_workspace_mismatch"] = True
            return result

    # A push can land on a repo other than the one this session registered for
    # (`git push upstream --force`, a URL, `--repo=`). The remote's identity is
    # the *destination*, so when the wrapper names one that is not the
    # session's repo, judge the push against claims on that repo - where this
    # session, whatever it holds here, is a stranger.
    if resource == models.ClaimResourceEnum.REMOTE and repo and session.workspace and repo != session.workspace.repo:
        result = guard_unregistered_caller(
            db,
            host=host or session.workspace.host,
            clone_path=clone_path or session.workspace.clone_path,
            resource=resource,
            repo=repo,
        )
        result["session_id"] = session_id
        result["caller"]["destination_repo_mismatch"] = True
        return result

    conflicts = find_resource_conflicts(db, session_id=session_id, resource=resource)
    return {
        "allowed": not conflicts,
        "resource": str(resource),
        "session_id": session_id,
        "conflicts": conflicts,
    }


def guard_unregistered_caller(
    db: DBSession,
    *,
    host: str,
    clone_path: str,
    resource: models.ClaimResourceEnum,
    repo: str | None = None,
) -> dict[str, Any]:
    """Whether an *unregistered* caller may touch `resource` in this checkout.

    This is the subagent case: a process spawned inside someone's working
    directory that never registered a session, and so cannot be the holder of
    any claim. It is located by (host, clone_path) rather than by session, and
    is refused whenever **anyone** holds the resource in a contending domain -
    including the session that owns the very clone it is running in, which is
    the point: the parent claimed the worktree precisely so that nothing else
    would disturb it.

    When nobody holds the resource it is allowed through. Claims are advisory,
    and a guard that blocked all uncoordinated work would simply be turned off.

    For REMOTE the checkout does not matter - the remote is shared by every
    clone of the repo - so an unknown workspace contends with every REMOTE claim
    on ``repo`` when the wrapper could name it, and with every REMOTE claim at
    all when it could not.
    """
    now = datetime.utcnow()
    workspace = (
        db.query(models.Workspace)
        .filter(models.Workspace.host == host, models.Workspace.clone_path == clone_path)
        .first()
    )

    held = (
        db.query(models.Claim)
        .filter(
            models.Claim.status == models.ClaimStatusEnum.HELD,
            models.Claim.expires_at > now,
            models.Claim.resource == resource,
        )
        .order_by(models.Claim.created_at.asc())
        .all()
    )

    conflicts = []
    for claim in held:
        other = claim.session.workspace if claim.session else None
        if resource == models.ClaimResourceEnum.REMOTE:
            # The remote is shared by every clone; only the destination repo
            # matters. Prefer the repo the wrapper named (the push target),
            # then the caller's own registered repo; with neither, no claim
            # can be proven to be about a different remote.
            target = repo or (workspace.repo if workspace else None)
            if other is None or (target and other.repo != target):
                continue
        elif workspace is None:
            # An unknown workspace (this clone has never registered) still
            # contends with a same-host claim: we cannot prove it is a
            # different checkout.
            if other is None or other.host != host:
                continue
        elif not _resource_domains_overlap(resource, workspace, other):
            continue
        conflicts.append(disclosure.conflict_view(claim, holder=describe_holder(claim.session)))

    return {
        "allowed": not conflicts,
        "resource": str(resource),
        "caller": disclosure.guard_caller_view(host=host, clone_path=clone_path, repo=repo, workspace=workspace),
        "conflicts": conflicts,
    }


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
    code: models.RelayCodeEnum | None = None,
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
        code=code,
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
    return disclosure.inbox_entry_view(relay, receipt)


def ack_relay(
    db: DBSession,
    *,
    relay_id: int,
    session_id: int,
    note: str | None = None,
    ack_code: models.AckCodeEnum | None = None,
) -> models.RelayReceipt:
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
    if ack_code is not None:
        receipt.ack_code = ack_code
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

    # A relay is still open while any addressee has not acked it - including one
    # nobody has read yet (no receipt row at all), hence the outer join.
    # `distinct` is explicit: several unacked receipts on one broadcast would
    # otherwise yield the same relay once per receipt.
    unacked = (
        db.query(models.Relay)
        .outerjoin(models.RelayReceipt, models.RelayReceipt.relay_id == models.Relay.id)
        .filter(models.RelayReceipt.acked_at.is_(None))
        .distinct()
        .order_by(models.Relay.created_at.desc())
        .limit(20)
        .all()
    )
    if repo:
        unacked = [r for r in unacked if r.to_repo in (None, repo)]

    return {
        "generated_at": now.isoformat(),
        "repo": repo,
        "sessions": [disclosure.board_session_view(s, stale=is_stale(s, now)) for s in sessions],
        "claims": [disclosure.board_claim_view(c, holder=describe_holder(c.session)) for c in claims],
        "open_relays": [disclosure.open_relay_view(r) for r in unacked],
    }
