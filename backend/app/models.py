"""Database models for AxonRelay (personal PoC pivot, post-migration 003)."""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    """Version history of task drafts — the artifacts approvals bind to.

    ``(task_id, version)`` is unique so a version number names exactly one
    artifact. ``commitment`` is the SHA-256 of the content (see
    app/ledger.py); it is what an approval records, so the ledger can prove
    which bytes were approved without ever re-reading them.
    """

    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_drafts_task_version"),)

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Artifact commitment (migration 009). NULL only on rows the backfill
    # could not reach; record_approval fills it in before binding to them.
    commitment = Column(String(64))
    commitment_algorithm = Column(String(32))
    # Producer of this version. Deliberately not a FK: a ledger field must not
    # be rewritten (SET NULL) because an Actor row was deleted.
    producer_actor_id = Column(Integer)

    task = relationship("Task", back_populates="drafts")


class Approval(Base):
    """Approval / rejection history for tasks."""

    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    reviewer_actor_id = Column(Integer, ForeignKey("actors.id", ondelete="SET NULL"))
    action = Column(String(20), nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Tamper-evident hash chain (see app/ledger.py). prev_hash links to the
    # previous approval's entry_hash for the same task; entry_hash is this row's.
    prev_hash = Column(String(64))
    entry_hash = Column(String(64))

    # Which payload the row was hashed with: NULL = v1 (event only, migrations
    # 004–008), 2 = artifact-bound. Verification picks the payload by this.
    hash_version = Column(Integer)

    # Artifact binding (migration 009) — all five fields are inside the v2
    # hash. NULL on legacy rows, which are reported as not artifact-bound.
    artifact_ref = Column(String(255))
    artifact_version = Column(Integer)
    artifact_commitment = Column(String(64))
    artifact_commitment_algorithm = Column(String(32))
    producer_actor_id = Column(Integer)  # not a FK, same reason as Draft

    task = relationship("Task", back_populates="approvals")
    reviewer = relationship("Actor", back_populates="approvals", foreign_keys=[reviewer_actor_id])

    @property
    def artifact_bound(self) -> bool:
        """True when this entry names the exact artifact it approved."""
        return self.hash_version == 2 and self.artifact_commitment is not None


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


# =============================================================================
# Coordination layer (Phase 3) - Workspace / Session / Claim / Relay
# =============================================================================
#
# The ledger above answers "who approved what". These tables answer the
# question that arises once several agents work at once across several
# machines: "who is working where, on what, right now - and are we about to
# collide?" See docs/coordination-spec.md.


class SessionStatusEnum(enum.StrEnum):
    """Lifecycle of an agent's working session."""

    ACTIVE = "active"
    ENDED = "ended"


class ClaimModeEnum(enum.StrEnum):
    """Territory claim strength, with reader/writer semantics.

    EXCLUSIVE conflicts with any overlapping claim; SHARED conflicts only with
    an overlapping EXCLUSIVE one (several readers coexist).
    """

    EXCLUSIVE = "exclusive"
    SHARED = "shared"


class ClaimResourceEnum(enum.StrEnum):
    """A git resource that is shared and cannot be split by path.

    Path claims cover "who edits which files". These cover the mutable
    singletons a git checkout has exactly one of, which no path pattern can
    describe - and whose sharing boundaries differ:

    WORKTREE  the working tree, index and HEAD of one checkout. Shared by every
              session in the same Workspace.
    STASH     the stash stack. ``refs/stash`` is a per-repository ref, so it is
              shared by **every git worktree of the same clone** - checking out
              a second worktree does not give you a second stash. This is what
              lets one session pop another session's parked work.
    REFS      local branches and tags. Also per-repository, so a branch deletion
              or a branch move is visible to every worktree of that clone.
    REMOTE    the refs on the shared remote. A force-push, `+refspec` or
              `push --delete` lands on the *same* remote from every clone on
              every host, so this contends across the whole repo - the one
              resource whose boundary is neither the checkout nor the clone.
    """

    WORKTREE = "worktree"
    STASH = "stash"
    REFS = "refs"
    REMOTE = "remote"


class ClaimStatusEnum(enum.StrEnum):
    """Territory claim state. Rows are never deleted, only transitioned.

    Expiry is not a stored state: a claim is live only while it is HELD *and*
    ``expires_at`` is in the future, evaluated at query time. That keeps a dead
    agent's claims from lingering without needing a reaper process.
    """

    HELD = "held"
    RELEASED = "released"


class RelayKindEnum(enum.StrEnum):
    """What a relay message is for - drives how urgently a peer should read it."""

    NOTE = "note"
    QUESTION = "question"
    ANSWER = "answer"
    HANDOFF = "handoff"
    WARNING = "warning"


class Workspace(Base):
    """One checkout of one repository on one machine.

    Stable across sessions: the same clone re-registering after a restart maps
    back to the same Workspace row, so its claim and relay history is continuous.
    ``repo`` should be the canonical remote identity (e.g. ``AxonRelay/core``) so
    that sibling clones of the same repository agree on it.
    """

    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("host", "repo", "clone_path", name="uq_workspace_identity"),)

    id = Column(Integer, primary_key=True, index=True)
    host = Column(String(255), nullable=False, index=True)
    repo = Column(String(255), nullable=False, index=True)
    clone_path = Column(String(1000), nullable=False)

    # `git rev-parse --git-common-dir`, absolute. Sibling worktrees of one clone
    # share this while having different clone_paths, and that is exactly the set
    # that shares a stash stack - so STASH conflicts are scoped by (host,
    # git_dir), not by repo (too wide: separate clones are independent) and not
    # by workspace (too narrow: it would miss the sibling-worktree collision).
    git_dir = Column(String(1000), index=True)

    label = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    sessions = relationship("Session", back_populates="workspace", cascade="all, delete-orphan")


