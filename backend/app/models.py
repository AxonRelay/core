"""Database models for AxonRelay (personal PoC pivot, post-migration 003)."""

import enum
import secrets
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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
from sqlalchemy.orm import relationship, validates

from app.database import Base

#: Bytes of randomness in an opaque identifier, kept in step with
#: app.disclosure.OPAQUE_ID_BYTES and migration 012. Defined here, not
#: imported, because `disclosure` imports this module.
OPAQUE_ID_BYTES = 12


def _new_opaque_id() -> str:
    """Default for every opaque_id column, so a row cannot exist without one."""
    return secrets.token_urlsafe(OPAQUE_ID_BYTES)


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
    # Stable, unguessable stand-in for `name` at a shared boundary. A name is
    # chosen by an operator and is arbitrary text - it routinely carries a
    # person, a machine or a project - so it is not something a shared instance
    # should hand back. Random rather than derived: a digest of a name a peer
    # can guess is not opaque.
    opaque_id = Column(String(32), unique=True, index=True, default=_new_opaque_id)
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

    @property
    def config_keys(self) -> list[str] | None:
        """The config's key names, never its values — it is where credentials go.

        A property rather than a serializer detail so every response model that
        reads this row from attributes gets the same answer (app/disclosure.py).
        """
        from app import disclosure  # local: disclosure imports models

        return disclosure.config_keys(self.config)


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

    # Which payload the row was hashed with: 1 = event only (migrations
    # 004–008; 009 stamps those rows), 2 = artifact-bound. NULL only on rows
    # that were never hashed (pre-004). Verification picks the payload by this.
    hash_version = Column(Integer)

    # Artifact binding (migration 009) — all five fields are inside the v2
    # hash. NULL on legacy rows, which are reported as not artifact-bound.
    artifact_ref = Column(String(255))
    artifact_version = Column(Integer)
    artifact_commitment = Column(String(64))
    artifact_commitment_algorithm = Column(String(32))
    producer_actor_id = Column(Integer)  # not a FK, same reason as Draft

    # Retry de-duplication (migration 013). A caller that can re-send the same
    # decision - the 2026-07-28 multi-round tools/call replays its whole
    # request - names the decision once here, and the unique index makes a
    # second append impossible rather than unlikely. Inside the v3 hash
    # payload (app/ledger.py): the server acts on it, so a value verification
    # could not see could be cleared to defeat the de-duplication.
    decision_key = Column(String(64))

    __table_args__ = (
        UniqueConstraint("task_id", "decision_key", name="uq_approval_decision_key"),
        # A key only means anything on a payload that hashes it. Without this,
        # one could be planted on a legacy row - free, because that row's hash
        # does not cover the field - and the unique index would then refuse the
        # real decision it belongs to. `verify_approval_chain` reports such a
        # row as tampered; this stops it being written in the first place.
        CheckConstraint(
            "decision_key IS NULL OR (hash_version IS NOT NULL AND hash_version >= 3)",
            name="ck_approval_decision_key_needs_v3",
        ),
    )

    task = relationship("Task", back_populates="approvals")
    reviewer = relationship("Actor", back_populates="approvals", foreign_keys=[reviewer_actor_id])

    @property
    def artifact_bound(self) -> bool:
        """True when this entry names the exact artifact it approved."""
        from app import ledger  # ledger has no model imports; local to keep the module graph acyclic

        return (self.hash_version or 1) >= ledger.ARTIFACT_BINDING_SINCE and self.artifact_commitment is not None


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

    @validates("url")
    def _sanitize_url(self, _key: str, value: str | None) -> str | None:
        """Refuse a URL that carries a credential, a query, a fragment or an odd scheme.

        On the model rather than in a caller: this row is the one place the
        schema invites a URL, and it has had no writer until now. Putting the
        rule here means the first writer inherits it (app/disclosure.py).
        """
        from app import disclosure  # local: disclosure imports models

        return disclosure.sanitize_url(value)


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


class FocusCodeEnum(enum.StrEnum):
    """What a session is doing, as a code rather than a sentence.

    Free-form focus text is useful to a human reading the board and is exactly
    the kind of thing that should not cross a shared boundary: it quotes file
    names, ticket titles and sometimes the work itself. These cover what a peer
    actually needs in order to decide whether to wait or work elsewhere.
    """

    EXPLORING = "exploring"
    IMPLEMENTING = "implementing"
    REVIEWING = "reviewing"
    TESTING = "testing"
    DEBUGGING = "debugging"
    DOCUMENTING = "documenting"
    RELEASING = "releasing"
    BLOCKED = "blocked"
    IDLE = "idle"


class ClaimReasonCodeEnum(enum.StrEnum):
    """Why a territory or resource claim was taken."""

    EDITING = "editing"
    REFACTORING = "refactoring"
    RUNNING_TESTS = "running_tests"
    MIGRATING = "migrating"
    RELEASING = "releasing"
    INVESTIGATING = "investigating"


