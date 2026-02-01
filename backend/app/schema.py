from typing import Optional, TypedDict

from pydantic import BaseModel


class AgentState(TypedDict):
    task: str
    draft: Optional[str]
    feedback: Optional[str]
    status: str


class TaskRequest(BaseModel):
    task: str
    thread_id: str


class ApprovalRequest(BaseModel):
    thread_id: str
    approved: bool
    modified_draft: Optional[str] = None


class StatusResponse(BaseModel):
    thread_id: str
    status: str
    current_draft: Optional[str]
    next_action: str
