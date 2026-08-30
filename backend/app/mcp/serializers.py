"""Plain-dict serializers for MCP tool / resource outputs.

MCP returns JSON-friendly payloads, not Pydantic / SQLAlchemy objects. These
helpers project ORM rows into plain dicts the LLM can read directly.
"""

from app import models


def actor_to_dict(actor: models.Actor | None) -> dict | None:
    if not actor:
        return None
    return {
        "id": actor.id,
        "type": str(actor.type),
        "name": actor.name,
    }


def assignment_to_dict(assignment: models.TaskAssignment) -> dict:
    return {
        "id": assignment.id,
        "task_id": assignment.task_id,
        "actor_id": assignment.actor_id,
        "role": str(assignment.role),
        "actor": actor_to_dict(assignment.actor),
    }


def task_to_dict(task: models.Task) -> dict:
    return {
        "id": task.id,
        "thread_id": task.thread_id,
        "title": task.title,
        "description": task.description,
        "status": str(task.status),
        "current_draft": task.current_draft,
        "feedback": task.feedback,
        "creator_actor_id": task.creator_actor_id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "assignments": [assignment_to_dict(a) for a in (task.assignments or [])],
    }


def draft_to_dict(draft: models.Draft) -> dict:
    return {
        "id": draft.id,
        "task_id": draft.task_id,
        "version": draft.version,
        "content": draft.content,
        "created_at": draft.created_at.isoformat(),
    }


def approval_to_dict(approval: models.Approval) -> dict:
    return {
        "id": approval.id,
        "task_id": approval.task_id,
        "reviewer_actor_id": approval.reviewer_actor_id,
        "action": approval.action,
        "comment": approval.comment,
        "created_at": approval.created_at.isoformat(),
        "prev_hash": approval.prev_hash,
        "entry_hash": approval.entry_hash,
    }


def agent_definition_to_dict(agent: models.AgentDefinition) -> dict:
    return {
        "id": agent.id,
        "actor_id": agent.actor_id,
        "agent_type": str(agent.agent_type),
        "description": agent.description,
        "config": agent.config,
        "is_active": agent.is_active,
        "actor": actor_to_dict(agent.actor),
    }


# ========== Coordination layer ==========


def workspace_to_dict(workspace: models.Workspace | None) -> dict | None:
    if not workspace:
        return None
    return {
        "id": workspace.id,
        "host": workspace.host,
        "repo": workspace.repo,
        "clone_path": workspace.clone_path,
        "label": workspace.label,
    }


def session_to_dict(session: models.Session) -> dict:
    return {
        "session_id": session.id,
        "actor_id": session.actor_id,
        "actor": actor_to_dict(session.actor),
        "workspace": workspace_to_dict(session.workspace),
        "branch": session.branch,
        "focus": session.focus,
        "status": str(session.status),
        "started_at": session.started_at.isoformat(),
        "last_heartbeat_at": session.last_heartbeat_at.isoformat(),
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
    }


def claim_to_dict(claim: models.Claim) -> dict:
    return {
        "claim_id": claim.id,
        "session_id": claim.session_id,
        "repo": claim.repo,
        "paths": list(claim.paths or []),
        "mode": str(claim.mode),
        "reason": claim.reason,
        "status": str(claim.status),
        "forced_over": claim.forced_over,
        "created_at": claim.created_at.isoformat(),
        "expires_at": claim.expires_at.isoformat(),
        "released_at": claim.released_at.isoformat() if claim.released_at else None,
    }


def relay_to_dict(relay: models.Relay) -> dict:
    return {
        "relay_id": relay.id,
        "kind": str(relay.kind),
        "subject": relay.subject,
        "body": relay.body,
        "from_session_id": relay.from_session_id,
        "from_actor_id": relay.from_actor_id,
        "to_actor_id": relay.to_actor_id,
        "to_workspace_id": relay.to_workspace_id,
        "to_repo": relay.to_repo,
        "in_reply_to_id": relay.in_reply_to_id,
        "created_at": relay.created_at.isoformat(),
    }
