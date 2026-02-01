from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.graph import graph_app, checkpointer
from app.schema import TaskRequest, ApprovalRequest, StatusResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    await checkpointer.asetup()
    yield


app = FastAPI(title="AxonRelay API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict to frontend domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    return {"status": "ok", "service": "AxonRelay"}


@app.post("/task/start")
async def start_task(req: TaskRequest):
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
async def get_status(thread_id: str):
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
async def approve_task(req: ApprovalRequest):
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
