"""CRUD operations for database models."""

from sqlalchemy.orm import Session

from app import models

# ========== User Operations ==========


def get_user_by_email(db: Session, email: str):
    """Get user by email address."""
    return db.query(models.User).filter(models.User.email == email).first()


def get_user_by_oauth(db: Session, oauth_provider: str, oauth_id: str):
    """Get user by OAuth provider and ID."""
    return (
        db.query(models.User)
        .filter(models.User.oauth_provider == oauth_provider, models.User.oauth_id == oauth_id)
        .first()
    )


def get_user(db: Session, user_id: int):
    """Get user by ID."""
    return db.query(models.User).filter(models.User.id == user_id).first()


def create_user(db: Session, email: str, name: str | None, oauth_provider: str, oauth_id: str):
    """Create a new user with associated Actor."""
    # Create Actor first
    display_name = name if name else email.split("@")[0]
    db_actor = models.Actor(type=models.ActorTypeEnum.HUMAN, name=display_name)
    db.add(db_actor)
    db.flush()  # Get actor ID

    # Create User linked to Actor
    db_user = models.User(
        actor_id=db_actor.id, email=email, name=name, oauth_provider=oauth_provider, oauth_id=oauth_id
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def get_or_create_user(db: Session, email: str, name: str | None, oauth_provider: str, oauth_id: str):
    """Get existing user or create new one."""
    user = get_user_by_oauth(db, oauth_provider, oauth_id)
    if user:
        # Update name if changed
        if name and user.name != name:
            user.name = name
            # Also update Actor name
            if user.actor:
                user.actor.name = name
            db.commit()
            db.refresh(user)
        return user
    return create_user(db, email, name, oauth_provider, oauth_id)


# ========== Project Operations ==========


def get_project(db: Session, project_id: int):
    """Get project by ID."""
    return db.query(models.Project).filter(models.Project.id == project_id).first()


def get_user_projects(db: Session, user_id: int, skip: int = 0, limit: int = 100):
    """Get all projects where user is a member."""
    return (
        db.query(models.Project)
        .join(models.ProjectMember)
        .filter(models.ProjectMember.user_id == user_id)
        .offset(skip)
        .limit(limit)
        .all()
    )


def create_project(db: Session, name: str, description: str | None, owner_id: int):
    """Create a new project with owner as OWNER role member."""
    db_project = models.Project(name=name, description=description)
    db.add(db_project)
    db.flush()  # Get project ID before adding member

    # Add owner as OWNER role member
    db_member = models.ProjectMember(user_id=owner_id, project_id=db_project.id, role=models.RoleEnum.OWNER)
    db.add(db_member)
    db.commit()
    db.refresh(db_project)
    return db_project


def update_project(db: Session, project_id: int, name: str | None = None, description: str | None = None):
    """Update project details."""
    project = get_project(db, project_id)
    if not project:
        return None

    if name is not None:
        project.name = name
    if description is not None:
        project.description = description

    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project_id: int):
    """Delete a project (cascade deletes members, tasks, etc.)."""
    project = get_project(db, project_id)
    if not project:
        return False

    db.delete(project)
    db.commit()
    return True


# ========== ProjectMember Operations ==========


def get_project_member(db: Session, project_id: int, user_id: int):
    """Get project member by project ID and user ID."""
    return (
        db.query(models.ProjectMember)
        .filter(models.ProjectMember.project_id == project_id, models.ProjectMember.user_id == user_id)
        .first()
    )


def get_project_members(db: Session, project_id: int):
    """Get all members of a project."""
    return db.query(models.ProjectMember).filter(models.ProjectMember.project_id == project_id).all()


def add_project_member(db: Session, project_id: int, user_id: int, role: models.RoleEnum):
    """Add a user to a project with specified role."""
    db_member = models.ProjectMember(user_id=user_id, project_id=project_id, role=role)
    db.add(db_member)
    db.commit()
    db.refresh(db_member)
    return db_member


