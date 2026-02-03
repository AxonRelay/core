from typing import Optional, TypedDict

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
