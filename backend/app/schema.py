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
