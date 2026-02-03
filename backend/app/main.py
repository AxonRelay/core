from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Path, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.graph import graph_app, checkpointer
from app.schema import (
    TaskRequest, ApprovalRequest, StatusResponse, UserSyncRequest, UserResponse,
    ProjectCreateRequest, ProjectUpdateRequest, ProjectResponse, ProjectWithMembersResponse,
    AddProjectMemberRequest, UpdateProjectMemberRoleRequest
)
from app.database import get_db
from app import crud, models
from sqlalchemy.orm import Session

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await checkpointer.asetup()
    yield


app = FastAPI(title="AxonRelay API", lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://axonrelay.com",
        "http://localhost",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.get("/")
def health():
    return {"status": "ok", "service": "AxonRelay"}


@app.post("/auth/sync", response_model=UserResponse)
@limiter.limit("30/minute")
async def sync_user(request: Request, user_data: UserSyncRequest, db: Session = Depends(get_db)):
    """Sync user from OAuth authentication to database."""
    user = crud.get_or_create_user(
        db=db,
        email=user_data.email,
        name=user_data.name,
        oauth_provider=user_data.oauth_provider,
        oauth_id=user_data.oauth_id
    )
    return user


@app.post("/task/start")
@limiter.limit("10/minute")
async def start_task(request: Request, req: TaskRequest):
    config = {"configurable": {"thread_id": req.thread_id}}
    initial_state = {
        "task": req.task,
        "draft": "",
        "feedback": "",
        "status": "processing",
    }
    async for _event in graph_app.astream(initial_state, config):
        pass
    return {"message": "Task started", "thread_id": req.thread_id}


@app.get("/task/{thread_id}", response_model=StatusResponse)
@limiter.limit("60/minute")
async def get_status(request: Request, thread_id: str = Path(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_\-]+$")):
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph_app.aget_state(config)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="Task not found")
    values = snapshot.values
    return StatusResponse(
        thread_id=thread_id,
        status=values.get("status", "unknown"),
        current_draft=values.get("draft"),
        next_action="wait_for_human" if snapshot.next else "completed",
    )


@app.post("/task/approve")
@limiter.limit("10/minute")
async def approve_task(request: Request, req: ApprovalRequest):
    config = {"configurable": {"thread_id": req.thread_id}}
    snapshot = await graph_app.aget_state(config)
    if not snapshot.next:
        raise HTTPException(status_code=400, detail="No task waiting for approval")
    if req.modified_draft:
        await graph_app.aupdate_state(
            config,
            {"draft": req.modified_draft, "feedback": "Human modified directly"},
        )
    async for _event in graph_app.astream(None, config):
        pass
    return {"message": "Task resumed and completed"}


# ========== Project Endpoints ==========

@app.get("/projects", response_model=list[ProjectResponse])
@limiter.limit("60/minute")
async def list_user_projects(request: Request, user_id: int, db: Session = Depends(get_db)):
    """List all projects where the user is a member."""
    projects = crud.get_user_projects(db, user_id)
    return projects


@app.post("/projects", response_model=ProjectResponse)
@limiter.limit("30/minute")
async def create_project(request: Request, project_data: ProjectCreateRequest, user_id: int, db: Session = Depends(get_db)):
    """Create a new project with the user as owner."""
    # Verify user exists
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    project = crud.create_project(
        db=db,
        name=project_data.name,
        description=project_data.description,
        owner_id=user_id
    )
    return project


@app.get("/projects/{project_id}", response_model=ProjectWithMembersResponse)
@limiter.limit("60/minute")
async def get_project(request: Request, project_id: int, user_id: int, db: Session = Depends(get_db)):
    """Get project details with members list."""
    # Check if user is a member
    member = crud.get_project_member(db, project_id, user_id)
    if not member:
        raise HTTPException(status_code=403, detail="Access denied")

    project = crud.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return project


@app.put("/projects/{project_id}", response_model=ProjectResponse)
@limiter.limit("30/minute")
async def update_project(request: Request, project_id: int, project_data: ProjectUpdateRequest, user_id: int, db: Session = Depends(get_db)):
    """Update project details (requires OWNER or ADMIN role)."""
    # Check permission
    if not crud.check_project_permission(db, project_id, user_id, [models.RoleEnum.OWNER, models.RoleEnum.ADMIN]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    project = crud.update_project(
        db=db,
        project_id=project_id,
        name=project_data.name,
        description=project_data.description
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return project


@app.delete("/projects/{project_id}")
@limiter.limit("30/minute")
async def delete_project(request: Request, project_id: int, user_id: int, db: Session = Depends(get_db)):
    """Delete a project (requires OWNER role)."""
    # Check permission
    if not crud.check_project_permission(db, project_id, user_id, [models.RoleEnum.OWNER]):
        raise HTTPException(status_code=403, detail="Only project owner can delete project")

    success = crud.delete_project(db, project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")

    return {"message": "Project deleted successfully"}


# ========== Project Member Endpoints ==========

@app.post("/projects/{project_id}/members")
@limiter.limit("30/minute")
async def add_project_member(request: Request, project_id: int, member_data: AddProjectMemberRequest, user_id: int, db: Session = Depends(get_db)):
    """Add a member to a project (requires OWNER or ADMIN role)."""
    # Check permission
    if not crud.check_project_permission(db, project_id, user_id, [models.RoleEnum.OWNER, models.RoleEnum.ADMIN]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Verify target user exists
    target_user = crud.get_user(db, member_data.user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a member
    existing_member = crud.get_project_member(db, project_id, member_data.user_id)
    if existing_member:
        raise HTTPException(status_code=400, detail="User is already a member")

    member = crud.add_project_member(
        db=db,
        project_id=project_id,
        user_id=member_data.user_id,
        role=models.RoleEnum(member_data.role)
    )
    return member


@app.patch("/projects/{project_id}/members/{member_user_id}")
@limiter.limit("30/minute")
async def update_member_role(request: Request, project_id: int, member_user_id: int, role_data: UpdateProjectMemberRoleRequest, user_id: int, db: Session = Depends(get_db)):
    """Update a project member's role (requires OWNER or ADMIN role)."""
    # Check permission
    if not crud.check_project_permission(db, project_id, user_id, [models.RoleEnum.OWNER, models.RoleEnum.ADMIN]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Prevent removing the last owner
    if role_data.role != "owner":
        members = crud.get_project_members(db, project_id)
        owner_count = sum(1 for m in members if m.role == models.RoleEnum.OWNER)
        if owner_count == 1:
            target_member = crud.get_project_member(db, project_id, member_user_id)
            if target_member and target_member.role == models.RoleEnum.OWNER:
                raise HTTPException(status_code=400, detail="Cannot remove the last owner")

    member = crud.update_project_member_role(
        db=db,
        project_id=project_id,
        user_id=member_user_id,
        role=models.RoleEnum(role_data.role)
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    return member


@app.delete("/projects/{project_id}/members/{member_user_id}")
@limiter.limit("30/minute")
async def remove_project_member(request: Request, project_id: int, member_user_id: int, user_id: int, db: Session = Depends(get_db)):
    """Remove a member from a project (requires OWNER or ADMIN role)."""
    # Check permission
    if not crud.check_project_permission(db, project_id, user_id, [models.RoleEnum.OWNER, models.RoleEnum.ADMIN]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Prevent removing the last owner
    members = crud.get_project_members(db, project_id)
    owner_count = sum(1 for m in members if m.role == models.RoleEnum.OWNER)
    if owner_count == 1:
        target_member = crud.get_project_member(db, project_id, member_user_id)
        if target_member and target_member.role == models.RoleEnum.OWNER:
            raise HTTPException(status_code=400, detail="Cannot remove the last owner")

    success = crud.remove_project_member(db, project_id, member_user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Member not found")

    return {"message": "Member removed successfully"}
