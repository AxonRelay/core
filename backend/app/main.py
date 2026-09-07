"""AxonRelay HTTP API (personal PoC, post-pivot).

Thin REST layer over the SQLAlchemy service layer. Read-heavy by design — write
operations (create_task, approve_task, reject_task) are also exposed via the
MCP server (Phase 2.3) which is the primary interface from the IDE.

User / Project / OAuth endpoints have been removed in this pivot. A single
human Actor (name="self") represents the operator.
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.orm import Session

from app import coordination, crud, langgraph_client, models, safe_envelope, service
from app.database import get_db
from app.mcp.serializers import claim_to_dict, session_to_dict
from app.ratelimit import client_key
from app.schema import (
    ActorResponse,
    AgentDefinitionCreateRequest,
    AgentDefinitionResponse,
    AgentDefinitionUpdateRequest,
    ApprovalResponse,
    ApproveRequest,
    DraftResponse,
    RejectRequest,
    TaskAssignmentCreateRequest,
    TaskAssignmentResponse,
    TaskCreateRequest,
    TaskResponse,
    TaskUpdateRequest,
    TaskWithAssignmentsResponse,
)

limiter = Limiter(key_func=client_key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="AxonRelay API", lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.axonrelay.com",
        "https://axonrelay.com",
        "http://localhost",
        "http://localhost:3000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def _value_free_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 without the rejected input.

    FastAPI's default body echoes `input` (the submitted value) and `ctx` for
    every error. A rejected value may be exactly what a caller must not have
    sent to a shared instance, so the body names the location and error type
    and nothing else. Same policy as the Safe Envelope service.
    """
    detail = [{"loc": list(err.get("loc", ())), "type": err.get("type", "value_error")} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(safe_envelope.SafeModeRefused)
async def _safe_mode_refused(request: Request, exc: safe_envelope.SafeModeRefused) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": safe_envelope.SAFE_MODE_REFUSAL})


def _free_text_surface() -> None:
    """Dependency for every endpoint that accepts arbitrary text (see app/safe_envelope.py)."""
    safe_envelope.refuse_free_text_if_safe_mode()


@app.get("/")
def health():
    return {"status": "ok", "service": "AxonRelay", "safe_mode": safe_envelope.safe_mode()}


# ========== Actor Endpoints ==========


@app.get("/actors", response_model=list[ActorResponse])
@limiter.limit("60/minute")
async def list_actors(request: Request, type: str | None = None, db: Session = Depends(get_db)):
    actor_type = None
    if type:
        try:
            actor_type = models.ActorTypeEnum(type)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid actor type. Must be 'human' or 'ai'") from e
    return crud.get_actors(db, actor_type=actor_type)


@app.get("/actors/{actor_id}", response_model=ActorResponse)
@limiter.limit("60/minute")
async def get_actor(request: Request, actor_id: int, db: Session = Depends(get_db)):
    actor = crud.get_actor(db, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="Actor not found")
    return actor


@app.get("/actors/me", response_model=ActorResponse)
@limiter.limit("60/minute")
async def get_self(request: Request, db: Session = Depends(get_db)):
    """Get the single human actor representing the operator."""
    actor = crud.get_self_actor(db)
    if not actor:
        raise HTTPException(status_code=404, detail="Self actor not seeded")
    return actor


# ========== Agent Definition Endpoints ==========


@app.get("/agents", response_model=list[AgentDefinitionResponse])
@limiter.limit("60/minute")
async def list_agents(
    request: Request,
    agent_type: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
):
    agent_type_enum = None
    if agent_type:
        try:
            agent_type_enum = models.AgentTypeEnum(agent_type)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid agent type") from e
    return crud.get_agent_definitions(db, agent_type=agent_type_enum, is_active=is_active)


@app.post("/agents", response_model=AgentDefinitionResponse)
@limiter.limit("30/minute")
async def create_agent(
    request: Request,
    agent_data: AgentDefinitionCreateRequest,
    db: Session = Depends(get_db),
    _guard: None = Depends(_free_text_surface),
):
    try:
        agent_type = models.AgentTypeEnum(agent_data.agent_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid agent type") from e
    return crud.create_agent_definition(
        db=db,
        name=agent_data.name,
        agent_type=agent_type,
        description=agent_data.description,
        config=agent_data.config,
    )


@app.get("/agents/{agent_id}", response_model=AgentDefinitionResponse)
@limiter.limit("60/minute")
async def get_agent(request: Request, agent_id: int, db: Session = Depends(get_db)):
    agent = crud.get_agent_definition(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@app.put("/agents/{agent_id}", response_model=AgentDefinitionResponse)
@limiter.limit("30/minute")
async def update_agent(
    request: Request,
    agent_id: int,
    agent_data: AgentDefinitionUpdateRequest,
    db: Session = Depends(get_db),
    _guard: None = Depends(_free_text_surface),
):
    agent_type = None
    if agent_data.agent_type:
        try:
            agent_type = models.AgentTypeEnum(agent_data.agent_type)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid agent type") from e

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
    if not crud.delete_agent_definition(db, agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"message": "Agent deleted successfully"}


# ========== Task Endpoints ==========


@app.get("/tasks", response_model=list[TaskResponse])
@limiter.limit("60/minute")
async def list_tasks_endpoint(
    request: Request,
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    status_enum = None
    if status:
        try:
            status_enum = models.TaskStatusEnum(status)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid status") from e
    return crud.list_tasks(db, status=status_enum, skip=skip, limit=limit)


@app.post("/tasks", response_model=TaskWithAssignmentsResponse)
@limiter.limit("30/minute")
async def create_task_endpoint(
    request: Request,
    task_data: TaskCreateRequest,
    db: Session = Depends(get_db),
    _guard: None = Depends(_free_text_surface),
):
    self_actor = crud.get_self_actor(db)
    creator_actor_id = self_actor.id if self_actor else None

    try:
        thread_id = await langgraph_client.create_thread(
            metadata={"title": task_data.title, "creator_actor_id": creator_actor_id}
        )
    except langgraph_client.PlatformNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    task = crud.create_task(
        db=db,
        thread_id=thread_id,
        title=task_data.title,
        creator_actor_id=creator_actor_id,
        description=task_data.description,
    )

    if task_data.assignments:
        for assignment in task_data.assignments:
            try:
                role = models.AssignmentRoleEnum(assignment.role)
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Invalid assignment role") from e
            actor = crud.get_actor(db, assignment.actor_id)
            if not actor:
                raise HTTPException(status_code=404, detail=f"Actor {assignment.actor_id} not found")
            crud.create_task_assignment(db=db, task_id=task.id, actor_id=assignment.actor_id, role=role)

    db.refresh(task)
    return task


@app.get("/tasks/{task_id}", response_model=TaskWithAssignmentsResponse)
@limiter.limit("60/minute")
async def get_task_endpoint(request: Request, task_id: int, db: Session = Depends(get_db)):
    task = crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.put("/tasks/{task_id}", response_model=TaskResponse)
@limiter.limit("30/minute")
async def update_task_endpoint(
    request: Request,
    task_id: int,
    task_data: TaskUpdateRequest,
    db: Session = Depends(get_db),
    _guard: None = Depends(_free_text_surface),
):
    status_enum = None
    if task_data.status:
        try:
            status_enum = models.TaskStatusEnum(task_data.status)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid status") from e

    task = crud.update_task(
        db=db,
        task_id=task_id,
        title=task_data.title,
        description=task_data.description,
        status=status_enum,
        current_draft=task_data.current_draft,
        feedback=task_data.feedback,
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.delete("/tasks/{task_id}")
@limiter.limit("30/minute")
async def delete_task_endpoint(request: Request, task_id: int, db: Session = Depends(get_db)):
    if not crud.delete_task(db, task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"message": "Task deleted successfully"}


# ========== Platform-driven Run / State Sync ==========


def _sync_state_to_db(db: Session, task: models.Task, values: dict, waiting_for_human: bool) -> None:
    """Project Platform thread state into the Postgres ledger.

    Thin wrapper over the shared service so the HTTP and MCP paths cannot drift.
    """
    service.project_run_state(db, task, values, waiting_for_human)


@app.post("/tasks/{task_id}/run", response_model=TaskWithAssignmentsResponse)
@limiter.limit("10/minute")
async def run_task_endpoint(
    request: Request, task_id: int, db: Session = Depends(get_db), _guard: None = Depends(_free_text_surface)
):
    """Kick off graph execution on LangGraph Platform. Blocks until the next
    interrupt or completion, then syncs state back to Postgres."""
    task = crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    initial_state = {
        "task_id": task.id,
        "title": task.title,
        "description": task.description,
        "drafts": [],
        "reviewer_comments": [],
        "iteration": 0,
    }

    try:
        result = await langgraph_client.run_until_interrupt(task.thread_id, initial_state)
    except langgraph_client.PlatformNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    values = langgraph_client.extract_values(result)
    waiting = langgraph_client.is_waiting_for_human(result)
    _sync_state_to_db(db, task, values, waiting)
    db.refresh(task)
    return task


# ========== Pending Approvals (the unified inbox) ==========


@app.get("/tasks/pending/approvals", response_model=list[TaskResponse])
@limiter.limit("60/minute")
async def list_pending_approvals_endpoint(
    request: Request, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)
):
    """Unified pending approval inbox for the operator (single human actor)."""
    self_actor = crud.get_self_actor(db)
    if not self_actor:
        return []
    return crud.list_pending_approvals(db, actor_id=self_actor.id, skip=skip, limit=limit)


# ========== Approve / Reject ==========


def _record_decision(db: Session, **kwargs) -> models.Approval:
    """`crud.record_approval` with the ledger's contract errors mapped to HTTP.

    409 for a stale target or a task with no artifact (the request was well
    formed; the state it assumed is gone), 404 for a vanished task. The error
    text names versions only, never content.
    """
    try:
        return crud.record_approval(db, **kwargs)
    except crud.TaskNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (crud.StaleArtifactError, crud.ArtifactRequiredError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except crud.LedgerError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/tasks/{task_id}/approve", response_model=ApprovalResponse)
@limiter.limit("30/minute")
async def approve_task_endpoint(
    request: Request,
    task_id: int,
    approve_data: ApproveRequest,
    db: Session = Depends(get_db),
    _guard: None = Depends(_free_text_surface),
):
    task = crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != models.TaskStatusEnum.WAITING_APPROVAL:
        raise HTTPException(status_code=400, detail="Task is not waiting for approval")

    self_actor = crud.get_self_actor(db)
    reviewer_actor_id = self_actor.id if self_actor else None

    # The ledger entry (and, if the operator edited the draft, the new draft
    # version it binds to) is written before the graph resumes: the approval
    # names the artifact, so the artifact has to exist first.
    approval = _record_decision(
        db,
        task_id=task_id,
        reviewer_actor_id=reviewer_actor_id,
        action="approved",
        comment=approve_data.comment,
        artifact_version=approve_data.artifact_version,
        expected_commitment=approve_data.expected_commitment,
        modified_draft=approve_data.modified_draft,
    )

    try:
        result = await langgraph_client.resume_thread(
            task.thread_id,
            {
                "decision": "approved",
                "human_comment": approve_data.comment,
                "modified_draft": approve_data.modified_draft,
            },
        )
    except langgraph_client.PlatformNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    values = langgraph_client.extract_values(result)
    waiting = langgraph_client.is_waiting_for_human(result)
    _sync_state_to_db(db, task, values, waiting)
    return approval


@app.post("/tasks/{task_id}/reject", response_model=ApprovalResponse)
@limiter.limit("30/minute")
async def reject_task_endpoint(
    request: Request,
    task_id: int,
    reject_data: RejectRequest,
    db: Session = Depends(get_db),
    _guard: None = Depends(_free_text_surface),
):
    task = crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != models.TaskStatusEnum.WAITING_APPROVAL:
        raise HTTPException(status_code=400, detail="Task is not waiting for approval")

    self_actor = crud.get_self_actor(db)
    reviewer_actor_id = self_actor.id if self_actor else None

    comment_parts = [reject_data.comment, reject_data.reason]
    combined_comment = " | ".join(p for p in comment_parts if p) or None

    approval = _record_decision(
        db,
        task_id=task_id,
        reviewer_actor_id=reviewer_actor_id,
        action="rejected",
        comment=combined_comment,
        artifact_version=reject_data.artifact_version,
        expected_commitment=reject_data.expected_commitment,
    )

    try:
        result = await langgraph_client.resume_thread(
            task.thread_id,
            {
                "decision": "rejected",
                "human_comment": combined_comment,
            },
        )
    except langgraph_client.PlatformNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    values = langgraph_client.extract_values(result)
    waiting = langgraph_client.is_waiting_for_human(result)
    _sync_state_to_db(db, task, values, waiting)
    return approval


# ========== Drafts ==========


@app.get("/tasks/{task_id}/drafts", response_model=list[DraftResponse])
@limiter.limit("60/minute")
async def get_drafts_endpoint(request: Request, task_id: int, db: Session = Depends(get_db)):
    task = crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return crud.get_drafts(db, task_id)


@app.get("/tasks/{task_id}/approvals", response_model=list[ApprovalResponse])
@limiter.limit("60/minute")
async def get_approvals_endpoint(request: Request, task_id: int, db: Session = Depends(get_db)):
    """Approval / rejection history for a task (read-only, for the dashboard)."""
    task = crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return crud.get_approvals(db, task_id)


# ========== Ledger Integrity ==========


@app.get("/tasks/{task_id}/ledger/verify")
@limiter.limit("60/minute")
async def verify_ledger_endpoint(request: Request, task_id: int, db: Session = Depends(get_db)):
    """Verify the tamper-evident approval hash chain for a task."""
    task = crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return crud.verify_approval_chain(db, task_id)


# ========== Task Assignments ==========


@app.get("/tasks/{task_id}/assignments", response_model=list[TaskAssignmentResponse])
@limiter.limit("60/minute")
async def list_task_assignments_endpoint(request: Request, task_id: int, db: Session = Depends(get_db)):
    return crud.get_task_assignments(db, task_id)


@app.post("/tasks/{task_id}/assignments", response_model=TaskAssignmentResponse)
@limiter.limit("30/minute")
async def create_task_assignment_endpoint(
    request: Request,
    task_id: int,
    assignment_data: TaskAssignmentCreateRequest,
    db: Session = Depends(get_db),
):
    if not crud.get_task(db, task_id):
        raise HTTPException(status_code=404, detail="Task not found")

    actor = crud.get_actor(db, assignment_data.actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="Actor not found")

    try:
        role = models.AssignmentRoleEnum(assignment_data.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid assignment role") from e

    return crud.create_task_assignment(db=db, task_id=task_id, actor_id=assignment_data.actor_id, role=role)


@app.delete("/tasks/{task_id}/assignments/{actor_id}")
@limiter.limit("30/minute")
async def remove_task_assignment_endpoint(
    request: Request,
    task_id: int,
    actor_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
):
    if not crud.delete_task_assignment_by_actor(db, task_id, actor_id):
        raise HTTPException(status_code=404, detail="Assignment not found")
    return {"message": "Assignment removed successfully"}


# ========== Safe Envelope (content-blind ingestion) ==========


@app.post("/envelopes", status_code=201)
@limiter.limit("60/minute")
async def ingest_envelope_endpoint(request: Request, db: Session = Depends(get_db)):
    """Ingest one metadata-only Safe Envelope (schema: docs/schemas/safe-envelope-v1.json).

    The body is read as raw JSON and validated by the shared service, not by a
    request model: a schema failure must name fields only, and this path must
    behave identically to the MCP tool. 422 lists the offending field names;
    409 is never used because re-sending an event_id is idempotent (200).
    """
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=422, detail={"reason": "not JSON", "fields": ["(envelope)"]}) from None
    try:
        event, created = safe_envelope.ingest(db, payload)
    except safe_envelope.EnvelopeRejected as e:
        raise HTTPException(status_code=422, detail={"reason": e.reason, "fields": e.fields}) from None
    body = safe_envelope.event_to_dict(event)
    body["created"] = created
    return JSONResponse(status_code=201 if created else 200, content=body)


@app.get("/envelopes")
@limiter.limit("60/minute")
async def list_envelopes_endpoint(
    request: Request, limit: int = 100, action: str | None = None, db: Session = Depends(get_db)
):
    """Stored Safe Envelopes, newest first. Every field was allowlisted on the way in."""
    try:
        events = safe_envelope.list_events(db, limit=limit, action=action)
    except safe_envelope.EnvelopeRejected as e:
        raise HTTPException(status_code=400, detail={"reason": e.reason, "fields": e.fields}) from None
    return [safe_envelope.event_to_dict(e) for e in events]


# ========== Coordination Board (read-only; writes go through MCP) ==========
#
# The board is how a human sees what the fleet of agents is doing. Agents drive
# it through the MCP tools (register_session / claim_territory / send_relay);
# these endpoints exist so the dashboard - and a person with curl - can read the
# same picture without an MCP client.


@app.get("/coordination/board")
@limiter.limit("60/minute")
async def coordination_board_endpoint(request: Request, repo: str | None = None, db: Session = Depends(get_db)):
    """Active sessions, live territory claims, and unacknowledged relays."""
    return coordination.board(db, repo=repo)


@app.get("/coordination/sessions")
@limiter.limit("60/minute")
async def coordination_sessions_endpoint(
    request: Request,
    repo: str | None = None,
    include_ended: bool = False,
    db: Session = Depends(get_db),
):
    """Who is working where, most recently active first."""
    sessions = coordination.list_sessions(db, repo=repo, include_ended=include_ended)
    return [{**session_to_dict(s), "stale": coordination.is_stale(s)} for s in sessions]


@app.get("/coordination/claims")
@limiter.limit("60/minute")
async def coordination_claims_endpoint(request: Request, repo: str | None = None, db: Session = Depends(get_db)):
    """Territory claims currently in force (held and not yet expired)."""
    return [
        {**claim_to_dict(c), "holder": coordination.describe_holder(c.session)}
        for c in coordination.live_claims(db, repo=repo)
    ]


@app.get("/coordination/git/guard")
@limiter.limit("120/minute")
async def coordination_git_guard_endpoint(
    request: Request,
    resource: str,
    session_id: int | None = None,
    host: str | None = None,
    clone_path: str | None = None,
    repo: str | None = None,
    db: Session = Depends(get_db),
):
    """Whether a git resource is free — the endpoint `tools/gitsafe` polls.

    REST rather than MCP because the caller is a shell wrapper sitting in front
    of `git`; speaking JSON-RPC over SSE from a shell script is not reasonable.
    It stays read-only, so the "writes go through MCP" rule is intact.

    A caller with no `session_id` is an *unregistered* actor in the clone — a
    subagent that spawned inside someone's working directory, typically. It is
    identified by (host, clone_path) instead, and is refused whenever anyone
    holds the resource, since it cannot have been the one to claim it.

    A caller *with* a `session_id` should still send `host` and `clone_path`:
    they are where the git command actually runs, and if that is not the
    session's own checkout the caller is judged as unregistered there.
    """
    try:
        resolved = models.ClaimResourceEnum(resource)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Unknown resource") from e

    if session_id is not None:
        try:
            return coordination.guard_git_operation(
                db, session_id=session_id, resource=resolved, host=host, clone_path=clone_path, repo=repo
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    if not (host and clone_path):
        raise HTTPException(status_code=400, detail="Pass session_id, or both host and clone_path")
    return coordination.guard_unregistered_caller(db, host=host, clone_path=clone_path, resource=resolved, repo=repo)


@app.get("/coordination/sessions/{session_id}/inbox")
@limiter.limit("60/minute")
async def coordination_inbox_endpoint(
    request: Request,
    session_id: int = Path(..., gt=0),
    include_acked: bool = False,
    db: Session = Depends(get_db),
):
    """A session's relay inbox. Reading here marks the relays read, as MCP does."""
    try:
        return coordination.read_inbox(db, session_id, include_acked=include_acked)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