class Session(Base):
    """An Actor working inside a Workspace over a stretch of time.

    This is the unit that holds claims and receives relays. One active session
    per (actor, workspace) - re-registering the same pair resumes it rather than
    creating a parallel one.
    """

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_workspace_status", "workspace_id", "status"),)

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, ForeignKey("actors.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)

    branch = Column(String(255))
    focus = Column(Text)
    status = Column(Enum(SessionStatusEnum), nullable=False, default=SessionStatusEnum.ACTIVE, index=True)

    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_heartbeat_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    ended_at = Column(DateTime)

    actor = relationship("Actor")
    workspace = relationship("Workspace", back_populates="sessions")
    claims = relationship("Claim", back_populates="session", cascade="all, delete-orphan")


class Claim(Base):
    """An advisory, expiring lease on part of a repository, or on a git resource.

    Advisory: holding one does not stop anyone writing. It makes the collision
    *visible* before it happens, which is what a fleet of semi-autonomous agents
    can actually act on. ``expires_at`` bounds the damage from an agent that dies
    holding a claim.
    """

    __tablename__ = "claims"
    __table_args__ = (Index("ix_claims_repo_status", "repo", "status"),)

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    repo = Column(String(255), nullable=False, index=True)

    # Exactly one of these describes what is claimed: `paths` for a file-scoped
    # claim, `resource` for one of the shared git singletons above.
    paths = Column(JSON, nullable=False, default=list)
    resource = Column(Enum(ClaimResourceEnum), index=True)

    mode = Column(Enum(ClaimModeEnum), nullable=False, default=ClaimModeEnum.EXCLUSIVE)
    reason = Column(Text)
    status = Column(Enum(ClaimStatusEnum), nullable=False, default=ClaimStatusEnum.HELD, index=True)

    # Set when the claim was granted over a live conflict via force=True. The
    # override is part of the record, not a silent success.
    forced_over = Column(JSON)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    released_at = Column(DateTime)

    session = relationship("Session", back_populates="claims")


class Relay(Base):
    """A durable, addressed message between sessions - the semi-synchronous channel.

    Delivery is pull-based: a peer reads its inbox when it next takes a turn,
    rather than being interrupted. Addressing is by audience, not by connection,
    so a message survives the recipient restarting or moving machines:

    - ``to_actor_id``     - every session of that actor
    - ``to_workspace_id`` - every session in that clone
    - ``to_repo``         - every session working on that repository
    - all three NULL      - broadcast to the fleet
    """

    __tablename__ = "relays"
    __table_args__ = (Index("ix_relays_created", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    from_session_id = Column(Integer, ForeignKey("sessions.id", ondelete="SET NULL"), index=True)
    from_actor_id = Column(Integer, ForeignKey("actors.id", ondelete="SET NULL"), index=True)

    to_actor_id = Column(Integer, ForeignKey("actors.id", ondelete="CASCADE"), index=True)
    to_workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    to_repo = Column(String(255), index=True)

    kind = Column(Enum(RelayKindEnum), nullable=False, default=RelayKindEnum.NOTE, index=True)
    subject = Column(String(500), nullable=False)
    body = Column(Text)
    in_reply_to_id = Column(Integer, ForeignKey("relays.id", ondelete="SET NULL"))

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    from_session = relationship("Session", foreign_keys=[from_session_id])
    from_actor = relationship("Actor", foreign_keys=[from_actor_id])
    receipts = relationship("RelayReceipt", back_populates="relay", cascade="all, delete-orphan")


class RelayReceipt(Base):
    """Per-recipient acknowledgement of a Relay.

    Read/ack state lives here rather than on the Relay because one broadcast has
    many recipients: one peer acking must not hide the message from the others,
    and "who has actually seen this" is the auditable part.
    """

    __tablename__ = "relay_receipts"
    __table_args__ = (UniqueConstraint("relay_id", "session_id", name="uq_receipt_relay_session"),)

    id = Column(Integer, primary_key=True, index=True)
    relay_id = Column(Integer, ForeignKey("relays.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)

    read_at = Column(DateTime)
    acked_at = Column(DateTime)
    ack_note = Column(Text)

    relay = relationship("Relay", back_populates="receipts")
    session = relationship("Session")
