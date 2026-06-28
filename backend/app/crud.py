"""CRUD operations for database models (personal PoC, post-migration 003)."""

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import ledger, models

# ========== Actor Operations ==========


def get_actor(db: Session, actor_id: int):
    return db.query(models.Actor).filter(models.Actor.id == actor_id).first()


def get_actors(db: Session, actor_type: models.ActorTypeEnum | None = None, skip: int = 0, limit: int = 100):
    query = db.query(models.Actor)
    if actor_type:
        query = query.filter(models.Actor.type == actor_type)
    return query.offset(skip).limit(limit).all()


def get_self_actor(db: Session):
    """Get the single human actor representing the operator."""
    return db.query(models.Actor).filter(models.Actor.type == models.ActorTypeEnum.HUMAN).first()


# ========== AgentDefinition Operations ==========


def get_agent_definition(db: Session, agent_id: int):
    return db.query(models.AgentDefinition).filter(models.AgentDefinition.id == agent_id).first()


def get_agent_definition_by_actor(db: Session, actor_id: int):
    return db.query(models.AgentDefinition).filter(models.AgentDefinition.actor_id == actor_id).first()


def get_agent_definitions(
    db: Session,
    agent_type: models.AgentTypeEnum | None = None,
    is_active: bool | None = None,
    skip: int = 0,
    limit: int = 100,
):
    query = db.query(models.AgentDefinition)
    if agent_type:
        query = query.filter(models.AgentDefinition.agent_type == agent_type)
    if is_active is not None:
        query = query.filter(models.AgentDefinition.is_active == is_active)
    return query.offset(skip).limit(limit).all()


def create_agent_definition(
    db: Session,
    name: str,
    agent_type: models.AgentTypeEnum,
    description: str | None = None,
    config: dict | None = None,
):
    db_actor = models.Actor(type=models.ActorTypeEnum.AI, name=name)
    db.add(db_actor)
    db.flush()

    db_agent = models.AgentDefinition(
        actor_id=db_actor.id, agent_type=agent_type, description=description, config=config
    )
    db.add(db_agent)
    db.commit()
    db.refresh(db_agent)
    return db_agent


def update_agent_definition(
    db: Session,
    agent_id: int,
    name: str | None = None,
    agent_type: models.AgentTypeEnum | None = None,
    description: str | None = None,
    config: dict | None = None,
    is_active: bool | None = None,
):
    agent = get_agent_definition(db, agent_id)
    if not agent:
        return None

    if name is not None:
        agent.actor.name = name
    if agent_type is not None:
        agent.agent_type = agent_type
    if description is not None:
        agent.description = description
    if config is not None:
        agent.config = config
    if is_active is not None:
        agent.is_active = is_active

    db.commit()
    db.refresh(agent)
    return agent


def delete_agent_definition(db: Session, agent_id: int):
    agent = get_agent_definition(db, agent_id)
    if not agent:
        return False
    db.delete(agent.actor)
    db.commit()
    return True


# ========== TaskAssignment Operations ==========


def get_task_assignment(db: Session, assignment_id: int):
    return db.query(models.TaskAssignment).filter(models.TaskAssignment.id == assignment_id).first()


def get_task_assignments(db: Session, task_id: int):
    return db.query(models.TaskAssignment).filter(models.TaskAssignment.task_id == task_id).all()


def get_actor_assignments(db: Session, actor_id: int, skip: int = 0, limit: int = 100):
    return (
        db.query(models.TaskAssignment)
        .filter(models.TaskAssignment.actor_id == actor_id)
        .offset(skip)
        .limit(limit)
        .all()
    )


