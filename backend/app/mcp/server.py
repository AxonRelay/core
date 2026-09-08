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
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import (
    AcceptedElicitation,
    CancelledElicitation,
    Context,
    Elicit,
    ElicitationResult,
    MCPServer,
    Resolve,
)
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.request_state import RequestStateSecurity
from pydantic import BaseModel, Field

from app import authz, coordination, crud, langgraph_client, models, safe_envelope, service, territory
from app.database import SessionLocal
from app.mcp import compat, http_auth
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

REQUEST_STATE_KEY_ENV = "AXONRELAY_REQUEST_STATE_KEY"

# How long a half-finished approval may sit before the operator has to start
# over. It is a human reading a draft, not a machine round trip, so minutes
# rather than seconds; long enough to survive a reconnect, short enough that a
# leaked handle is not a standing offer.
REQUEST_STATE_TTL_SECONDS = 900.0


def _request_state_principal(ctx) -> str | None:
    """Who a resumable handle belongs to, for the SDK's seal.

    The SDK's default binding reads *its* OAuth context, which AxonRelay never
    populates - it resolves callers itself (app/authz.py, ADR-011) - so left
    alone every handle is unbound and one credential could finish a round that
    was started, and had its draft displayed, under another.

    The credential is identified by a digest of the presented Authorization
    header: stable across the rounds of one call, different for a different
    caller, and never the plaintext. It is a binding value, not a lookup key -
    it is never compared against a stored credential digest.

    `None` on stdio and on an HTTP call that presents nothing, which is the
    same trust assumption the rest of the server makes about those callers: one
    operator, one process. The SDK rejects a handle whose binding appears or
    disappears between rounds, so the two cases cannot be mixed.
    """
    request = getattr(ctx, "request", None)
    header = request.headers.get("authorization") if request is not None else None
    if not header:
        return None
    return hashlib.sha256(header.encode()).hexdigest()


def _request_state_security() -> RequestStateSecurity:
    """Key the sealed `requestState` handle that carries a half-finished approval.

    From 2026-07-28 an unanswered question comes back to the client as an
    opaque handle and returns on the next `tools/call` (app/mcp/compat.py).
    The SDK treats an inbound handle as attacker-controlled and only accepts
    one it minted, so the key decides *which* processes can resume a decision.

    Set AXONRELAY_REQUEST_STATE_KEY to share that across restarts and across
    workers behind one Streamable HTTP endpoint. Left unset, the key is
    process-local and a restart mid-approval makes the client ask again -
    the safe failure, and the right default for the stdio server, which is one
    process that dies with its client anyway.
    """
    key = os.environ.get(REQUEST_STATE_KEY_ENV, "").strip()
    if not key:
        # `RequestStateSecurity.ephemeral()` would be the shorthand, but it
        # takes no `bind_principal` and would silently install the SDK's OAuth
        # default - the very binding that is inert here. Same process-local
        # key, stated explicitly.
        return RequestStateSecurity(
            keys=[os.urandom(32)], ttl=REQUEST_STATE_TTL_SECONDS, bind_principal=_request_state_principal
        )
    try:
        return RequestStateSecurity(keys=[key], ttl=REQUEST_STATE_TTL_SECONDS, bind_principal=_request_state_principal)
    except ValueError as e:
        # Refused at startup and by name. A weak key here does not fail
        # visibly later: it seals handles that carry a pending approval, and
        # the whole point of sealing them is that a caller cannot mint one.
        raise ValueError(f"{REQUEST_STATE_KEY_ENV} is not usable: {e}") from None


mcp = MCPServer("axonrelay", request_state_security=_request_state_security())


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
            try:
                return await call_next(ctx)
            except authz.AuthzError as e:
                # A refusal raised *inside* a tool (an actor claim, a session
                # that belongs to somebody else) is still an authorization
                # answer: it reaches the client as a clean message rather than
                # as an unexpected error with a traceback in the log.
                raise ToolError(str(e)) from None

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


def _resolve_enum(enum_cls, value: str | None, label: str):
    """A structured code, or None. An unknown value is refused by name, never guessed."""
    if not value:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(m.value for m in enum_cls)
        raise ToolError(f"Invalid {label}. One of: {allowed}") from None


