"""AxonRelay MCP server.

Exposes AxonRelay as an MCP server so the operator can drive it from any
MCP-aware client (Claude Code is the first-class target). Tools mirror the
HTTP API but operate at the service / SQLAlchemy layer directly to avoid an
internal HTTP hop.

Run with:
    python -m app.mcp.server         # stdio transport (default for IDE clients)

Phase 2.4 will additionally mount a Streamable HTTP transport behind the
Cloudflare Tunnel so remote MCP clients can connect.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import BaseModel, Field

from app import crud, langgraph_client, models, service
from app.database import SessionLocal
from app.mcp.serializers import (
    actor_to_dict,
    agent_definition_to_dict,
    approval_to_dict,
    draft_to_dict,
    task_to_dict,
)

mcp = FastMCP("axonrelay")


@contextmanager
def _session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
        self_actor = crud.get_self_actor(db)
        if not self_actor:
            return []
        tasks = crud.list_pending_approvals(db, actor_id=self_actor.id)
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
    "legacy": int}. valid=False means a recorded approval was altered or reordered
    after the fact; "legacy" counts pre-hash-chain rows that are not covered.
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
    with _session() as db:
        self_actor = crud.get_self_actor(db)
        creator_actor_id = self_actor.id if self_actor else None

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
) -> dict:
    """Record an approve/reject in the ledger and resume the Platform thread.

    Shared by the approve_task / reject_task tools and the interactive
    review_pending_task (elicitation) tool so they cannot drift.
    """
    with _session() as db:
        task = crud.get_task(db, task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task.status != models.TaskStatusEnum.WAITING_APPROVAL:
            raise ValueError(f"Task {task_id} is not waiting for approval (status={task.status})")

        self_actor = crud.get_self_actor(db)
        reviewer_actor_id = self_actor.id if self_actor else None

        crud.record_approval(
            db,
            task_id=task_id,
            reviewer_actor_id=reviewer_actor_id,
            action=action,
            comment=comment,
        )
        resume_payload = {"decision": action, "human_comment": comment}
        if action == "approved":
            resume_payload["modified_draft"] = modified_draft
        result = await langgraph_client.resume_thread(task.thread_id, resume_payload)
        _sync_state(db, task, result)
        db.refresh(task)
        return task_to_dict(task)


@mcp.tool()
async def approve_task(
    task_id: int,
    comment: str | None = None,
    modified_draft: str | None = None,
) -> dict:
    """Approve a task that's WAITING_APPROVAL. Optionally include comment / edit."""
    return await _apply_decision(task_id, action="approved", comment=comment, modified_draft=modified_draft)


@mcp.tool()
async def reject_task(
    task_id: int,
    comment: str | None = None,
    reason: str | None = None,
) -> dict:
    """Reject a task; revision loop continues unless the iteration cap is hit."""
    combined = " | ".join(p for p in [comment, reason] if p) or None
    return await _apply_decision(task_id, action="rejected", comment=combined)


class _ApprovalDecision(BaseModel):
    """Elicitation schema for an interactive approval decision."""

    approve: bool = Field(description="Approve the draft? false = reject / request revision.")
    comment: str = Field(default="", description="Optional note recorded in the approval ledger.")
    modified_draft: str = Field(
        default="",
        description="Optional edited draft text (used only when approving); leave blank to keep the draft as-is.",
    )


@mcp.tool()
async def review_pending_task(task_id: int, ctx: Context[ServerSession, None]) -> dict:
    """Interactively review a WAITING_APPROVAL task.

    Shows the current draft and reviewer feedback, then asks for your decision
    through the MCP client's native elicitation prompt, records it in the
    tamper-evident ledger, and resumes the graph. Clients that do not support
    elicitation should use approve_task / reject_task directly instead.
    """
    with _session() as db:
        task = crud.get_task(db, task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task.status != models.TaskStatusEnum.WAITING_APPROVAL:
            raise ValueError(f"Task {task_id} is not waiting for approval (status={task.status})")
        title, draft, feedback = task.title, task.current_draft, task.feedback

    # Elicit outside the DB session — don't pin a session across user interaction.
    message = (
        f"Task #{task_id}: {title}\n\n"
        f"--- Draft ---\n{draft or '(no draft)'}\n\n"
        f"--- Reviewer feedback ---\n{feedback or '(none)'}\n\n"
        "Approve this draft?"
    )
    result = await ctx.elicit(message=message, schema=_ApprovalDecision)
    if result.action != "accept" or not result.data:
        return {"status": "no_decision", "elicitation_action": result.action, "task_id": task_id}

    decision = result.data
    if decision.approve:
        return await _apply_decision(
            task_id,
            action="approved",
            comment=decision.comment or None,
            modified_draft=decision.modified_draft or None,
        )
    return await _apply_decision(task_id, action="rejected", comment=decision.comment or None)


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
    """Return the operator's human Actor (singleton in personal PoC)."""
    with _session() as db:
        actor = crud.get_self_actor(db)
        if not actor:
            raise ValueError("Self actor is not seeded; run migration 003")
        return actor_to_dict(actor)


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


# ========== Entry point ==========


def main() -> None:
    """stdio entry point — used by Claude Code etc."""
    mcp.run()


if __name__ == "__main__":
    main()