def update_project_member_role(db: Session, project_id: int, user_id: int, role: models.RoleEnum):
    """Update a project member's role."""
    member = get_project_member(db, project_id, user_id)
    if not member:
        return None

    member.role = role
    db.commit()
    db.refresh(member)
    return member


def remove_project_member(db: Session, project_id: int, user_id: int):
    """Remove a user from a project."""
    member = get_project_member(db, project_id, user_id)
    if not member:
        return False

    db.delete(member)
    db.commit()
    return True


def check_project_permission(db: Session, project_id: int, user_id: int, required_roles: list[models.RoleEnum]):
    """Check if user has one of the required roles in project."""
    member = get_project_member(db, project_id, user_id)
    if not member:
        return False
    return member.role in required_roles


# ========== Actor Operations (ADR-005) ==========


def get_actor(db: Session, actor_id: int):
    """Get actor by ID."""
    return db.query(models.Actor).filter(models.Actor.id == actor_id).first()


def get_actors(db: Session, actor_type: models.ActorTypeEnum | None = None, skip: int = 0, limit: int = 100):
    """Get all actors, optionally filtered by type."""
    query = db.query(models.Actor)
    if actor_type:
        query = query.filter(models.Actor.type == actor_type)
    return query.offset(skip).limit(limit).all()


# ========== AgentDefinition Operations ==========


def get_agent_definition(db: Session, agent_id: int):
    """Get agent definition by ID."""
    return db.query(models.AgentDefinition).filter(models.AgentDefinition.id == agent_id).first()


def get_agent_definition_by_actor(db: Session, actor_id: int):
    """Get agent definition by actor ID."""
    return db.query(models.AgentDefinition).filter(models.AgentDefinition.actor_id == actor_id).first()


def get_agent_definitions(
    db: Session,
    agent_type: models.AgentTypeEnum | None = None,
    is_active: bool | None = None,
    skip: int = 0,
    limit: int = 100,
):
    """Get all agent definitions with optional filters."""
    query = db.query(models.AgentDefinition)
    if agent_type:
        query = query.filter(models.AgentDefinition.agent_type == agent_type)
    if is_active is not None:
        query = query.filter(models.AgentDefinition.is_active == is_active)
    return query.offset(skip).limit(limit).all()


def create_agent_definition(
    db: Session, name: str, agent_type: models.AgentTypeEnum, description: str | None = None, config: dict | None = None
):
    """Create a new AI agent definition with associated Actor."""
    # Create Actor first
    db_actor = models.Actor(type=models.ActorTypeEnum.AI, name=name)
    db.add(db_actor)
    db.flush()  # Get actor ID

    # Create AgentDefinition linked to Actor
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
    """Update an AI agent definition."""
    agent = get_agent_definition(db, agent_id)
    if not agent:
        return None

    if name is not None:
        agent.actor.name = name  # Update Actor name
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
    """Delete an AI agent definition (also deletes associated Actor)."""
    agent = get_agent_definition(db, agent_id)
    if not agent:
        return False

    # Delete Actor (will cascade to AgentDefinition)
    db.delete(agent.actor)
    db.commit()
    return True


# ========== TaskAssignment Operations ==========


def get_task_assignment(db: Session, assignment_id: int):
    """Get task assignment by ID."""
    return db.query(models.TaskAssignment).filter(models.TaskAssignment.id == assignment_id).first()


def get_task_assignments(db: Session, task_id: int):
    """Get all assignments for a task."""
    return db.query(models.TaskAssignment).filter(models.TaskAssignment.task_id == task_id).all()


def get_actor_assignments(db: Session, actor_id: int, skip: int = 0, limit: int = 100):
    """Get all task assignments for an actor."""
    return (
        db.query(models.TaskAssignment)
        .filter(models.TaskAssignment.actor_id == actor_id)
        .offset(skip)
        .limit(limit)
        .all()
    )