def _resolve_focus_code(value: str | None) -> models.FocusCodeEnum | None:
    return _resolve_enum(models.FocusCodeEnum, value, "focus code")


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
    decision_key: str | None = None,
) -> dict:
    """Record an approve/reject in the ledger and resume the Platform thread.

    Shared by the approve_task / reject_task tools and the interactive
    review_pending_task (elicitation) tool so they cannot drift.

    The ledger entry is written first and binds to the exact draft decided on
    (a modified draft becomes a new version before the entry is recorded). If
    `artifact_version` / `expected_commitment` no longer match the task's
    latest draft, nothing is recorded and the call returns
    status="stale_decision".

    A `decision_key` makes the write idempotent. The lookup happens inside
    `record_approval`, under the task row lock, so exactly one of two racing
    rounds is told it wrote the entry; the other is refused with
    `DuplicateDecisionError` and returns without resuming the graph. Deciding
    that here, before the lock, would let both believe they wrote it and apply
    one decision to the graph twice.
    """
    with _session() as db:
        task = crud.get_task(db, task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        # Authorization first, before this call can learn anything or change
        # anything.
        authz.check_may_approve(db, task_id)
        # The recorded reviewer is the authenticated caller, never a parameter.
        reviewer_actor_id = authz.acting_actor_id(db)

        if task.status != models.TaskStatusEnum.WAITING_APPROVAL:
            raise ValueError(f"Task {task_id} is not waiting for approval (status={task.status})")

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
                decision_key=_scope_to_reviewer(decision_key, reviewer_actor_id),
            )
        except crud.DuplicateDecisionError as e:
            # Another round recorded exactly this decision and owns the resume
            # that follows it. Answer with what stands; do not drive the graph
            # a second time.
            db.rollback()
            payload = task_to_dict(task)
            payload["approval"] = approval_to_dict(e.approval)
            payload["replayed"] = True
            return payload
        except crud.StaleArtifactError as e:
            db.rollback()
            return {"status": "stale_decision", "task_id": task_id, "reason": str(e)}
        except crud.ArtifactRequiredError as e:
            db.rollback()
            return {"status": "no_artifact", "task_id": task_id, "reason": str(e)}
        except crud.LedgerError as e:
            db.rollback()
            raise ToolError(str(e)) from None
        except authz.AuthzError as e:
            db.rollback()
            raise ToolError(str(e)) from None

        resume_payload = {"decision": action, "human_comment": comment}
        if action == "approved":
            resume_payload["modified_draft"] = modified_draft
        result = await langgraph_client.resume_thread(task.thread_id, resume_payload)
        # Delivered. Recorded rather than inferred from the task's status,
        # which cannot tell "never delivered" from "delivered, and the graph
        # interrupted again" (app/mcp/server.py, `_shown_artifact`).
        approval.resumed_at = datetime.utcnow()
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


class _ShownArtifact(BaseModel):
    """The exact draft `review_pending_task` puts in front of the reviewer.

    Resolved once per round and shared by the question and the tool body, so
    "what was displayed" and "what the decision binds to" are one object rather
    than two reads that a concurrent writer can separate.

    `state` says whether there is anything to ask about:

    * `ready` - `version` / `commitment` / `content` name the pinned draft;
    * `no_artifact` - the task is waiting, but has no draft to decide on;
    * `not_waiting` - the task is not (or is no longer) WAITING_APPROVAL;
    * `recorded_unresumed` - this caller's decision is already in the ledger
      but the task is still parked, so the graph never received it;
      `approval_id` names the entry to re-drive.

    `not_waiting` is a state and not an exception because it is the ordinary
    outcome of a race, and it can happen on *any* round: from 2026-07-28 this
    resolver re-runs when the reviewer answers, so a task that moved on while
    they were reading would otherwise turn the answer into a crash.
    """

    task_id: int
    state: Literal["ready", "no_artifact", "not_waiting", "recorded_unresumed"] = "ready"
    reason: str = ""
    approval_id: int | None = None
    title: str = ""
    feedback: str | None = None
    version: int | None = None
    commitment: str | None = None
    content: str | None = None


