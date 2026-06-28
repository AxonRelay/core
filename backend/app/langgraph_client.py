"""Thin wrapper around the LangGraph Platform SDK.

The backend delegates all graph execution to LangGraph Platform; the Postgres
side stores Draft / Approval / Task as a projection of the Platform thread
state.
"""

from __future__ import annotations

import os
from typing import Any

from langgraph_sdk import get_client

ASSISTANT_ID = os.environ.get("LANGGRAPH_ASSISTANT_ID", "axonrelay")
API_URL_ENV = "LANGGRAPH_API_URL"
API_KEY_ENV = "LANGGRAPH_API_KEY"


class PlatformNotConfiguredError(RuntimeError):
    """Raised when LANGGRAPH_API_URL is not configured."""


def _client():
    api_url = os.environ.get(API_URL_ENV)
    if not api_url:
        raise PlatformNotConfiguredError(f"{API_URL_ENV} is not set. Configure the LangGraph Platform endpoint.")
    api_key = os.environ.get(API_KEY_ENV)
    return get_client(url=api_url, api_key=api_key)


async def create_thread(metadata: dict[str, Any] | None = None) -> str:
    """Allocate a new thread on Platform and return its thread_id."""
    client = _client()
    thread = await client.threads.create(metadata=metadata or {})
    return thread["thread_id"]


async def run_until_interrupt(thread_id: str, input_state: dict[str, Any]) -> dict[str, Any]:
    """Run the graph synchronously until it interrupts or reaches END.

    Returns the final thread state values (the dict the graph compiled from
    AgentState).
    """
    client = _client()
    result = await client.runs.wait(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        input=input_state,
    )
    return result


async def resume_thread(thread_id: str, decision_payload: dict[str, Any]) -> dict[str, Any]:
    """Resume an interrupted thread with the human's decision payload."""
    client = _client()
    result = await client.runs.wait(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        command={"resume": decision_payload},
    )
    return result


async def get_state(thread_id: str) -> dict[str, Any]:
    """Fetch the current thread state snapshot (values + next nodes)."""
    client = _client()
    return await client.threads.get_state(thread_id)


def is_waiting_for_human(state_or_result: dict[str, Any]) -> bool:
    """Heuristic: a thread is waiting for human if its next nodes include
    `human_approval` or it has unresolved interrupts.

    Works for both runs.wait() return values and threads.get_state() snapshots.
    """
    next_nodes = state_or_result.get("next") or []
    if "human_approval" in next_nodes:
        return True
    interrupts = state_or_result.get("tasks") or []
    return any(isinstance(task, dict) and task.get("interrupts") for task in interrupts)


def extract_values(state_or_result: dict[str, Any]) -> dict[str, Any]:
    """Extract the AgentState values from either a runs.wait() result or a
    threads.get_state() snapshot."""
    if "values" in state_or_result:
        return state_or_result["values"] or {}
    # runs.wait may already return the values dict directly.
    return state_or_result