def create_task_assignment(db: Session, task_id: int, actor_id: int, role: models.AssignmentRoleEnum):
    """Create a new task assignment."""
    db_assignment = models.TaskAssignment(task_id=task_id, actor_id=actor_id, role=role)
    db.add(db_assignment)
    db.commit()
    db.refresh(db_assignment)
    return db_assignment


def delete_task_assignment(db: Session, assignment_id: int):
    """Delete a task assignment."""
    assignment = get_task_assignment(db, assignment_id)
    if not assignment:
        return False

    db.delete(assignment)
    db.commit()
    return True


def delete_task_assignment_by_actor(db: Session, task_id: int, actor_id: int):
    """Delete a task assignment by task and actor ID."""
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
    """Get task by ID."""
    return db.query(models.Task).filter(models.Task.id == task_id).first()


def get_task_by_thread_id(db: Session, thread_id: str):
    """Get task by LangGraph thread ID."""
    return db.query(models.Task).filter(models.Task.thread_id == thread_id).first()


def get_project_tasks(
    db: Session, project_id: int, status: models.TaskStatusEnum | None = None, skip: int = 0, limit: int = 100
):
    """Get all tasks in a project with optional status filter."""
    query = db.query(models.Task).filter(models.Task.project_id == project_id)
    if status:
        query = query.filter(models.Task.status == status)
    return query.order_by(models.Task.created_at.desc()).offset(skip).limit(limit).all()


def get_user_created_tasks(
    db: Session, user_id: int, status: models.TaskStatusEnum | None = None, skip: int = 0, limit: int = 100
):
    """Get all tasks created by a user."""
    query = db.query(models.Task).filter(models.Task.creator_id == user_id)
    if status:
        query = query.filter(models.Task.status == status)
    return query.order_by(models.Task.created_at.desc()).offset(skip).limit(limit).all()


def get_user_assigned_tasks(
    db: Session,
    actor_id: int,
    role: models.AssignmentRoleEnum | None = None,
    status: models.TaskStatusEnum | None = None,
    skip: int = 0,
    limit: int = 100,
):
    """Get all tasks assigned to an actor with optional role and status filters."""
    query = db.query(models.Task).join(models.TaskAssignment).filter(models.TaskAssignment.actor_id == actor_id)
    if role:
        query = query.filter(models.TaskAssignment.role == role)
    if status:
        query = query.filter(models.Task.status == status)
    return query.order_by(models.Task.created_at.desc()).offset(skip).limit(limit).all()


def get_tasks_by_assignment_role_across_projects(
    db: Session,
    user_id: int,
    assignment_role: models.AssignmentRoleEnum,
    status: models.TaskStatusEnum | None = None,
    skip: int = 0,
    limit: int = 100,
):
    """
    Get tasks across all projects where the user's actor has a specific assignment role.
    Useful for role-based filtering (e.g., "all tasks where I'm a reviewer").
    """
    # Get user's actor_id
    user = get_user(db, user_id)
    if not user or not user.actor_id:
        return []

    query = (
        db.query(models.Task)
        .join(models.TaskAssignment)
        .filter(models.TaskAssignment.actor_id == user.actor_id, models.TaskAssignment.role == assignment_role)
    )
    if status:
        query = query.filter(models.Task.status == status)
    return query.order_by(models.Task.created_at.desc()).offset(skip).limit(limit).all()


def create_task(
    db: Session,
    project_id: int,
    thread_id: str,
    title: str,
    creator_id: int | None = None,
    description: str | None = None,
):
    """Create a new task."""
    db_task = models.Task(
        project_id=project_id,
        thread_id=thread_id,
        title=title,
        creator_id=creator_id,
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
    """Update a task."""
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
    """Update only the task status."""
    task = get_task(db, task_id)
    if not task:
        return None

    task.status = status
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, task_id: int):
    """Delete a task."""
    task = get_task(db, task_id)
    if not task:
        return False

    db.delete(task)
    db.commit()
    return True