def _shown_artifact(task_id: int) -> _ShownArtifact:
    """Pin the draft under review before anything is shown or asked.

    A resolver rather than the first lines of the tool body: from 2026-07-28
    the framework needs the question *before* it runs the body, and the
    question must be rendered from the same object the decision later binds
    to. The Safe Envelope guard runs here for the same reason - it has to
    refuse before a draft reaches the wire, and on that path the wire is the
    elicitation, not the tool result.
    """
    _free_text_surface()
    with _session() as db:
        task = crud.get_task(db, task_id)
        if not task:
            # A task id that names nothing is the caller's mistake in every
            # round, so it stays an error rather than becoming a status.
            raise ValueError(f"Task {task_id} not found")
        # Refuse before the draft is rendered, not after the answer comes back.
        # A caller who may not decide on this task has no business being shown
        # its draft as a question - and finding out at record time means the
        # content already crossed the wire. (`get_drafts` still serves the same
        # content to a `ledger:read` credential; this closes the narrower gap
        # where `ledger:write` alone put it in front of a non-approver, and it
        # fails fast for everyone else.)
        authz.check_may_approve(db, task_id)
        if task.status != models.TaskStatusEnum.WAITING_APPROVAL:
            return _ShownArtifact(
                task_id=task_id,
                state="not_waiting",
                reason=f"Task {task_id} is not waiting for approval (status={task.status}).",
            )
        # The latest *artifact* — the draft row the decision will bind to — not
        # the task's current_draft mirror, which PUT /tasks can rewrite without
        # creating a version. What is shown and what is recorded must be the
        # same object.
        drafts = crud.get_drafts(db, task_id)
        # Before showing anything: has this caller already decided, without the
        # graph hearing about it? `record_approval` commits, then the Platform
        # call can fail — the ledger entry stands while the task stays parked.
        # Asking again here would be wrong twice over: the reviewer would be
        # re-shown a draft they have already ruled on (their own edit, if they
        # made one, since that edit is now the latest version), and answering
        # would append a second entry for one decision. Repair instead.
        #
        # `resumed_at` is the whole discriminator, and it has to be recorded
        # rather than inferred: a rejection that WAS delivered and made the
        # graph interrupt again on the same draft leaves the task in a state
        # identical to a lost resume, and replaying the old decision there
        # would rob the reviewer of the new question.
        newest = crud.latest_approval(db, task_id) if drafts else None
        if newest is not None and newest.resumed_at is None and newest.reviewer_actor_id == authz.acting_actor_id(db):
            return _ShownArtifact(
                task_id=task_id,
                state="recorded_unresumed",
                approval_id=newest.id,
                reason="A decision by this reviewer is recorded but was never delivered to the graph.",
            )
        if not drafts:
            return _ShownArtifact(
                task_id=task_id,
                state="no_artifact",
                reason="Task has no draft to decide on; nothing was shown and nothing is recorded.",
                title=task.title,
                feedback=task.feedback,
            )
        shown = drafts[-1]
        return _ShownArtifact(
            task_id=task_id,
            title=task.title,
            feedback=task.feedback,
            version=shown.version,
            commitment=shown.commitment,
            content=shown.content,
        )


def _ask_approval(
    shown: Annotated[_ShownArtifact, Resolve(_shown_artifact)],
    ctx: Context[None, None],
) -> Elicit[_ApprovalDecision] | ElicitationResult[_ApprovalDecision]:
    """Ask the reviewer, or hand back "not asked" without touching the wire.

    Returning `Elicit` lets the SDK choose the interaction model the negotiated
    revision requires — a standalone `elicitation/create` up to 2025-11-25, an
    `InputRequiredResult` plus `requestState` from 2026-07-28 — so one code
    path serves both (app/mcp/compat.py).

    The two silent arms are the cases where asking would be wrong rather than
    unanswered: nothing to show, and a client that never declared it can answer
    a form. Both return a cancel the body reinterprets, having re-derived the
    reason itself; that is cheaper than a protocol error the client has to
    translate back into "use approve_task instead".
    """
    if shown.state != "ready":
        return CancelledElicitation()  # nothing to decide, or nothing to ask about
    if not compat.client_can_elicit(ctx.client_capabilities):
        return CancelledElicitation()
    message = (
        f"Task #{shown.task_id}: {shown.title}\n\n"
        f"--- Draft (v{shown.version}) ---\n{shown.content}\n\n"
        f"--- Reviewer feedback ---\n{shown.feedback or '(none)'}\n\n"
        "Approve this draft?"
    )
    return Elicit(message, _ApprovalDecision)


