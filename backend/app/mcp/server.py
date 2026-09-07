"""AxonRelay MCP server.

Exposes AxonRelay as an MCP server so the operator can drive it from any
MCP-aware client (Claude Code is the first-class target). Tools mirror the
HTTP API but operate at the service / SQLAlchemy layer directly to avoid an
internal HTTP hop.

Run with:
    python -m app.mcp.server                     # stdio, for a local IDE client
    python -m app.mcp.server --http --port 8765  # Streamable HTTP, for remote clients

Streamable HTTP is what lets several machines share one AxonRelay: point every
device's MCP client at the same instance (over Tailscale or a Cloudflare Tunnel
- see deploy/DEPLOYMENT.md) and they share one ledger and one coordination board.
Exposing it publicly is not supported: there is no per-caller authentication, so
the transport must stay on a private network.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from app import authz, coordination, crud, langgraph_client, models, safe_envelope, service, territory
from app.database import SessionLocal
from app.mcp import http_auth
from app.mcp.serializers import (
    actor_to_dict,
    agent_definition_to_dict,
    approval_to_dict,
    claim_to_dict,
    draft_to_dict,
    relay_to_dict,
    session_to_dict,
    task_to_dict,
)

mcp = MCPServer("axonrelay")


@contextmanager
def _session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class AuthorizationMiddleware:
    """Resolve the caller and check the scope of every inbound MCP message.

    One middleware instead of a check inside thirty tools: the SDK wraps every
    request with `(ctx, call_next)`, so the decision happens in one place and a
    tool cannot be added without one — `authz.TOOL_SCOPES` must name it, and a
    structural test fails if it does not.

    `ctx.request` is the HTTP request the transport attached; it is `None` on
    stdio, which is the operator's own process and runs as the loopback
    principal (ADR-011 records that trust assumption). Headers are read only
    to look a credential up by digest — never treated as an identity assertion
    in themselves, and never logged.
    """

    async def __call__(self, ctx, call_next):
        method = ctx.method
        request = getattr(ctx, "request", None)
        transport_is_local = request is None
        if not transport_is_local and not authz.require_auth():
            transport_is_local = True  # enforcement off: same trust as stdio

        # initialize, tools/list, ping and notifications address no resource,
        # so they resolve a principal but carry no scope.
        scope = self._scope_for(method, ctx.params)

        with _session() as db:
            try:
                principal = authz.resolve(
                    db,
                    authorization=(request.headers.get("authorization") if request is not None else None),
                    transport_is_local=transport_is_local,
                )
                if scope is not None:
                    principal.require(scope)
            except authz.AuthzError as e:
                raise ToolError(str(e)) from None

        with authz.bind(principal):
            return await call_next(ctx)

    @staticmethod
    def _scope_for(method: str, params) -> authz.Scope | None:
        """The scope this message needs, or None when it reads and writes nothing."""
        params = params or {}
        if method == "tools/call":
            name = params.get("name")
            if name in authz.TOOL_SCOPES:
                return authz.TOOL_SCOPES[name]
            # An unknown tool is the SDK's METHOD_NOT_FOUND to report, not ours.
            return None
        if method == "resources/read":
            uri = str(params.get("uri", ""))
            for template, scope in authz.RESOURCE_SCOPES.items():
                if _uri_matches(template, uri):
                    return scope
            return None
        return None


def _uri_matches(template: str, uri: str) -> bool:
    """Does a concrete resource URI come from this template? ({placeholders} match one segment)."""
    pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", re.escape(template).replace(r"\{", "{").replace(r"\}", "}")) + "$"
    return re.match(pattern, uri) is not None


mcp.middleware.append(AuthorizationMiddleware())


def _free_text_surface() -> None:
    """Refuse, value-free, when the instance runs in Safe Envelope mode (app/safe_envelope.py).

    Raised as ToolError so the SDK returns the fixed message as the tool
    result instead of logging a traceback that could carry the arguments.
    """
    try:
        safe_envelope.refuse_free_text_if_safe_mode()
    except safe_envelope.SafeModeRefused as e:
        raise ToolError(str(e)) from None


def _resolve_status(status: str | None) -> models.TaskStatusEnum | None:
    if not status:
        return None
    try:
        return models.TaskStatusEnum(status)
    except ValueError as e:
        raise ValueError(f"Invalid status '{status}'") from e


def _resolve_role(role: str) -> models.AssignmentRoleEnum:
    try:
        return models.AssignmentRoleEnum(role)
    except ValueError as e:
        raise ValueError(f"Invalid assignment role '{role}'") from e


# ========== Task Tools ==========


@mcp.tool()
def list_tasks(status: str | None = None, limit: int = 50) -> list[dict]:
    """List tasks across the operator's workspace, newest first.

    Args:
        status: Optional filter — one of draft, waiting_review, waiting_approval,
                approved, rejected, needs_revision, completed, cancelled.
        limit: Max rows (default 50).
    """
    with _session() as db:
        tasks = crud.list_tasks(db, status=_resolve_status(status), limit=limit)
        return [task_to_dict(t) for t in tasks]


@mcp.tool()
def list_pending_approvals() -> list[dict]:
    """List tasks awaiting the operator's approval (the unified inbox)."""
    with _session() as db:
        actor_id = authz.acting_actor_id(db)
        if not actor_id:
            return []
        tasks = crud.list_pending_approvals(db, actor_id=actor_id)
        return [task_to_dict(t) for t in tasks]


