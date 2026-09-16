"""Thin wrapper around the LangGraph Platform SDK.

The backend delegates all graph execution to LangGraph Platform; the Postgres
side stores Draft / Approval / Task as a projection of the Platform thread
state.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from typing import Any

from langgraph_sdk import get_client

ASSISTANT_ID = os.environ.get("LANGGRAPH_ASSISTANT_ID", "axonrelay")
API_URL_ENV = "LANGGRAPH_API_URL"
API_KEY_ENV = "LANGGRAPH_API_KEY"


class PlatformNotConfiguredError(RuntimeError):
    """Raised when LANGGRAPH_API_URL is not configured."""


class AmbiguousDeliveryError(RuntimeError):
    """Platform accepted a run, but its outcome could not be reconciled safely."""

    def __init__(self, run_id: str | None, cause: Exception):
        self.run_id = run_id
        run_label = run_id or "an unobserved run"
        super().__init__(f"{run_label} may have been accepted, but delivery outcome is unknown: {cause}")


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


async def resume_thread(
    thread_id: str,
    decision_payload: dict[str, Any],
    *,
    decision_key: str,
    on_run_created: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Resume once, reconciling failures after Platform allocates a run ID.

    The local ledger's atomic delivery claim prevents concurrent sends. The
    decision key is also attached as Platform metadata for traceability. If the
    request fails after a run ID is allocated, joining that exact run is the
    only safe recovery; if reconciliation also fails, the caller must mark the
    delivery ``unknown`` rather than retry automatically.
    """
    client = _client()
    created_run: dict[str, str] = {}

    def remember_run(metadata: Any) -> None:
        run_id = metadata.get("run_id") if isinstance(metadata, dict) else getattr(metadata, "run_id", None)
        if run_id:
            created_run["run_id"] = str(run_id)
            if on_run_created:
                on_run_created(str(run_id))

    try:
        return await client.runs.wait(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID,
            command={"resume": decision_payload},
            metadata={"axonrelay_decision_key": decision_key},
            multitask_strategy="reject",
            on_run_created=remember_run,
        )
    except Exception as exc:
        run_id = created_run.get("run_id")
        if run_id:
            try:
                return await client.runs.join(thread_id, run_id)
            except Exception as reconcile_exc:
                raise AmbiguousDeliveryError(run_id, reconcile_exc) from exc
        # Once runs.wait has been invoked, absence of a callback is not proof
        # that Platform did not accept the HTTP request. Never classify this as
        # a retryable failure.
        raise AmbiguousDeliveryError(None, exc) from exc


async def join_run(thread_id: str, run_id: str) -> dict[str, Any]:
    """Read the terminal result for a previously allocated Platform run."""
    client = _client()
    return await client.runs.join(thread_id, run_id)


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
    if state_or_result.get("__interrupt__"):
        return True
    values = state_or_result.get("values") or {}
    if isinstance(values, dict) and values.get("__interrupt__"):
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


def extract_approval_episode_id(state_or_result: dict[str, Any]) -> str | None:
    """Derive a stable ID for one concrete Platform interrupt/checkpoint."""
    checkpoint = state_or_result.get("checkpoint") or {}
    checkpoint_id = checkpoint.get("checkpoint_id") or state_or_result.get("checkpoint_id")
    if checkpoint_id:
        return f"checkpoint:{checkpoint_id}"
    values = state_or_result.get("values") or {}
    direct_interrupts = state_or_result.get("__interrupt__") or (
        values.get("__interrupt__") if isinstance(values, dict) else None
    )
    if direct_interrupts:
        canonical = json.dumps(direct_interrupts, sort_keys=True, separators=(",", ":"), default=str)
        return f"interrupt:{hashlib.sha256(canonical.encode()).hexdigest()}"
    tasks = state_or_result.get("tasks") or []
    interrupt_shape = [
        {"id": task.get("id"), "interrupts": task.get("interrupts")}
        for task in tasks
        if isinstance(task, dict) and task.get("interrupts")
    ]
    if not interrupt_shape:
        return None
    canonical = json.dumps(interrupt_shape, sort_keys=True, separators=(",", ":"), default=str)
    return f"interrupt:{hashlib.sha256(canonical.encode()).hexdigest()}"
