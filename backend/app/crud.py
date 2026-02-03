"""CRUD operations for database models."""
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app import models


# ========== User Operations ==========

def get_user_by_email(db: Session, email: str):
    """Get user by email address."""
    return db.query(models.User).filter(models.User.email == email).first()


def get_user_by_oauth(db: Session, oauth_provider: str, oauth_id: str):
    """Get user by OAuth provider and ID."""
    return db.query(models.User).filter(
        models.User.oauth_provider == oauth_provider,
        models.User.oauth_id == oauth_id
    ).first()


def get_user(db: Session, user_id: int):
    """Get user by ID."""
    return db.query(models.User).filter(models.User.id == user_id).first()


def create_user(db: Session, email: str, name: str | None, oauth_provider: str, oauth_id: str):
    """Create a new user."""
    db_user = models.User(
        email=email,
        name=name,
        oauth_provider=oauth_provider,
        oauth_id=oauth_id
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
    return db.query(models.Project).join(models.ProjectMember).filter(
        models.ProjectMember.user_id == user_id
    ).offset(skip).limit(limit).all()


def create_project(db: Session, name: str, description: str | None, owner_id: int):
    """Create a new project with owner as OWNER role member."""
    db_project = models.Project(name=name, description=description)
    db.add(db_project)
    db.flush()  # Get project ID before adding member

    # Add owner as OWNER role member
    db_member = models.ProjectMember(
        user_id=owner_id,
        project_id=db_project.id,
        role=models.RoleEnum.OWNER
    )
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
    return db.query(models.ProjectMember).filter(
        models.ProjectMember.project_id == project_id,
        models.ProjectMember.user_id == user_id
    ).first()


def get_project_members(db: Session, project_id: int):
    """Get all members of a project."""
    return db.query(models.ProjectMember).filter(
        models.ProjectMember.project_id == project_id
    ).all()


def add_project_member(db: Session, project_id: int, user_id: int, role: models.RoleEnum):
    """Add a user to a project with specified role."""
    db_member = models.ProjectMember(
        user_id=user_id,
        project_id=project_id,
        role=role
    )
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