@mcp.tool()
def get_task(task_id: int) -> dict:
    """Get a task with its assignments, drafts, and approvals."""
    with _session() as db:
        task = crud.get_task(db, task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        payload = task_to_dict(task)
        payload["drafts"] = [draft_to_dict(d) for d in crud.get_drafts(db, task_id)]
        payload["approvals"] = [approval_to_dict(a) for a in crud.get_approvals(db, task_id)]
        return payload


@mcp.tool()
def get_drafts(task_id: int) -> list[dict]:
    """Get the full draft history for a task."""
    with _session() as db:
        if not crud.get_task(db, task_id):
            raise ValueError(f"Task {task_id} not found")
        return [draft_to_dict(d) for d in crud.get_drafts(db, task_id)]


@mcp.tool()
def verify_task_ledger(task_id: int) -> dict:
    """Verify the tamper-evident approval hash chain for a task.

    Returns {"valid": bool, "broken_at": approval id or None, "count": int,
    "legacy": int, "artifact_bound": int, "unbound": int}. valid=False means a
    recorded approval was altered or reordered after the fact (any of its
    artifact-binding fields included); "legacy" counts pre-hash-chain rows that
    are not covered; "artifact_bound" counts entries that name the exact draft
    version and commitment they decided on, "unbound" the rest.
    """
    with _session() as db:
        if not crud.get_task(db, task_id):
            raise ValueError(f"Task {task_id} not found")
        return crud.verify_approval_chain(db, task_id)


@mcp.tool()
async def create_task(
    title: str,
    description: str | None = None,
    assignments: list[dict[str, Any]] | None = None,
) -> dict:
    """Create a new task and allocate a LangGraph Platform thread.

    Args:
        title: Task title.
        description: Optional longer description / context.
        assignments: Optional list of {actor_id: int, role: str} dicts.
    """
    _free_text_surface()
    with _session() as db:
        creator_actor_id = authz.acting_actor_id(db)

        thread_id = await langgraph_client.create_thread(
            metadata={"title": title, "creator_actor_id": creator_actor_id}
        )
        task = crud.create_task(
            db=db,
            thread_id=thread_id,
            title=title,
            creator_actor_id=creator_actor_id,
            description=description,
        )

        for assignment in assignments or []:
            actor_id = assignment.get("actor_id")
            role_value = assignment.get("role", "executor")
            if not actor_id:
                continue
            role = _resolve_role(role_value)
            crud.create_task_assignment(db, task_id=task.id, actor_id=int(actor_id), role=role)

        db.refresh(task)
        return task_to_dict(task)


def _sync_state(db, task: models.Task, result: dict[str, Any]) -> None:
    """Project a Platform run result back into the Postgres ledger.

    Delegates to the shared service so the MCP and HTTP paths cannot drift.
    """
    values = langgraph_client.extract_values(result)
    waiting = langgraph_client.is_waiting_for_human(result)
    service.project_run_state(db, task, values, waiting)


@mcp.tool()
async def run_task(task_id: int) -> dict:
    """Kick off graph execution on Platform. Blocks until interrupt or completion."""
    _free_text_surface()
    with _session() as db:
        task = crud.get_task(db, task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        initial_state = {
            "task_id": task.id,
            "title": task.title,
            "description": task.description,
            "drafts": [],
            "reviewer_comments": [],
            "iteration": 0,
        }
        result = await langgraph_client.run_until_interrupt(task.thread_id, initial_state)
        _sync_state(db, task, result)
        db.refresh(task)
        return task_to_dict(task)


async def _apply_decision(
    task_id: int,
    *,
    action: str,
    comment: str | None,
    modified_draft: str | None = None,
    artifact_version: int | None = None,
    expected_commitment: str | None = None,
) -> dict:
    """Record an approve/reject in the ledger and resume the Platform thread.

    Shared by the approve_task / reject_task tools and the interactive
    review_pending_task (elicitation) tool so they cannot drift.

    The ledger entry is written first and binds to the exact draft decided on
    (a modified draft becomes a new version before the entry is recorded). If
    `artifact_version` / `expected_commitment` no longer match the task's
    latest draft, nothing is recorded and the call returns
    status="stale_decision".
    """
    with _session() as db:
        task = crud.get_task(db, task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task.status != models.TaskStatusEnum.WAITING_APPROVAL:
            raise ValueError(f"Task {task_id} is not waiting for approval (status={task.status})")

        # The reviewer is the authenticated caller (app/authz.py).
        reviewer_actor_id = authz.acting_actor_id(db)

        try:
            approval = crud.record_approval(
                db,
                task_id=task_id,
                reviewer_actor_id=reviewer_actor_id,
                action=action,
                comment=comment,
                artifact_version=artifact_version,
                expected_commitment=expected_commitment,
                modified_draft=modified_draft if action == "approved" else None,
            )
        except crud.StaleArtifactError as e:
            db.rollback()
            return {"status": "stale_decision", "task_id": task_id, "reason": str(e)}
        except crud.ArtifactRequiredError as e:
            db.rollback()
            return {"status": "no_artifact", "task_id": task_id, "reason": str(e)}
        except crud.LedgerError as e:
            db.rollback()
            raise ToolError(str(e)) from None
        resume_payload = {"decision": action, "human_comment": comment}
        if action == "approved":
            resume_payload["modified_draft"] = modified_draft
        result = await langgraph_client.resume_thread(task.thread_id, resume_payload)
        _sync_state(db, task, result)
        db.refresh(task)
        payload = task_to_dict(task)
        payload["approval"] = approval_to_dict(approval)
        return payload


@mcp.tool()
async def approve_task(
    task_id: int,
    comment: str | None = None,
    modified_draft: str | None = None,
    artifact_version: int | None = None,
    expected_commitment: str | None = None,
) -> dict:
    """Approve a task that's WAITING_APPROVAL. Optionally include comment / edit.

    Pass artifact_version and/or expected_commitment (from get_drafts) to bind
    the decision to the draft you actually read; if the task moved on, nothing
    is recorded and status="stale_decision" is returned. A modified_draft is
    stored as a new draft version and the approval binds to that version.
    """
    _free_text_surface()
    return await _apply_decision(
        task_id,
        action="approved",
        comment=comment,
        modified_draft=modified_draft,
        artifact_version=artifact_version,
        expected_commitment=expected_commitment,
    )


@mcp.tool()
async def reject_task(
    task_id: int,
    comment: str | None = None,
    reason: str | None = None,
    artifact_version: int | None = None,
    expected_commitment: str | None = None,
) -> dict:
    """Reject a task; revision loop continues unless the iteration cap is hit.

    artifact_version / expected_commitment work as in approve_task: the
    rejection is recorded against the draft you read, or not at all.
    """
    _free_text_surface()
    combined = " | ".join(p for p in [comment, reason] if p) or None
    return await _apply_decision(
        task_id,
        action="rejected",
        comment=combined,
        artifact_version=artifact_version,
        expected_commitment=expected_commitment,
    )


class _ApprovalDecision(BaseModel):
    """Elicitation schema for an interactive approval decision."""

    approve: bool = Field(description="Approve the draft? false = reject / request revision.")
    comment: str = Field(default="", description="Optional note recorded in the approval ledger.")
    modified_draft: str = Field(
        default="",
        description="Optional edited draft text (used only when approving); leave blank to keep the draft as-is.",
    )


@mcp.tool()
async def review_pending_task(task_id: int, ctx: Context[None, None]) -> dict:
    """Interactively review a WAITING_APPROVAL task.

    Shows the current draft and reviewer feedback, then asks for your decision
    through the MCP client's native elicitation prompt, records it in the
    tamper-evident ledger, and resumes the graph. Clients that do not support
    elicitation should use approve_task / reject_task directly instead.

    If the task changes between display and decision (the draft no longer matches
    what was shown, or it is no longer waiting), the call returns
    status="stale_decision" and records nothing, so you never approve unseen content.
    The decision is bound to the draft version and commitment that were shown,
    and the check is made under the ledger's row lock, so a concurrent writer
    cannot slip a different draft in between the check and the record. A task
    with no draft returns status="no_artifact" before asking anything.
    """
    _free_text_surface()
    with _session() as db:
        task = crud.get_task(db, task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task.status != models.TaskStatusEnum.WAITING_APPROVAL:
            raise ValueError(f"Task {task_id} is not waiting for approval (status={task.status})")
        title, feedback = task.title, task.feedback
        # Show the latest *artifact* — the draft row the decision will bind
        # to — not the task's current_draft mirror, which PUT /tasks can
        # rewrite without creating a version. What is shown and what is
        # recorded must be the same object.
        drafts = crud.get_drafts(db, task_id)
        if not drafts:
            return {
                "status": "no_artifact",
                "task_id": task_id,
                "reason": "Task has no draft to decide on; nothing was shown and nothing is recorded.",
            }
        shown = drafts[-1]
        draft, shown_version, shown_commitment = shown.content, shown.version, shown.commitment

    # Elicit outside the DB session — don't pin a session across user interaction.
    message = (
        f"Task #{task_id}: {title}\n\n"
        f"--- Draft (v{shown_version}) ---\n{draft}\n\n"
        f"--- Reviewer feedback ---\n{feedback or '(none)'}\n\n"
        "Approve this draft?"
    )
    result = await ctx.elicit(message=message, schema=_ApprovalDecision)
    if result.action != "accept" or not result.data:
        return {"status": "no_decision", "elicitation_action": result.action, "task_id": task_id}

    # Staleness guard, part 1: the task may have left WAITING_APPROVAL while we
    # awaited the human. Part 2 — "is the latest draft still the one shown?" —
    # is enforced inside record_approval under the task row lock, by passing
    # the shown version and commitment as the decision's target.
    with _session() as db:
        current = crud.get_task(db, task_id)
        if not current or current.status != models.TaskStatusEnum.WAITING_APPROVAL:
            return {
                "status": "stale_decision",
                "task_id": task_id,
                "reason": "Task changed since it was shown; no decision recorded. Re-run review_pending_task.",
            }

    decision = result.data
    if decision.approve:
        return await _apply_decision(
            task_id,
            action="approved",
            comment=decision.comment or None,
            modified_draft=decision.modified_draft or None,
            artifact_version=shown_version,
            expected_commitment=shown_commitment,
        )
    return await _apply_decision(
        task_id,
        action="rejected",
        comment=decision.comment or None,
        artifact_version=shown_version,
        expected_commitment=shown_commitment,
    )


# ========== Agent / Actor Tools ==========


@mcp.tool()
def list_agents(agent_type: str | None = None, is_active: bool | None = None) -> list[dict]:
    """List AI agent definitions."""
    with _session() as db:
        agent_type_enum = None
        if agent_type:
            try:
                agent_type_enum = models.AgentTypeEnum(agent_type)
            except ValueError as e:
                raise ValueError(f"Invalid agent type '{agent_type}'") from e
        agents = crud.get_agent_definitions(db, agent_type=agent_type_enum, is_active=is_active)
        return [agent_definition_to_dict(a) for a in agents]


@mcp.tool()
def create_agent(
    name: str,
    agent_type: str,
    description: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict:
    """Create an AI agent definition (and its underlying Actor)."""
    _free_text_surface()
    with _session() as db:
        try:
            agent_type_enum = models.AgentTypeEnum(agent_type)
        except ValueError as e:
            raise ValueError(f"Invalid agent type '{agent_type}'") from e
        agent = crud.create_agent_definition(
            db=db, name=name, agent_type=agent_type_enum, description=description, config=config
        )
        return agent_definition_to_dict(agent)


@mcp.tool()
def update_agent(
    agent_id: int,
    name: str | None = None,
    agent_type: str | None = None,
    description: str | None = None,
    config: dict[str, Any] | None = None,
    is_active: bool | None = None,
) -> dict:
    """Update an AI agent definition."""
    _free_text_surface()
    with _session() as db:
        agent_type_enum = None
        if agent_type:
            try:
                agent_type_enum = models.AgentTypeEnum(agent_type)
            except ValueError as e:
                raise ValueError(f"Invalid agent type '{agent_type}'") from e
        agent = crud.update_agent_definition(
            db=db,
            agent_id=agent_id,
            name=name,
            agent_type=agent_type_enum,
            description=description,
            config=config,
            is_active=is_active,
        )
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")
        return agent_definition_to_dict(agent)


@mcp.tool()
def get_self_actor() -> dict:
    """The Actor this caller is, decided server-side.

    Under a credential that is the credential's Actor; over stdio, or on an
    instance that has not turned enforcement on, the operator's human Actor.
    Never a value the call supplied - ask this when you want to know who the
    server thinks you are.
    """
    with _session() as db:
        actor_id = authz.acting_actor_id(db)
        actor = crud.get_actor(db, actor_id) if actor_id else None
        if not actor:
            raise ToolError("No actor is bound to this caller; seed the operator actor (migration 003)")
        return actor_to_dict(actor)


# ========== Coordination Tools (Phase 3) ==========
#
# These let several agents - across machines, repos and clones of one repo -
# see each other, avoid editing the same files at once, and leave each other
# durable messages. Everything here is pull-based: nothing interrupts a peer.


@mcp.tool()
def register_session(
    actor_name: str,
    host: str,
    repo: str,
    clone_path: str,
    branch: str | None = None,
    focus: str | None = None,
    actor_type: str = "ai",
    git_dir: str | None = None,
) -> dict:
    """Join the coordination board. Call this once at the start of a work session.

    Returns a session_id that every other coordination tool needs, so record it
    for the rest of the session. Safe to call again: re-registering the same
    actor in the same clone resumes the existing session (keeping its claims and
    unread relays) rather than creating a duplicate.

    `repo` must be the canonical remote identity (e.g. "AxonRelay/core") so that
    sibling clones of the same repository recognise each other; `clone_path` is
    the absolute path of *this* checkout, which is what distinguishes them.

    Pass `git_dir` (`git rev-parse --git-common-dir`, absolute) so stash
    collisions can be detected: sibling git worktrees have different clone_paths
    but share one stash stack, and only the git dir identifies that.
    """
    _free_text_surface()
    # A credential decides which Actor this is; a differing actor_name is a
    # request to act as somebody else and is refused, not quietly ignored.
    authz.check_claimed_actor(actor_name)
    resolved_type = models.ActorTypeEnum(actor_type)
    with _session() as db:
        session = coordination.register_session(
            db,
            actor_name=actor_name,
            actor_type=resolved_type,
            host=host,
            repo=repo,
            clone_path=clone_path,
            branch=branch,
            focus=focus,
            git_dir=git_dir,
        )
        return session_to_dict(session)


@mcp.tool()
def heartbeat_session(session_id: int, focus: str | None = None, branch: str | None = None) -> dict:
    """Report that you are still working, and update what you are working on.

    `focus` is the one line other agents see on the board - keep it current
    ("refactoring app/crud.py", "waiting on review of #44"). A session that goes
    quiet for 30 minutes is shown as stale to everyone else.
    """
    _free_text_surface()
    with _session() as db:
        session = coordination.heartbeat_session(db, session_id, focus=focus, branch=branch)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        return session_to_dict(session)


@mcp.tool()
def end_session(session_id: int) -> dict:
    """Leave the board and release every territory claim this session holds.

    Call this when you finish, so peers are not waiting on leases you no longer
    need. Claims expire on their own if you never do.
    """
    with _session() as db:
        result = coordination.end_session(db, session_id)
        if not result:
            raise ValueError(f"Session {session_id} not found")
        session, released_claim_ids = result
        return {**session_to_dict(session), "released_claim_ids": released_claim_ids}


@mcp.tool()
def get_board(repo: str | None = None) -> dict:
    """One read that answers "what is going on right now" across the fleet.

    Active sessions (who, which machine, which clone, which branch, what focus),
    live territory claims, and unacknowledged relays. Start a turn with this to
    orient yourself before deciding what to touch.
    """
    with _session() as db:
        return coordination.board(db, repo=repo)


@mcp.tool()
def check_conflicts(repo: str, paths: list[str], session_id: int | None = None, mode: str = "exclusive") -> dict:
    """Ask whether anyone else holds a claim overlapping `paths` - without claiming.

    Use this before planning an edit; use claim_territory when you commit to it.
    Path patterns are repo-relative, with fnmatch wildcards allowed; a pattern
    without a wildcard covers everything beneath it ("backend/app" covers
    "backend/app/crud.py").
    """
    _free_text_surface()
    resolved_mode = models.ClaimModeEnum(mode)
    with _session() as db:
        conflicts = coordination.find_conflicts(
            db,
            repo=repo,
            paths=[territory.normalize_path(p) for p in paths],
            mode=resolved_mode,
            exclude_session_id=session_id,
        )
        return {"repo": repo, "paths": paths, "clear": not conflicts, "conflicts": conflicts}


@mcp.tool()
def claim_territory(
    session_id: int,
    paths: list[str],
    reason: str | None = None,
    mode: str = "exclusive",
    ttl_minutes: int = 60,
    repo: str | None = None,
    force: bool = False,
) -> dict:
    """Take an advisory lease on the paths you are about to edit.

    **Refused if it overlaps someone else's live claim** - the response then
    carries `granted: false` and the conflicting claims, including who holds
    them and where, so you can message them (send_relay) or work elsewhere.
    Pass force=true to claim anyway; the override is recorded on the claim.

    mode="exclusive" (default) collides with any overlapping claim;
    mode="shared" only collides with exclusive ones, so several readers coexist.
    The lease expires after ttl_minutes (default 60) so a crashed agent cannot
    hold territory forever. Release it with release_territory when you are done.
    """
    _free_text_surface()
    resolved_mode = models.ClaimModeEnum(mode)
    with _session() as db:
        result = coordination.claim_territory(
            db,
            session_id=session_id,
            paths=paths,
            repo=repo,
            mode=resolved_mode,
            reason=reason,
            ttl_minutes=ttl_minutes,
            force=force,
        )
        return {
            "granted": result["granted"],
            "claim": claim_to_dict(result["claim"]) if result["claim"] else None,
            "conflicts": result["conflicts"],
        }


@mcp.tool()
def release_territory(claim_id: int | None = None, session_id: int | None = None) -> dict:
    """Give back a claim (by claim_id) or all of a session's claims (by session_id)."""
    if claim_id is None and session_id is None:
        raise ValueError("Pass either claim_id or session_id")
    with _session() as db:
        if claim_id is not None:
            claim = coordination.release_claim(db, claim_id)
            if not claim:
                raise ValueError(f"Claim {claim_id} not found")
            return {"released": 1, "claim": claim_to_dict(claim)}
        return {"released": coordination.release_session_claims(db, session_id)}


@mcp.tool()
def send_relay(
    from_session_id: int,
    subject: str,
    body: str | None = None,
    kind: str = "note",
    to_actor_id: int | None = None,
    to_workspace_id: int | None = None,
    to_repo: str | None = None,
    in_reply_to_id: int | None = None,
) -> dict:
    """Leave a durable message for other sessions. They read it on their own turn.

    Addressing, narrowest to widest: to_actor_id (that agent, wherever it runs),
    to_workspace_id (that clone), to_repo (everyone on that repository), or none
    of them (broadcast). The message waits for recipients that are offline, so
    this works across machines and across restarts.

    kind is one of note / question / answer / handoff / warning. Use "warning"
    when you are about to do something others should know about ("rewriting the
    migration chain"), and "handoff" when you are passing work on.
    """
    _free_text_surface()
    resolved_kind = models.RelayKindEnum(kind)
    with _session() as db:
        relay = coordination.send_relay(
            db,
            subject=subject,
            body=body,
            from_session_id=from_session_id,
            to_actor_id=to_actor_id,
            to_workspace_id=to_workspace_id,
            to_repo=to_repo,
            kind=resolved_kind,
            in_reply_to_id=in_reply_to_id,
        )
        return relay_to_dict(relay)


@mcp.tool()
def read_inbox(session_id: int, include_acked: bool = False, limit: int = 50) -> list[dict]:
    """Read relays addressed to this session, oldest first. Marks them as read.

    Check this at the start of a turn, alongside get_board. Messages stay in the
    inbox until you ack_relay them, so nothing is lost if you do not act now.
    """
    with _session() as db:
        return coordination.read_inbox(db, session_id, include_acked=include_acked, limit=limit)


@mcp.tool()
def ack_relay(relay_id: int, session_id: int, note: str | None = None) -> dict:
    """Acknowledge a relay so it leaves your inbox, optionally with a reply note.

    Acking is per recipient: it does not hide a broadcast from anyone else, and
    the receipt records that you saw it.
    """
    _free_text_surface()
    with _session() as db:
        receipt = coordination.ack_relay(db, relay_id=relay_id, session_id=session_id, note=note)
        return {
            "relay_id": receipt.relay_id,
            "session_id": receipt.session_id,
            "acked_at": receipt.acked_at.isoformat() if receipt.acked_at else None,
            "ack_note": receipt.ack_note,
        }


# ========== Resources (read-only context for the LLM) ==========


@mcp.resource("axonrelay://tasks/{task_id}")
def task_resource(task_id: int) -> str:
    """A task as a Markdown-formatted resource."""
    with _session() as db:
        task = crud.get_task(db, int(task_id))
        if not task:
            raise ValueError(f"Task {task_id} not found")
        drafts = crud.get_drafts(db, task.id)
        approvals = crud.get_approvals(db, task.id)

        lines = [
            f"# Task #{task.id}: {task.title}",
            "",
            f"- Status: `{task.status}`",
            f"- Thread: `{task.thread_id}`",
            f"- Created: {task.created_at.isoformat()}",
        ]
        if task.description:
            lines += ["", "## Description", task.description]
        if task.current_draft:
            lines += ["", "## Current draft", task.current_draft]
        if task.feedback:
            lines += ["", "## Latest reviewer feedback", task.feedback]
        if drafts:
            lines += ["", "## Draft history"]
            for d in drafts:
                lines += [f"### v{d.version} — {d.created_at.isoformat()}", d.content, ""]
        if approvals:
            lines += ["", "## Approval history"]
            for a in approvals:
                lines += [
                    f"- {a.created_at.isoformat()} | **{a.action}** by actor #{a.reviewer_actor_id}: "
                    f"{a.comment or '(no comment)'}"
                ]
        return "\n".join(lines)


@mcp.resource("axonrelay://tasks/{task_id}/drafts/{version}")
def draft_resource(task_id: int, version: int) -> str:
    """A specific draft version (raw content)."""
    with _session() as db:
        drafts = crud.get_drafts(db, int(task_id))
        for d in drafts:
            if d.version == int(version):
                return d.content
        raise ValueError(f"Task {task_id} has no draft v{version}")


@mcp.tool()
def claim_git_resource(
    session_id: int,
    resource: str,
    reason: str | None = None,
    ttl_minutes: int = 60,
    force: bool = False,
) -> dict:
    """Claim a shared git resource that no path pattern can describe.

    A checkout has exactly one working tree, index, HEAD and stash stack, and
    `git stash pop` names no path — so a path claim cannot protect them. Take
    one of these before a destructive git operation:

      "worktree" — `reset --hard`, `clean -fd`, checking out over a dirty tree.
                   Contends only with sessions in the *same* clone.
      "stash"    — any `git stash` operation. **Contends across sibling git
                   worktrees of the same clone**, because `refs/stash` is a
                   per-repository ref: a second worktree does not give you a
                   second stash stack.
      "refs"     — deleting or moving local branches and tags. Also per-clone.
      "remote"   — force-push, `+refspec`, `push --delete`. **Contends across
                   every clone of the repo on every host**: they all push to
                   the same remote refs.

    Always exclusive, refused on conflict unless force=true, and expiring like a
    path claim.
    """
    _free_text_surface()
    resolved = models.ClaimResourceEnum(resource)
    with _session() as db:
        result = coordination.claim_resource(
            db, session_id=session_id, resource=resolved, reason=reason, ttl_minutes=ttl_minutes, force=force
        )
        return {
            "granted": result["granted"],
            "claim": claim_to_dict(result["claim"]) if result["claim"] else None,
            "conflicts": result["conflicts"],
        }


@mcp.tool()
def check_git_resource(session_id: int, resource: str) -> dict:
    """Ask whether a git resource is free for this session, without claiming it.

    This is what `tools/gitsafe` consults before letting a destructive git
    command through. Read-only.
    """
    resolved = models.ClaimResourceEnum(resource)
    with _session() as db:
        return coordination.guard_git_operation(db, session_id=session_id, resource=resolved)


@mcp.resource("axonrelay://board")
def board_resource() -> str:
    """The live coordination board: active sessions, claims, and open relays.

    Exposed as a resource as well as a tool so a client can pin it as ambient
    context and keep the fleet's state in view without spending a tool call.
    """
    with _session() as db:
        return json.dumps(coordination.board(db), indent=2, ensure_ascii=False)


# ========== Entry point ==========


# ========== Safe Envelope (content-blind ingestion) ==========


@mcp.tool()
def ingest_safe_envelope(envelope: dict[str, Any]) -> dict:
    """Ingest one metadata-only Safe Envelope (schema: docs/schemas/safe-envelope-v1.json).

    The envelope carries opaque identifiers, an action and outcome from closed
    lists, a source-produced artifact commitment and timestamps — never a
    title, body, path or URL; unknown fields are rejected. Rejections name the
    offending field names only. Re-sending the same event_id is idempotent.
    Same service as POST /envelopes.
    """
    with _session() as db:
        try:
            event, created = safe_envelope.ingest(db, envelope)
        except safe_envelope.EnvelopeRejected as e:
            raise ToolError(str(e)) from None
        payload = safe_envelope.event_to_dict(event)
        payload["created"] = created
        return payload


@mcp.tool()
def list_safe_events(limit: int = 100, action: str | None = None) -> list[dict]:
    """Stored Safe Envelopes, newest first (optionally one action)."""
    with _session() as db:
        try:
            events = safe_envelope.list_events(db, limit=limit, action=action)
        except safe_envelope.EnvelopeRejected as e:
            raise ToolError(str(e)) from None
        return [safe_envelope.event_to_dict(e) for e in events]


def main() -> None:
    """Run the server over stdio (default) or Streamable HTTP (``--http``)."""
    parser = argparse.ArgumentParser(prog="app.mcp.server", description="AxonRelay MCP server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve Streamable HTTP instead of stdio, so remote clients can share this instance.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address for --http (default: loopback only).")
    parser.add_argument("--port", type=int, default=8765, help="Port for --http (default: 8765).")
    parser.add_argument("--path", default="/mcp", help="URL path for --http (default: /mcp).")
    args = parser.parse_args()

    if not args.http:
        mcp.run()
        return

    # Bound to loopback by default. Reaching it from another device should go
    # through Tailscale rather than a public bind; setting AXONRELAY_MCP_TOKEN
    # additionally requires a bearer token on every request. Neither replaces
    # the other. See deploy/DEPLOYMENT.md and app/mcp/http_auth.py.
    import uvicorn

    app = http_auth.wrap_if_configured(mcp.streamable_http_app(streamable_http_path=args.path, host=args.host))
    if os.environ.get(http_auth.TOKEN_ENV, "").strip():
        print(f"bearer token required ({http_auth.TOKEN_ENV} is set)", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