def create_task_assignment(db: Session, task_id: int, actor_id: int, role: models.AssignmentRoleEnum):
    """Assign an actor to a task with a role. Idempotent on (task_id, actor_id,
    role): a repeat call returns the existing row instead of violating the
    unique constraint."""
    existing = (
        db.query(models.TaskAssignment)
        .filter(
            models.TaskAssignment.task_id == task_id,
            models.TaskAssignment.actor_id == actor_id,
            models.TaskAssignment.role == role,
        )
        .first()
    )
    if existing:
        return existing
    db_assignment = models.TaskAssignment(task_id=task_id, actor_id=actor_id, role=role)
    db.add(db_assignment)
    try:
        db.commit()
    except IntegrityError:
        # Only treat this as idempotent if a matching row now exists (a concurrent
        # insert won the unique-constraint race). Any other integrity error (e.g.
        # a bad task_id / actor_id FK) is re-raised rather than silently swallowed.
        db.rollback()
        winner = (
            db.query(models.TaskAssignment)
            .filter(
                models.TaskAssignment.task_id == task_id,
                models.TaskAssignment.actor_id == actor_id,
                models.TaskAssignment.role == role,
            )
            .first()
        )
        if winner is None:
            raise
        return winner
    db.refresh(db_assignment)
    return db_assignment


def delete_task_assignment(db: Session, assignment_id: int):
    assignment = get_task_assignment(db, assignment_id)
    if not assignment:
        return False
    db.delete(assignment)
    db.commit()
    return True


def delete_task_assignment_by_actor(db: Session, task_id: int, actor_id: int):
    assignment = (
        db.query(models.TaskAssignment)
        .filter(models.TaskAssignment.task_id == task_id, models.TaskAssignment.actor_id == actor_id)
        .first()
    )
    if not assignment:
        return False
    db.delete(assignment)
    db.commit()
    return True


# ========== Task Operations ==========


def get_task(db: Session, task_id: int):
    return db.query(models.Task).filter(models.Task.id == task_id).first()


def get_task_by_thread_id(db: Session, thread_id: str):
    return db.query(models.Task).filter(models.Task.thread_id == thread_id).first()


def list_tasks(
    db: Session,
    status: models.TaskStatusEnum | None = None,
    skip: int = 0,
    limit: int = 100,
):
    """List all tasks (single-user PoC)."""
    query = db.query(models.Task)
    if status:
        query = query.filter(models.Task.status == status)
    return query.order_by(models.Task.created_at.desc()).offset(skip).limit(limit).all()


def get_actor_tasks_by_role(
    db: Session,
    actor_id: int,
    role: models.AssignmentRoleEnum,
    status: models.TaskStatusEnum | None = None,
    skip: int = 0,
    limit: int = 100,
):
    """Get tasks where the given actor has the specified assignment role."""
    query = (
        db.query(models.Task)
        .join(models.TaskAssignment)
        .filter(models.TaskAssignment.actor_id == actor_id, models.TaskAssignment.role == role)
        # distinct(): defence in depth against a task appearing twice via the
        # join (the (task_id, actor_id, role) unique constraint should already
        # prevent duplicate assignments).
        .distinct()
    )
    if status:
        query = query.filter(models.Task.status == status)
    return query.order_by(models.Task.created_at.desc()).offset(skip).limit(limit).all()


def list_pending_approvals(db: Session, actor_id: int, skip: int = 0, limit: int = 100):
    """List tasks the given actor must approve."""
    return get_actor_tasks_by_role(
        db,
        actor_id=actor_id,
        role=models.AssignmentRoleEnum.APPROVER,
        status=models.TaskStatusEnum.WAITING_APPROVAL,
        skip=skip,
        limit=limit,
    )


