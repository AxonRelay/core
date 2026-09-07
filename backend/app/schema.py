"""Pydantic schemas for AxonRelay (personal PoC, post-migration 003)."""

from datetime import datetime

from pydantic import BaseModel, Field

from app import ledger

# ========== Actor Schemas ==========


class ActorResponse(BaseModel):
    """An Actor as a shared boundary may describe it.

    `name` is arbitrary text an operator chose, so in content-blind mode it is
    replaced by `actor_ref` (app/disclosure.py). Both are optional because
    exactly one of them is present, and which one is the policy's decision, not
    the caller's. Built through `app.disclosure.actor_view`, so this matches
    what the MCP surface says about the same row.
    """

    id: int
    type: str  # "human" or "ai"
    name: str | None = None
    actor_ref: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


# ========== AgentDefinition Schemas ==========


class AgentDefinitionCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    agent_type: str = Field(..., pattern="^(writer|reviewer|validator|researcher|assistant|custom)$")
    description: str | None = None
    config: dict | None = None


class AgentDefinitionUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    agent_type: str | None = Field(None, pattern="^(writer|reviewer|validator|researcher|assistant|custom)$")
    description: str | None = None
    config: dict | None = None
    is_active: bool | None = None


class AgentDefinitionResponse(BaseModel):
    id: int
    actor_id: int
    agent_type: str
    description: str | None
    #: The config's key names, never its values - it is where credentials go
    #: (app/disclosure.py). Populated from the ORM row by `config_keys`.
    config_keys: list[str] | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    actor: ActorResponse

    class Config:
        from_attributes = True


# ========== TaskAssignment Schemas ==========


class TaskAssignmentCreateRequest(BaseModel):
    actor_id: int = Field(..., gt=0)
    role: str = Field(..., pattern="^(executor|reviewer|approver|observer)$")


class TaskAssignmentResponse(BaseModel):
    id: int
    task_id: int
    actor_id: int
    role: str
    assigned_at: datetime
    actor: ActorResponse

    class Config:
        from_attributes = True


# ========== Task Schemas ==========

_TASK_STATUS_PATTERN = "^(draft|waiting_review|waiting_approval|approved|rejected|needs_revision|completed|cancelled)$"


class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    assignments: list[TaskAssignmentCreateRequest] | None = None


class TaskUpdateRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = None
    status: str | None = Field(None, pattern=_TASK_STATUS_PATTERN)
    current_draft: str | None = None
    feedback: str | None = None


class TaskResponse(BaseModel):
    id: int
    thread_id: str
    creator_actor_id: int | None
    title: str
    description: str | None
    status: str
    current_draft: str | None
    feedback: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TaskWithAssignmentsResponse(TaskResponse):
    assignments: list[TaskAssignmentResponse] = []

    class Config:
        from_attributes = True


# ========== Draft / Approval Schemas ==========


class DraftResponse(BaseModel):
    id: int
    task_id: int
    version: int
    content: str
    created_at: datetime
    # Artifact commitment (see app/ledger.py). None only on rows that predate
    # migration 009 and were never bound.
    commitment: str | None = None
    commitment_algorithm: str | None = None
    producer_actor_id: int | None = None

    class Config:
        from_attributes = True


class _DecisionTarget(BaseModel):
    """Optional statement of which draft the caller reviewed.

    When either is given and the task's latest draft no longer matches, the
    decision is refused (HTTP 409) and nothing is recorded — you never approve
    content you did not see. Omit both to decide on whatever is current.
    """

    artifact_version: int | None = Field(None, ge=1)
    expected_commitment: str | None = Field(None, pattern=ledger.SHA256_HEX_PATTERN)


class ApproveRequest(_DecisionTarget):
    comment: str | None = Field(None, max_length=2000)
    modified_draft: str | None = Field(None, max_length=50000)


class RejectRequest(_DecisionTarget):
    comment: str | None = Field(None, max_length=2000)
    reason: str | None = Field(None, max_length=500)


class ApprovalResponse(BaseModel):
    id: int
    task_id: int
    reviewer_actor_id: int | None
    action: str
    comment: str | None
    created_at: datetime
    # Hash chain (tamper-evidence of the event).
    prev_hash: str | None = None
    entry_hash: str | None = None
    hash_version: int | None = None
    # Artifact binding: which draft this entry decided on. All None, and
    # artifact_bound False, on entries recorded before migration 009.
    artifact_bound: bool = False
    artifact_ref: str | None = None
    artifact_version: int | None = None
    artifact_commitment: str | None = None
    artifact_commitment_algorithm: str | None = None
    producer_actor_id: int | None = None

    class Config:
        from_attributes = True
