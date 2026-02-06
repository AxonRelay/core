from typing import Optional, TypedDict
from datetime import datetime

from pydantic import BaseModel, Field


class AgentState(TypedDict):
    task: str
    draft: Optional[str]
    feedback: Optional[str]
    status: str


class TaskRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=10000)
    thread_id: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_\-]+$")


class ApprovalRequest(BaseModel):
    thread_id: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_\-]+$")
    approved: bool
    modified_draft: Optional[str] = Field(None, max_length=50000)


class StatusResponse(BaseModel):
    thread_id: str
    status: str
    current_draft: Optional[str]
    next_action: str


class UserSyncRequest(BaseModel):
    """Request to sync user from OAuth authentication."""
    email: str = Field(..., min_length=1, max_length=255)
    name: Optional[str] = Field(None, max_length=255)
    oauth_provider: str = Field(..., min_length=1, max_length=50)
    oauth_id: str = Field(..., min_length=1, max_length=255)


class UserResponse(BaseModel):
    """User response model."""
    id: int
    email: str
    name: Optional[str]
    oauth_provider: str
    oauth_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ========== Project Schemas ==========

class ProjectCreateRequest(BaseModel):
    """Request to create a new project."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class ProjectUpdateRequest(BaseModel):
    """Request to update project details."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None


class ProjectResponse(BaseModel):
    """Project response model."""
    id: int
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectMemberResponse(BaseModel):
    """Project member response model."""
    id: int
    user_id: int
    project_id: int
    role: str
    joined_at: datetime
    user: UserResponse

    class Config:
        from_attributes = True


class ProjectWithMembersResponse(ProjectResponse):
    """Project response with members list."""
    members: list[ProjectMemberResponse] = []

    class Config:
        from_attributes = True


class AddProjectMemberRequest(BaseModel):
    """Request to add a member to a project."""
    user_id: int = Field(..., gt=0)
    role: str = Field(..., pattern="^(owner|admin|member|reviewer|viewer)$")


class UpdateProjectMemberRoleRequest(BaseModel):
    """Request to update a project member's role."""
    role: str = Field(..., pattern="^(owner|admin|member|reviewer|viewer)$")


# ========== Actor Schemas (ADR-005) ==========

class ActorResponse(BaseModel):
    """Actor response model (lightweight reference)."""
    id: int
    type: str  # "human" or "ai"
    name: str
    created_at: datetime

    class Config:
        from_attributes = True


class AgentDefinitionCreateRequest(BaseModel):
    """Request to create a new AI agent definition."""
    name: str = Field(..., min_length=1, max_length=255)
    agent_type: str = Field(..., pattern="^(writer|reviewer|validator|researcher|assistant|custom)$")
    description: Optional[str] = None
    config: Optional[dict] = None


class AgentDefinitionUpdateRequest(BaseModel):
    """Request to update an AI agent definition."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    agent_type: Optional[str] = Field(None, pattern="^(writer|reviewer|validator|researcher|assistant|custom)$")
    description: Optional[str] = None
    config: Optional[dict] = None
    is_active: Optional[bool] = None


class AgentDefinitionResponse(BaseModel):
    """AI agent definition response model."""
    id: int
    actor_id: int
    agent_type: str
    description: Optional[str]
    config: Optional[dict]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    actor: ActorResponse

    class Config:
        from_attributes = True


# ========== Task Assignment Schemas ==========

class TaskAssignmentCreateRequest(BaseModel):
    """Request to create a task assignment."""
    actor_id: int = Field(..., gt=0)
    role: str = Field(..., pattern="^(executor|reviewer|approver|observer)$")


class TaskAssignmentResponse(BaseModel):
    """Task assignment response model."""
    id: int
    task_id: int
    actor_id: int
    role: str
    assigned_at: datetime
    actor: ActorResponse

    class Config:
        from_attributes = True


class UserWithActorResponse(UserResponse):
    """User response with actor information."""
    actor: Optional[ActorResponse] = None

    class Config:
        from_attributes = True
