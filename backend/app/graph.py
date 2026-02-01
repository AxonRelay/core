import os

from langgraph.graph import StateGraph
from langgraph.checkpoint.redis import AsyncRedisSaver
from redis.asyncio import Redis

from app.schema import AgentState

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")


async def writer_node(state: AgentState):
    """AI generates a draft based on the task."""
    # TODO: replace with actual LLM call
    draft = f"[AxonRelay Draft] Task: {state['task']}"
    return {"draft": draft, "status": "waiting_approval"}


async def approval_node(state: AgentState):
    """Runs after human approval — finalizes the task."""
    return {"status": "completed"}


builder = StateGraph(AgentState)
builder.add_node("writer", writer_node)
builder.add_node("publisher", approval_node)
builder.set_entry_point("writer")
builder.add_edge("writer", "publisher")

redis_client = Redis.from_url(REDIS_URL)
checkpointer = AsyncRedisSaver(conn=redis_client)

graph_app = builder.compile(
    checkpointer=checkpointer,
    interrupt_before=["publisher"],
)
