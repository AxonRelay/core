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
