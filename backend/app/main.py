from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.graph import graph_app, checkpointer
from app.schema import TaskRequest, ApprovalRequest, StatusResponse

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
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/")
def health():
    return {"status": "ok", "service": "AxonRelay"}


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