class RelayCodeEnum(enum.StrEnum):
    """What a relay is asking for, without saying it in prose."""

    HANDOFF_READY = "handoff_ready"
    NEEDS_REVIEW = "needs_review"
    BLOCKED_ON_YOU = "blocked_on_you"
    CONFLICT_DETECTED = "conflict_detected"
    RELEASE_REQUESTED = "release_requested"
    HEADS_UP = "heads_up"
    ANSWERED = "answered"


class AckCodeEnum(enum.StrEnum):
    """How a recipient answered a relay."""

    ACKNOWLEDGED = "acknowledged"
    DONE = "done"
    DECLINED = "declined"
    DEFERRED = "deferred"
    NOT_APPLICABLE = "not_applicable"


class SafeActionEnum(enum.StrEnum):
    """What a Safe Envelope reports happened. Closed set; free text has no slot."""

    SESSION_START = "session_start"
    SESSION_HEARTBEAT = "session_heartbeat"
    SESSION_END = "session_end"
    CLAIM_REQUEST = "claim_request"
    CLAIM_RELEASE = "claim_release"
    ARTIFACT_PRODUCED = "artifact_produced"
    DECISION_APPROVE = "decision_approve"
    DECISION_REJECT = "decision_reject"
    RELAY_NOTICE = "relay_notice"
    RELAY_ACK = "relay_ack"


class SafeOutcomeEnum(enum.StrEnum):
    SUCCESS = "success"
    REFUSED = "refused"
    CONFLICT = "conflict"
    ERROR = "error"


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
    # The checkout's identity at a shared boundary. `host`, `clone_path` and
    # `git_dir` name a machine and a filesystem; this names the same checkout
    # without describing it, and stays the same across restarts so claim and
    # relay history remains continuous.
    opaque_id = Column(String(32), unique=True, index=True, default=_new_opaque_id)
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
    #: The disclosable form of `focus`. Set it and a shared boundary has
    #: something to say about this session without quoting the prose.
    focus_code = Column(Enum(FocusCodeEnum))
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
    reason_code = Column(Enum(ClaimReasonCodeEnum))
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
    code = Column(Enum(RelayCodeEnum))
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
    ack_code = Column(Enum(AckCodeEnum))

    relay = relationship("Relay", back_populates="receipts")
    session = relationship("Session")


class SafeEvent(Base):
    """One ingested Safe Envelope (app/safe_envelope.py). Append-only, metadata only.

    Every column is a bounded identifier, an enum, a digest or a timestamp. The
    table has no text column by design: a producer that wanted to send a
    title, a body or a path has no field to put it in, and the schema rejects
    unknown fields before anything reaches this row.
    """

    __tablename__ = "safe_events"

    id = Column(Integer, primary_key=True, index=True)
    schema_version = Column(Integer, nullable=False)
    policy_version = Column(String(32), nullable=False)
    identifier_policy = Column(String(16), nullable=False)

    event_id = Column(String(128), nullable=False, unique=True, index=True)
    actor_ref = Column(String(128), nullable=False, index=True)
    repository_ref = Column(String(201), nullable=False, index=True)
    workspace_ref = Column(String(128))
    session_ref = Column(String(128), index=True)

    action = Column(Enum(SafeActionEnum), nullable=False, index=True)
    outcome = Column(Enum(SafeOutcomeEnum), nullable=False)

    artifact_ref = Column(String(128))
    artifact_version = Column(Integer)
    artifact_commitment = Column(String(64))
    artifact_commitment_algorithm = Column(String(32))

    occurred_at = Column(DateTime, nullable=False, index=True)
    received_at = Column(DateTime, nullable=False)
    producer_signature = Column(String(1024))


class Credential(Base):
    """An issued API credential: which Actor a caller is, and what it may do.

    Only the SHA-256 of the token is stored (`token_hash`, unique so a lookup
    is a single indexed hit and never a scan). The token itself exists once,
    in the output of `python -m app.credentials issue`; nothing here can
    recover it, and nothing logs it. See app/authz.py.

    `actor_id` is a real FK with CASCADE: deleting an Actor must take its
    credentials with it, or a deleted identity would keep authenticating.
    That is the opposite of the ledger's rule (where a hashed reference must
    never change), and deliberately so - this row is access control, not
    evidence.
    """

    __tablename__ = "credentials"

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, ForeignKey("actors.id", ondelete="CASCADE"), nullable=False, index=True)
    label = Column(String(255), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    #: Comma-separated Scope values; parsed by authz.parse_scopes, which drops
    #: a name it does not know rather than guessing at it.
    scopes = Column(String(255), nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime)
    revoked_at = Column(DateTime)

    actor = relationship("Actor")
