"""Database models for AxonRelay (personal PoC pivot, post-migration 003)."""

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
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

    EXECUTOR = "executor"
    REVIEWER = "reviewer"
    APPROVER = "approver"
    OBSERVER = "observer"


class TaskStatusEnum(enum.StrEnum):
    """Task status."""

    DRAFT = "draft"
    WAITING_REVIEW = "waiting_review"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# =============================================================================
# Actor / AgentDefinition / TaskAssignment
# =============================================================================


class Actor(Base):
    """Unified abstraction for humans and AI agents.

    Single human actor (name="self") represents the operator.
    AI actors are 1:1 with AgentDefinition rows.
    """

    __tablename__ = "actors"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(Enum(ActorTypeEnum), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    agent_definition = relationship("AgentDefinition", back_populates="actor", uselist=False)
    task_assignments = relationship("TaskAssignment", back_populates="actor", cascade="all, delete-orphan")
    created_tasks = relationship("Task", back_populates="creator", foreign_keys="Task.creator_actor_id")
    approvals = relationship("Approval", back_populates="reviewer", foreign_keys="Approval.reviewer_actor_id")


class AgentDefinition(Base):
    """AI Agent definition with configuration. 1:1 with Actor."""

    __tablename__ = "agent_definitions"

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, ForeignKey("actors.id", ondelete="CASCADE"), unique=True, nullable=False)
    agent_type = Column(Enum(AgentTypeEnum), nullable=False, index=True)
    description = Column(Text)
    config = Column(JSON)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    actor = relationship("Actor", back_populates="agent_definition")


class TaskAssignment(Base):
    """Assignment of an Actor (human or AI) to a Task with a specific role."""

    __tablename__ = "task_assignments"
    __table_args__ = (UniqueConstraint("task_id", "actor_id", "role", name="uq_assignment_task_actor_role"),)

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("actors.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(Enum(AssignmentRoleEnum), nullable=False, index=True)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("Task", back_populates="assignments")
    actor = relationship("Actor", back_populates="task_assignments")


# =============================================================================
# Task / Draft / Approval / ExternalLink
# =============================================================================


class Task(Base):
    """Task managed by AI agents with human-in-the-loop approval."""

    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String(100), unique=True, index=True, nullable=False)
    creator_actor_id = Column(Integer, ForeignKey("actors.id", ondelete="SET NULL"))

    title = Column(String(500), nullable=False)
    description = Column(Text)
    status = Column(Enum(TaskStatusEnum), nullable=False, default=TaskStatusEnum.DRAFT, index=True)

    current_draft = Column(Text)
    feedback = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    creator = relationship("Actor", back_populates="created_tasks", foreign_keys=[creator_actor_id])
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

    task = relationship("Task", back_populates="drafts")


class Approval(Base):
    """Approval / rejection history for tasks."""

    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    reviewer_actor_id = Column(Integer, ForeignKey("actors.id", ondelete="SET NULL"))
    action = Column(String(20), nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Tamper-evident hash chain (see app/ledger.py). prev_hash links to the
    # previous approval's entry_hash for the same task; entry_hash is this row's.
    prev_hash = Column(String(64))
    entry_hash = Column(String(64))

    task = relationship("Task", back_populates="approvals")
    reviewer = relationship("Actor", back_populates="approvals", foreign_keys=[reviewer_actor_id])


class ExternalLink(Base):
    """Links to external resources (MCP resource URI, GitHub issue, etc.)."""

    __tablename__ = "external_links"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    link_type = Column(String(50), nullable=False)
    url = Column(String(1000), nullable=False)
    link_metadata = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("Task", back_populates="external_links")
