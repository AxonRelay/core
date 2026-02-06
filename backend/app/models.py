"""Database models for AxonRelay."""

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base

# =============================================================================
# Enums
# =============================================================================


class ActorTypeEnum(enum.StrEnum):
    """Actor types - human or AI."""

    HUMAN = "human"
    AI = "ai"


class AgentTypeEnum(enum.StrEnum):
    """AI Agent types."""

    WRITER = "writer"
    REVIEWER = "reviewer"
    VALIDATOR = "validator"
    RESEARCHER = "researcher"
    ASSISTANT = "assistant"
    CUSTOM = "custom"


class AssignmentRoleEnum(enum.StrEnum):
    """Task assignment roles."""

    EXECUTOR = "executor"  # Executes the task (AI or human)
    REVIEWER = "reviewer"  # Reviews the output
    APPROVER = "approver"  # Approves/rejects the task
    OBSERVER = "observer"  # Receives notifications only


class RoleEnum(enum.StrEnum):
    """Project member roles."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class TaskStatusEnum(enum.StrEnum):
    """Task status."""

    DRAFT = "draft"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# =============================================================================
# Actor Unified Model (ADR-005)
# =============================================================================


class Actor(Base):
    """
    Unified abstraction for humans and AI agents.
    Serves as a lightweight reference point for assignments.
    """

    __tablename__ = "actors"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(Enum(ActorTypeEnum), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships (1:1 with User or AgentDefinition)
    user = relationship("User", back_populates="actor", uselist=False)
    agent_definition = relationship("AgentDefinition", back_populates="actor", uselist=False)
    task_assignments = relationship("TaskAssignment", back_populates="actor", cascade="all, delete-orphan")


class AgentDefinition(Base):
    """
    AI Agent definition with configuration.
    Linked to Actor via 1:1 relationship.
    """

    __tablename__ = "agent_definitions"

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, ForeignKey("actors.id", ondelete="CASCADE"), unique=True, nullable=False)
    agent_type = Column(Enum(AgentTypeEnum), nullable=False, index=True)
    description = Column(Text)
    config = Column(JSON)  # Agent-specific configuration (model, prompts, etc.)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    actor = relationship("Actor", back_populates="agent_definition")


class TaskAssignment(Base):
    """
    Assignment of actors (human or AI) to tasks with specific roles.
    """

    __tablename__ = "task_assignments"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("actors.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(Enum(AssignmentRoleEnum), nullable=False, index=True)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    task = relationship("Task", back_populates="assignments")
    actor = relationship("Actor", back_populates="task_assignments")


# =============================================================================
# Core Models
# =============================================================================


class User(Base):
    """User account (authenticated via OAuth). Linked to Actor via 1:1 relationship."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, ForeignKey("actors.id", ondelete="SET NULL"), unique=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(255))
    oauth_provider = Column(String(50))  # e.g., "google"
    oauth_id = Column(String(255))  # provider-specific user ID
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    actor = relationship("Actor", back_populates="user")
    project_memberships = relationship("ProjectMember", back_populates="user", cascade="all, delete-orphan")
    created_tasks = relationship("Task", back_populates="creator", foreign_keys="Task.creator_id")


class Project(Base):
    """Project workspace for organizing tasks."""

    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    """Many-to-many relationship between Users and Projects with roles."""

    __tablename__ = "project_members"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    role = Column(Enum(RoleEnum), nullable=False, default=RoleEnum.MEMBER)
    joined_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="project_memberships")
    project = relationship("Project", back_populates="members")


class Task(Base):
    """Task managed by AI agents with human-in-the-loop approval."""

    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String(100), unique=True, index=True, nullable=False)  # LangGraph thread ID
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))

    title = Column(String(500), nullable=False)
    description = Column(Text)
    status = Column(Enum(TaskStatusEnum), nullable=False, default=TaskStatusEnum.DRAFT, index=True)

    # Current draft and feedback
    current_draft = Column(Text)
    feedback = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    project = relationship("Project", back_populates="tasks")
    creator = relationship("User", back_populates="created_tasks", foreign_keys=[creator_id])
    assignments = relationship("TaskAssignment", back_populates="task", cascade="all, delete-orphan")
    drafts = relationship("Draft", back_populates="task", cascade="all, delete-orphan")
    approvals = relationship("Approval", back_populates="task", cascade="all, delete-orphan")
    external_links = relationship("ExternalLink", back_populates="task", cascade="all, delete-orphan")


class Draft(Base):
    """Version history of task drafts."""

    __tablename__ = "drafts"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    task = relationship("Task", back_populates="drafts")


class Approval(Base):
    """Approval/rejection history for tasks."""

    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    reviewer_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    action = Column(String(20), nullable=False)  # "approved" or "rejected"
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    task = relationship("Task", back_populates="approvals")
    reviewer = relationship("User")


class ExternalLink(Base):
    """Links to external resources (GitHub issues, Notion pages, etc.)."""

    __tablename__ = "external_links"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    link_type = Column(String(50), nullable=False)  # "github_issue", "notion_page", etc.
    url = Column(String(1000), nullable=False)
    link_metadata = Column(JSON)  # Additional provider-specific data
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    task = relationship("Task", back_populates="external_links")
