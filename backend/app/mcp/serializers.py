"""Plain-dict serializers for MCP tool / resource outputs.

MCP returns JSON-friendly payloads, not Pydantic / SQLAlchemy objects. These
helpers project ORM rows into plain dicts the LLM can read directly.

Everything about the coordination plane, and every Actor, goes through
`app.disclosure`, which decides what a response may contain in the current
mode (issue #26). These functions stay as the one place a row becomes a dict;
they just no longer decide *which* fields that dict has.
"""

from app import disclosure, models


def actor_to_dict(actor: models.Actor | None) -> dict | None:
    """An Actor's name is arbitrary text, so safe mode gets its opaque ref instead."""
    return disclosure.actor_view(actor)


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
        "commitment": draft.commitment,
        "commitment_algorithm": draft.commitment_algorithm,
        "producer_actor_id": draft.producer_actor_id,
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
        "hash_version": approval.hash_version,
        # Which draft this entry decided on; False / None on pre-009 entries.
        "artifact_bound": approval.artifact_bound,
        "artifact_ref": approval.artifact_ref,
        "artifact_version": approval.artifact_version,
        "artifact_commitment": approval.artifact_commitment,
        "artifact_commitment_algorithm": approval.artifact_commitment_algorithm,
        "producer_actor_id": approval.producer_actor_id,
    }


def agent_definition_to_dict(agent: models.AgentDefinition) -> dict:
    return {
        "id": agent.id,
        "actor_id": agent.actor_id,
        "agent_type": str(agent.agent_type),
        "description": agent.description,
        # Never the config's values: it is where an operator puts credentials
        # (app/disclosure.py). The key names describe its shape.
        "config_keys": disclosure.config_keys(agent.config),
        "is_active": agent.is_active,
        "actor": actor_to_dict(agent.actor),
    }


# ========== Coordination layer ==========


def workspace_to_dict(workspace: models.Workspace | None) -> dict | None:
    return disclosure.workspace_view(workspace)


def session_to_dict(session: models.Session) -> dict:
    return disclosure.session_view(session)


def claim_to_dict(claim: models.Claim) -> dict:
    return disclosure.claim_view(claim)


def relay_to_dict(relay: models.Relay) -> dict:
    return disclosure.relay_view(relay)
