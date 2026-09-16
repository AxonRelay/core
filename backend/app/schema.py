"""Pydantic schemas for AxonRelay (personal PoC, post-migration 003)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ========== Actor Schemas ==========


class ActorResponse(BaseModel):
    id: int
    type: str  # "human" or "ai"
    name: str
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
    config: dict | None
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
    approval_episode_id: str | None
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

    class Config:
        from_attributes = True


class ApproveRequest(BaseModel):
    approval_episode_id: str = Field(..., min_length=1, max_length=255)
    comment: str | None = Field(None, max_length=2000)
    modified_draft: str | None = Field(None, max_length=50000)


class RejectRequest(BaseModel):
    approval_episode_id: str = Field(..., min_length=1, max_length=255)
    comment: str | None = Field(None, max_length=2000)
    reason: str | None = Field(None, max_length=500)


class ApprovalResponse(BaseModel):
    id: int
    task_id: int
    reviewer_actor_id: int | None
    action: str
    comment: str | None
    approved_content: str | None = None
    approved_draft_sha256: str | None = None
    evidence_manifest: list[dict] | None = None
    approval_episode_id: str | None = None
    decision_key: str | None = None
    delivery_status: str = "pending"
    delivery_run_id: str | None = None
    delivery_error: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ApprovalSummaryResponse(BaseModel):
    """Redacted approval timeline safe for the read-only dashboard."""

    id: int
    task_id: int
    reviewer_actor_id: int | None
    action: str
    comment: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class ApprovalDeliveryResolutionRequest(BaseModel):
    outcome: str = Field(pattern="^(confirmed_delivered|confirmed_not_delivered)$")


# ========== Evidence Clip Schemas ==========


class EvidenceAnnotations(BaseModel):
    """Derived data only; verified facts require a separate governed action."""

    model_config = ConfigDict(extra="forbid")
    summary: str = Field(..., max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=8)
    source_claims: list[str] = Field(default_factory=list, max_length=5)
    interpretations: list[str] = Field(default_factory=list, max_length=5)


class EvidenceClipCreateRequest(BaseModel):
    source_url: str = Field(..., min_length=1, max_length=2000)
    source_title: str = Field(..., min_length=1, max_length=500)
    source_type: str = Field(..., pattern="^(public|personal)$")
    quote: str = Field(..., min_length=1, max_length=8000)
    locator: dict | None = None
    annotations: EvidenceAnnotations | None = None
    extractor: str | None = Field(None, max_length=100)
    extractor_version: str | None = Field(None, max_length=100)
    prompt_version: str | None = Field(None, max_length=100)
    inference_location: str = Field("none", pattern="^(none|device|lan)$")
    extraction_ms: float | None = Field(None, ge=0)


class EvidenceFeedbackRequest(BaseModel):
    verdict: str = Field(..., pattern="^(relevant|irrelevant|misleading)$")
    comment: str | None = Field(None, max_length=2000)