def _decision_key(shown: _ShownArtifact, decision: _ApprovalDecision) -> str:
    """A stable name for *this* decision on *this* draft.

    A 2026-07-28 answer round replays the whole `tools/call`, so the body can
    run more than once for one human decision — a client retry after a dropped
    response is the ordinary case, not the exotic one. The key is derived from
    what was decided and what it was decided on, which makes every replay of a
    round produce the same name and the ledger reject the second append
    (app/crud.py, migration 013).

    It deliberately carries no nonce and no request id: a nonce would make a
    replay look new, which is the whole failure being prevented.

    This half names the *decision*; `_scope_to_reviewer` adds the *decider*,
    because a task can have more than one authorised approver and two people
    reaching the same verdict on the same draft are two ledger entries, not one.
    The residual case this cannot separate is the same reviewer recording a
    byte-identical decision twice on the same draft version — which needs the
    task to return to WAITING_APPROVAL without a new draft, and which nothing
    in the payload distinguishes from a retry.
    """
    payload = json.dumps(
        {
            "task_id": shown.task_id,
            "version": shown.version,
            "commitment": shown.commitment,
            "approve": decision.approve,
            "comment": decision.comment,
            # Only when approving: `_apply_decision` discards an edit on a
            # rejection, so counting it here would give two identical
            # rejections two different names and let both be recorded.
            "modified_draft": decision.modified_draft if decision.approve else "",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _scope_to_reviewer(decision_key: str | None, reviewer_actor_id: int | None) -> str | None:
    """Bind a decision key to the actor it will be recorded against.

    Without this, "same draft, same verdict, same words" from a *different*
    authorised approver collides with the first reviewer's entry: the second
    decision is never recorded and its caller is handed the first reviewer's
    approval. In a ledger whose subject is who approved what, that is the worst
    possible way to be idempotent.
    """
    if decision_key is None:
        return None
    return hashlib.sha256(f"{decision_key}:{reviewer_actor_id}".encode()).hexdigest()


async def _resume_recorded_decision(task_id: int, approval_id: int) -> dict:
    """Hand a decision that is already in the ledger to the graph, again.

    The recovery half of "a ledger row is not evidence the graph received the
    decision". Everything the resume needs is in the recorded entry, so no new
    entry is written and the reviewer is not asked anything: the decision was
    made, it just did not arrive.

    The claim is taken first, as a conditional UPDATE. Two retries arriving
    together would otherwise both find `resumed_at IS NULL` and both drive the
    graph - the same defect the row lock prevents on the write side, one step
    later. A claim whose Platform call then fails is given back, so a lost
    resume stays recoverable rather than becoming permanent.
    """
    with _session() as db:
        task = crud.get_task(db, task_id)
        approval = db.query(models.Approval).filter(models.Approval.id == approval_id).one_or_none()
        if task is None or approval is None:  # pragma: no cover - torn between rounds
            raise ToolError(f"Task {task_id} changed while its recorded decision was being resumed")
        if not crud.claim_resume(db, approval_id):
            # Somebody else is delivering it. Answer with what stands.
            db.refresh(task)
            payload = task_to_dict(task)
            payload["approval"] = approval_to_dict(approval)
            payload["replayed"] = True
            return payload

        resume_payload: dict[str, Any] = {"decision": approval.action, "human_comment": approval.comment}
        if approval.action == "approved":
            # Faithful to the original call: the producer of the bound draft
            # cannot answer this, because a reviewer who happened to author
            # the latest draft earlier looks exactly like one who edited it as
            # part of this approval.
            bound = next((d for d in crud.get_drafts(db, task_id) if d.version == approval.artifact_version), None)
            resume_payload["modified_draft"] = bound.content if (approval.edited_artifact and bound) else None
        try:
            result = await langgraph_client.resume_thread(task.thread_id, resume_payload)
        except Exception:
            crud.release_resume(db, approval_id)
            raise
        _sync_state(db, task, result)
        db.refresh(task)
        payload = task_to_dict(task)
        payload["approval"] = approval_to_dict(approval)
        payload["replayed"] = True
        return payload


@mcp.tool()
async def review_pending_task(
    task_id: int,
    shown: Annotated[_ShownArtifact, Resolve(_shown_artifact)],
    decision: Annotated[ElicitationResult[_ApprovalDecision], Resolve(_ask_approval)],
    ctx: Context[None, None],
) -> dict:
    """Interactively review a WAITING_APPROVAL task.

    Shows the current draft and reviewer feedback, asks for your decision
    through the MCP client's native elicitation prompt, records it in the
    tamper-evident ledger, and resumes the graph.

    The question travels in whichever shape the negotiated protocol revision
    uses: held open on the connection up to 2025-11-25, or returned as an
    input-required round with a resumable handle from 2026-07-28, where a
    dropped connection costs a retry rather than the decision. Read
    `axonrelay://compat` for the matrix. A client that cannot answer a form
    gets status="elicitation_unsupported" and the names of the direct tools.

    The decision is bound to the draft version and commitment that were shown.
    If the draft changes while you are reading it, the answer is discarded and
    the question is asked again against the new draft, so you never approve
    unseen content; if the task leaves WAITING_APPROVAL, the call returns
    status="stale_decision" and records nothing. A task with no draft returns
    status="no_artifact" before asking anything. Replaying an answered round
    returns the decision it already recorded, marked `replayed: true`, instead
    of appending a second one.
    """
    if shown.state == "recorded_unresumed":
        return await _resume_recorded_decision(task_id, shown.approval_id)
    if shown.state == "not_waiting":
        return {
            "status": "stale_decision",
            "task_id": task_id,
            # Careful in both directions. "This call recorded nothing" is
            # always true; "nothing was recorded" is not, because replaying an
            # answered round lands here once that decision moved the task on -
            # and "your decision stands" is not either, because the task may
            # have been carried off by somebody else before this one arrived.
            # Name what is certain and say where to look for the rest.
            "reason": (
                f"{shown.reason} This call recorded nothing and showed nothing. "
                "Read get_task or verify_task_ledger for the decisions that stand on it."
            ),
        }
    if shown.state == "no_artifact":
        return {"status": "no_artifact", "task_id": task_id, "reason": shown.reason}
    if not compat.client_can_elicit(ctx.client_capabilities):
        return {
            "status": "elicitation_unsupported",
            "task_id": task_id,
            "reason": (
                "This client did not declare form elicitation, so nothing was shown and nothing is recorded. "
                f"Read the draft with get_drafts and decide with {' / '.join(compat.FALLBACK_TOOLS)}, "
                "passing artifact_version and expected_commitment to bind the decision to what you read."
            ),
            "fallback_tools": list(compat.FALLBACK_TOOLS),
            "artifact_version": shown.version,
            "expected_commitment": shown.commitment,
        }
    if not isinstance(decision, AcceptedElicitation):
        return {"status": "no_decision", "elicitation_action": decision.action, "task_id": task_id}

    answer = decision.data
    # Staleness, part 1: the task may have left WAITING_APPROVAL while we
    # awaited the human. Part 2 — "is the latest draft still the one shown?" —
    # is enforced inside record_approval under the task row lock, by passing
    # the shown version and commitment as the decision's target. A draft that
    # changed between rounds never reaches here at all: the question renders
    # from `shown`, so the framework sees a different question and re-asks it.
    with _session() as db:
        current = crud.get_task(db, task_id)
        if not current or current.status != models.TaskStatusEnum.WAITING_APPROVAL:
            return {
                "status": "stale_decision",
                "task_id": task_id,
                "reason": "Task changed since it was shown; no decision recorded. Re-run review_pending_task.",
            }

    return await _apply_decision(
        task_id,
        action="approved" if answer.approve else "rejected",
        comment=answer.comment or None,
        modified_draft=(answer.modified_draft or None) if answer.approve else None,
        artifact_version=shown.version,
        expected_commitment=shown.commitment,
        decision_key=_decision_key(shown, answer),
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
    focus_code: str | None = None,
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
            focus_code=_resolve_focus_code(focus_code),
            git_dir=git_dir,
        )
        return session_to_dict(session)


@mcp.tool()
def heartbeat_session(
    session_id: int, focus: str | None = None, branch: str | None = None, focus_code: str | None = None
) -> dict:
    """Report that you are still working, and update what you are working on.

    `focus` is the one line other agents see on the board - keep it current
    ("refactoring app/crud.py", "waiting on review of #44"). A session that goes
    quiet for 30 minutes is shown as stale to everyone else.
    """
    _free_text_surface()
    with _session() as _db:
        authz.check_session_owner(_db, session_id)
    with _session() as db:
        session = coordination.heartbeat_session(
            db, session_id, focus=focus, focus_code=_resolve_focus_code(focus_code), branch=branch
        )
        if not session:
            raise ValueError(f"Session {session_id} not found")
        return session_to_dict(session)


@mcp.tool()
def end_session(session_id: int) -> dict:
    """Leave the board and release every territory claim this session holds.

    Call this when you finish, so peers are not waiting on leases you no longer
    need. Claims expire on their own if you never do.
    """
    with _session() as _db:
        authz.check_session_owner(_db, session_id)
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
    with _session() as _db:
        authz.check_session_owner(_db, session_id)
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
    reason_code: str | None = None,
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
    with _session() as _db:
        authz.check_session_owner(_db, session_id)
    resolved_mode = models.ClaimModeEnum(mode)
    with _session() as db:
        result = coordination.claim_territory(
            db,
            session_id=session_id,
            paths=paths,
            repo=repo,
            mode=resolved_mode,
            reason=reason,
            reason_code=_resolve_enum(models.ClaimReasonCodeEnum, reason_code, "reason code"),
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
        authz.check_session_owner(db, session_id)
        if claim_id is not None:
            claim = coordination.release_claim(db, claim_id)
            if claim is not None:
                # A claim id names a session too; releasing another Actor's
                # territory is the same forgery as ending its session.
                authz.check_session_owner(db, claim.session_id)
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
    code: str | None = None,
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
    with _session() as _db:
        authz.check_session_owner(_db, from_session_id)
    resolved_kind = models.RelayKindEnum(kind)
    with _session() as db:
        relay = coordination.send_relay(
            db,
            subject=subject,
            body=body,
            code=_resolve_enum(models.RelayCodeEnum, code, "relay code"),
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
    with _session() as _db:
        authz.check_session_owner(_db, session_id)
    with _session() as db:
        return coordination.read_inbox(db, session_id, include_acked=include_acked, limit=limit)


@mcp.tool()
def ack_relay(relay_id: int, session_id: int, note: str | None = None, ack_code: str | None = None) -> dict:
    """Acknowledge a relay so it leaves your inbox, optionally with a reply note.

    Acking is per recipient: it does not hide a broadcast from anyone else, and
    the receipt records that you saw it.
    """
    _free_text_surface()
    with _session() as _db:
        authz.check_session_owner(_db, session_id)
    with _session() as db:
        receipt = coordination.ack_relay(
            db,
            relay_id=relay_id,
            session_id=session_id,
            note=note,
            ack_code=_resolve_enum(models.AckCodeEnum, ack_code, "ack code"),
        )
        return {
            "relay_id": receipt.relay_id,
            "session_id": receipt.session_id,
            "acked_at": receipt.acked_at.isoformat() if receipt.acked_at else None,
            "ack_note": receipt.ack_note,
            "ack_code": str(receipt.ack_code) if receipt.ack_code else None,
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
    reason_code: str | None = None,
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
    with _session() as _db:
        authz.check_session_owner(_db, session_id)
    resolved = models.ClaimResourceEnum(resource)
    with _session() as db:
        result = coordination.claim_resource(
            db,
            session_id=session_id,
            resource=resolved,
            reason=reason,
            reason_code=_resolve_enum(models.ClaimReasonCodeEnum, reason_code, "reason code"),
            ttl_minutes=ttl_minutes,
            force=force,
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


@mcp.resource("axonrelay://compat")
def compat_resource() -> str:
    """The MCP protocol revisions, SDK line and clients this server is tested against.

    Served as a resource so a client can read the claim instead of inferring
    it: which revisions negotiate, where interactive approval switches from a
    held connection to a resumable input-required round, and which tools to
    fall back to when it cannot answer a question at all.
    """
    return json.dumps(compat.matrix(), indent=2, sort_keys=True)


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