def create_task(
    db: Session,
    thread_id: str,
    title: str,
    creator_actor_id: int | None = None,
    description: str | None = None,
):
    db_task = models.Task(
        thread_id=thread_id,
        title=title,
        creator_actor_id=creator_actor_id,
        description=description,
        status=models.TaskStatusEnum.DRAFT,
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


def update_task(
    db: Session,
    task_id: int,
    title: str | None = None,
    description: str | None = None,
    status: models.TaskStatusEnum | None = None,
    current_draft: str | None = None,
    feedback: str | None = None,
):
    task = get_task(db, task_id)
    if not task:
        return None

    if title is not None:
        task.title = title
    if description is not None:
        task.description = description
    if status is not None:
        task.status = status
    if current_draft is not None:
        task.current_draft = current_draft
    if feedback is not None:
        task.feedback = feedback

    db.commit()
    db.refresh(task)
    return task


def update_task_status(db: Session, task_id: int, status: models.TaskStatusEnum):
    task = get_task(db, task_id)
    if not task:
        return None
    task.status = status
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, task_id: int):
    task = get_task(db, task_id)
    if not task:
        return False
    db.delete(task)
    db.commit()
    return True


# ========== Draft Operations ==========


def add_draft(db: Session, task_id: int, content: str):
    """Append a new draft version to a task."""
    last_version = (
        db.query(models.Draft.version)
        .filter(models.Draft.task_id == task_id)
        .order_by(models.Draft.version.desc())
        .first()
    )
    next_version = (last_version[0] + 1) if last_version else 1
    db_draft = models.Draft(task_id=task_id, version=next_version, content=content)
    db.add(db_draft)
    db.commit()
    db.refresh(db_draft)
    return db_draft


def get_drafts(db: Session, task_id: int):
    return db.query(models.Draft).filter(models.Draft.task_id == task_id).order_by(models.Draft.version.asc()).all()


# ========== Approval Operations ==========


def record_approval(
    db: Session,
    task_id: int,
    reviewer_actor_id: int | None,
    action: str,
    comment: str | None = None,
):
    """Record an approval / rejection event, chained to the task's prior entry.

    created_at is set explicitly here (not via the column default) so the value
    that is hashed is exactly the value persisted.
    """
    now = datetime.utcnow()
    last = (
        db.query(models.Approval).filter(models.Approval.task_id == task_id).order_by(models.Approval.id.desc()).first()
    )
    prev_hash = last.entry_hash if last else None
    entry_hash = ledger.compute_entry_hash(
        prev_hash,
        task_id=task_id,
        reviewer_actor_id=reviewer_actor_id,
        action=action,
        comment=comment,
        created_at=now,
    )
    db_approval = models.Approval(
        task_id=task_id,
        reviewer_actor_id=reviewer_actor_id,
        action=action,
        comment=comment,
        created_at=now,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
    )
    db.add(db_approval)
    db.commit()
    db.refresh(db_approval)
    return db_approval


def get_approvals(db: Session, task_id: int):
    return db.query(models.Approval).filter(models.Approval.task_id == task_id).order_by(models.Approval.id.asc()).all()


def verify_approval_chain(db: Session, task_id: int) -> dict:
    """Recompute the approval hash chain for a task and report tampering.

    Returns {"valid": bool, "broken_at": <approval id or None>, "count": int,
    "legacy": int}. ``legacy`` counts pre-migration-004 rows (entry_hash IS NULL)
    that predate the hash chain; these are not covered by tamper-evidence and are
    skipped (the chain restarts after them, matching record_approval). A mismatch
    among hashed rows means a recorded approval was altered or reordered.
    """
    approvals = get_approvals(db, task_id)
    prev_hash = None
    legacy = 0
    for approval in approvals:
        if approval.entry_hash is None:
            # Legacy row (recorded before the hash chain existed); not verifiable.
            legacy += 1
            prev_hash = None
            continue
        expected = ledger.compute_entry_hash(
            prev_hash,
            task_id=approval.task_id,
            reviewer_actor_id=approval.reviewer_actor_id,
            action=approval.action,
            comment=approval.comment,
            created_at=approval.created_at,
        )
        if approval.prev_hash != prev_hash or approval.entry_hash != expected:
            return {"valid": False, "broken_at": approval.id, "count": len(approvals), "legacy": legacy}
        prev_hash = approval.entry_hash
    return {"valid": True, "broken_at": None, "count": len(approvals), "legacy": legacy}
