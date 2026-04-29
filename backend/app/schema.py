"""Pydantic schemas for AxonRelay (personal PoC, post-migration 003)."""

from datetime import datetime

from pydantic import BaseModel, Field

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
    comment: str | None = Field(None, max_length=2000)
    modified_draft: str | None = Field(None, max_length=50000)


class RejectRequest(BaseModel):
    comment: str | None = Field(None, max_length=2000)
    reason: str | None = Field(None, max_length=500)


class ApprovalResponse(BaseModel):
    id: int
    task_id: int
    reviewer_actor_id: int | None
    action: str
    comment: str | None
    created_at: datetime

    class Config:
        from_attributes = True
