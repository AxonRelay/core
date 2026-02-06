from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app import crud, models
from app.database import get_db
from app.graph import checkpointer, graph_app
from app.schema import (
    ActorResponse,
    AddProjectMemberRequest,
    AgentDefinitionCreateRequest,
    AgentDefinitionResponse,
    AgentDefinitionUpdateRequest,
    ApprovalRequest,
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
    ProjectWithMembersResponse,
    StatusResponse,
    TaskAssignmentCreateRequest,
    TaskAssignmentResponse,
    TaskCreateRequest,
    TaskRequest,
    TaskResponse,
    TaskUpdateRequest,
    TaskWithAssignmentsResponse,
    UpdateProjectMemberRoleRequest,
    UserResponse,
    UserSyncRequest,
)

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
        oauth_id=user_data.oauth_id,
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
async def get_status(
    request: Request, thread_id: str = Path(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_\-]+$")
):
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
async def create_project(
    request: Request, project_data: ProjectCreateRequest, user_id: int, db: Session = Depends(get_db)
):
    """Create a new project with the user as owner."""
    # Verify user exists
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    project = crud.create_project(db=db, name=project_data.name, description=project_data.description, owner_id=user_id)
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
async def update_project(
    request: Request, project_id: int, project_data: ProjectUpdateRequest, user_id: int, db: Session = Depends(get_db)
):
    """Update project details (requires OWNER or ADMIN role)."""
    # Check permission
    if not crud.check_project_permission(db, project_id, user_id, [models.RoleEnum.OWNER, models.RoleEnum.ADMIN]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    project = crud.update_project(
        db=db, project_id=project_id, name=project_data.name, description=project_data.description
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
async def add_project_member(
    request: Request, project_id: int, member_data: AddProjectMemberRequest, user_id: int, db: Session = Depends(get_db)
):
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
        db=db, project_id=project_id, user_id=member_data.user_id, role=models.RoleEnum(member_data.role)
    )
    return member


@app.patch("/projects/{project_id}/members/{member_user_id}")
@limiter.limit("30/minute")
async def update_member_role(
    request: Request,
    project_id: int,
    member_user_id: int,
    role_data: UpdateProjectMemberRoleRequest,
    user_id: int,
    db: Session = Depends(get_db),
):
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
        db=db, project_id=project_id, user_id=member_user_id, role=models.RoleEnum(role_data.role)
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    return member


@app.delete("/projects/{project_id}/members/{member_user_id}")
@limiter.limit("30/minute")
async def remove_project_member(
    request: Request, project_id: int, member_user_id: int, user_id: int, db: Session = Depends(get_db)
):
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


# ========== Actor Endpoints (ADR-005) ==========


@app.get("/actors", response_model=list[ActorResponse])
@limiter.limit("60/minute")
async def list_actors(request: Request, type: str | None = None, db: Session = Depends(get_db)):
    """List all actors, optionally filtered by type (human/ai)."""
    actor_type = None
    if type:
        try:
            actor_type = models.ActorTypeEnum(type)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid actor type. Must be 'human' or 'ai'")

    actors = crud.get_actors(db, actor_type=actor_type)
    return actors


@app.get("/actors/{actor_id}", response_model=ActorResponse)
@limiter.limit("60/minute")
async def get_actor(request: Request, actor_id: int, db: Session = Depends(get_db)):
    """Get an actor by ID."""
    actor = crud.get_actor(db, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="Actor not found")
    return actor


# ========== Agent Definition Endpoints ==========


@app.get("/agents", response_model=list[AgentDefinitionResponse])
@limiter.limit("60/minute")
async def list_agents(
    request: Request, agent_type: str | None = None, is_active: bool | None = None, db: Session = Depends(get_db)
):
    """List all AI agent definitions."""
    agent_type_enum = None
    if agent_type:
        try:
            agent_type_enum = models.AgentTypeEnum(agent_type)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid agent type")

    agents = crud.get_agent_definitions(db, agent_type=agent_type_enum, is_active=is_active)
    return agents


@app.post("/agents", response_model=AgentDefinitionResponse)
@limiter.limit("30/minute")
async def create_agent(request: Request, agent_data: AgentDefinitionCreateRequest, db: Session = Depends(get_db)):
    """Create a new AI agent definition."""
    try:
        agent_type = models.AgentTypeEnum(agent_data.agent_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent type")

    agent = crud.create_agent_definition(
        db=db, name=agent_data.name, agent_type=agent_type, description=agent_data.description, config=agent_data.config
    )
    return agent


@app.get("/agents/{agent_id}", response_model=AgentDefinitionResponse)
@limiter.limit("60/minute")
async def get_agent(request: Request, agent_id: int, db: Session = Depends(get_db)):
    """Get an AI agent definition by ID."""
    agent = crud.get_agent_definition(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@app.put("/agents/{agent_id}", response_model=AgentDefinitionResponse)
@limiter.limit("30/minute")
async def update_agent(
    request: Request, agent_id: int, agent_data: AgentDefinitionUpdateRequest, db: Session = Depends(get_db)
):
    """Update an AI agent definition."""
    agent_type = None
    if agent_data.agent_type:
        try:
            agent_type = models.AgentTypeEnum(agent_data.agent_type)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid agent type")

    agent = crud.update_agent_definition(
        db=db,
        agent_id=agent_id,
        name=agent_data.name,
        agent_type=agent_type,
        description=agent_data.description,
        config=agent_data.config,
        is_active=agent_data.is_active,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@app.delete("/agents/{agent_id}")
@limiter.limit("30/minute")
async def delete_agent(request: Request, agent_id: int, db: Session = Depends(get_db)):
    """Delete an AI agent definition."""
    success = crud.delete_agent_definition(db, agent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"message": "Agent deleted successfully"}


# ========== Task Assignment Endpoints ==========


@app.get("/tasks/{task_id}/assignments", response_model=list[TaskAssignmentResponse])
@limiter.limit("60/minute")
async def list_task_assignments(request: Request, task_id: int, db: Session = Depends(get_db)):
    """List all assignments for a task."""
    assignments = crud.get_task_assignments(db, task_id)
    return assignments


@app.post("/tasks/{task_id}/assignments", response_model=TaskAssignmentResponse)
@limiter.limit("30/minute")
async def create_task_assignment(
    request: Request, task_id: int, assignment_data: TaskAssignmentCreateRequest, db: Session = Depends(get_db)
):
    """Assign an actor (human or AI) to a task."""
    # Verify actor exists
    actor = crud.get_actor(db, assignment_data.actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="Actor not found")

    try:
        role = models.AssignmentRoleEnum(assignment_data.role)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid assignment role")

    assignment = crud.create_task_assignment(db=db, task_id=task_id, actor_id=assignment_data.actor_id, role=role)
    return assignment


@app.delete("/tasks/{task_id}/assignments/{actor_id}")
@limiter.limit("30/minute")
async def remove_task_assignment(request: Request, task_id: int, actor_id: int, db: Session = Depends(get_db)):
    """Remove an assignment from a task."""
    success = crud.delete_task_assignment_by_actor(db, task_id, actor_id)
    if not success:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return {"message": "Assignment removed successfully"}


@app.get("/actors/{actor_id}/assignments", response_model=list[TaskAssignmentResponse])
@limiter.limit("60/minute")
async def list_actor_assignments(request: Request, actor_id: int, db: Session = Depends(get_db)):
    """List all task assignments for an actor."""
    # Verify actor exists
    actor = crud.get_actor(db, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="Actor not found")

    assignments = crud.get_actor_assignments(db, actor_id)
    return assignments


# ========== Task CRUD Endpoints ==========


@app.get("/projects/{project_id}/tasks", response_model=list[TaskResponse])
@limiter.limit("60/minute")
async def list_project_tasks(
    request: Request,
    project_id: int,
    user_id: int,
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """List all tasks in a project."""
    # Check user is a member of the project
    if not crud.get_project_member(db, project_id, user_id):
        raise HTTPException(status_code=403, detail="Access denied")

    status_enum = None
    if status:
        try:
            status_enum = models.TaskStatusEnum(status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")

    tasks = crud.get_project_tasks(db, project_id, status=status_enum, skip=skip, limit=limit)
    return tasks


@app.post("/projects/{project_id}/tasks", response_model=TaskResponse)
@limiter.limit("30/minute")
async def create_task_in_project(
    request: Request, project_id: int, task_data: TaskCreateRequest, user_id: int, db: Session = Depends(get_db)
):
    """Create a new task in a project."""
    # Check user is a member of the project
    if not crud.get_project_member(db, project_id, user_id):
        raise HTTPException(status_code=403, detail="Access denied")

    # Verify project_id matches
    if task_data.project_id != project_id:
        raise HTTPException(status_code=400, detail="Project ID mismatch")

    # Generate thread_id
    import uuid

    thread_id = f"task-{uuid.uuid4().hex[:12]}"

    task = crud.create_task(
        db=db,
        project_id=project_id,
        thread_id=thread_id,
        title=task_data.title,
        creator_id=user_id,
        description=task_data.description,
    )
    return task


@app.get("/projects/{project_id}/tasks/{task_id}", response_model=TaskWithAssignmentsResponse)
@limiter.limit("60/minute")
async def get_task_detail(request: Request, project_id: int, task_id: int, user_id: int, db: Session = Depends(get_db)):
    """Get task details with assignments."""
    # Check user is a member of the project
    if not crud.get_project_member(db, project_id, user_id):
        raise HTTPException(status_code=403, detail="Access denied")

    task = crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Verify task belongs to the project
    if task.project_id != project_id:
        raise HTTPException(status_code=404, detail="Task not found in this project")

    return task


@app.put("/projects/{project_id}/tasks/{task_id}", response_model=TaskResponse)
@limiter.limit("30/minute")
async def update_task_in_project(
    request: Request,
    project_id: int,
    task_id: int,
    task_data: TaskUpdateRequest,
    user_id: int,
    db: Session = Depends(get_db),
):
    """Update a task."""
    # Check user is a member of the project with appropriate role
    if not crud.check_project_permission(
        db, project_id, user_id, [models.RoleEnum.OWNER, models.RoleEnum.ADMIN, models.RoleEnum.MEMBER]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    task = crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.project_id != project_id:
        raise HTTPException(status_code=404, detail="Task not found in this project")

    status_enum = None
    if task_data.status:
        try:
            status_enum = models.TaskStatusEnum(task_data.status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")

    updated_task = crud.update_task(
        db=db,
        task_id=task_id,
        title=task_data.title,
        description=task_data.description,
        status=status_enum,
        current_draft=task_data.current_draft,
        feedback=task_data.feedback,
    )
    return updated_task


@app.delete("/projects/{project_id}/tasks/{task_id}")
@limiter.limit("30/minute")
async def delete_task_in_project(
    request: Request, project_id: int, task_id: int, user_id: int, db: Session = Depends(get_db)
):
    """Delete a task."""
    # Check user is a member of the project with appropriate role
    if not crud.check_project_permission(db, project_id, user_id, [models.RoleEnum.OWNER, models.RoleEnum.ADMIN]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    task = crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.project_id != project_id:
        raise HTTPException(status_code=404, detail="Task not found in this project")

    crud.delete_task(db, task_id)
    return {"message": "Task deleted successfully"}


# ========== User Task Queries (Role-based Filters) ==========


@app.get("/users/{user_id}/tasks/created", response_model=list[TaskResponse])
@limiter.limit("60/minute")
async def list_user_created_tasks(
    request: Request,
    user_id: int,
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """List all tasks created by a user."""
    # Verify user exists
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    status_enum = None
    if status:
        try:
            status_enum = models.TaskStatusEnum(status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")

    tasks = crud.get_user_created_tasks(db, user_id, status=status_enum, skip=skip, limit=limit)
    return tasks


@app.get("/users/{user_id}/tasks/assigned", response_model=list[TaskResponse])
@limiter.limit("60/minute")
async def list_user_assigned_tasks(
    request: Request,
    user_id: int,
    role: str | None = None,
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """List all tasks assigned to a user (via their Actor)."""
    # Verify user exists and has an actor
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.actor_id:
        return []

    role_enum = None
    if role:
        try:
            role_enum = models.AssignmentRoleEnum(role)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid assignment role")

    status_enum = None
    if status:
        try:
            status_enum = models.TaskStatusEnum(status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")

    tasks = crud.get_user_assigned_tasks(db, user.actor_id, role=role_enum, status=status_enum, skip=skip, limit=limit)
    return tasks


@app.get("/users/{user_id}/tasks/review", response_model=list[TaskResponse])
@limiter.limit("60/minute")
async def list_user_review_tasks(
    request: Request,
    user_id: int,
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """List all tasks where the user is assigned as reviewer (across all projects)."""
    status_enum = None
    if status:
        try:
            status_enum = models.TaskStatusEnum(status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")

    tasks = crud.get_tasks_by_assignment_role_across_projects(
        db, user_id, models.AssignmentRoleEnum.REVIEWER, status=status_enum, skip=skip, limit=limit
    )
    return tasks


@app.get("/users/{user_id}/tasks/approve", response_model=list[TaskResponse])
@limiter.limit("60/minute")
async def list_user_approve_tasks(
    request: Request,
    user_id: int,
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """List all tasks where the user is assigned as approver (across all projects)."""
    status_enum = None
    if status:
        try:
            status_enum = models.TaskStatusEnum(status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")

    tasks = crud.get_tasks_by_assignment_role_across_projects(
        db, user_id, models.AssignmentRoleEnum.APPROVER, status=status_enum, skip=skip, limit=limit
    )
    return tasks
