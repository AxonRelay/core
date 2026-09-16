"""Delivery reconciliation around LangGraph Platform run creation."""

import asyncio

import pytest

from app import langgraph_client


class _Runs:
    def __init__(self, *, join_result=None, join_error: Exception | None = None, create_run: bool = True):
        self.join_result = join_result
        self.join_error = join_error
        self.create_run = create_run
        self.wait_kwargs = None
        self.join_args = None

    async def wait(self, **kwargs):
        self.wait_kwargs = kwargs
        if self.create_run:
            kwargs["on_run_created"]({"run_id": "run-123"})
        raise OSError("connection dropped")

    async def join(self, thread_id, run_id):
        self.join_args = (thread_id, run_id)
        if self.join_error:
            raise self.join_error
        return self.join_result


class _Client:
    def __init__(self, runs):
        self.runs = runs


def test_resume_reconciles_the_exact_created_run(monkeypatch):
    runs = _Runs(join_result={"values": {"status": "approved"}})
    monkeypatch.setattr(langgraph_client, "_client", lambda: _Client(runs))
    observed_run_ids = []

    result = asyncio.run(
        langgraph_client.resume_thread(
            "thread-1",
            {"decision": "approved"},
            decision_key="decision-1",
            on_run_created=observed_run_ids.append,
        )
    )

    assert result == {"values": {"status": "approved"}}
    assert runs.join_args == ("thread-1", "run-123")
    assert runs.wait_kwargs["metadata"] == {"axonrelay_decision_key": "decision-1"}
    assert runs.wait_kwargs["multitask_strategy"] == "reject"
    assert observed_run_ids == ["run-123"]


def test_resume_reports_unknown_when_created_run_cannot_be_reconciled(monkeypatch):
    runs = _Runs(join_error=TimeoutError("join timed out"))
    monkeypatch.setattr(langgraph_client, "_client", lambda: _Client(runs))

    with pytest.raises(langgraph_client.AmbiguousDeliveryError) as error:
        asyncio.run(langgraph_client.resume_thread("thread-1", {"decision": "approved"}, decision_key="decision-1"))

    assert error.value.run_id == "run-123"


def test_resume_without_a_creation_callback_is_still_ambiguous(monkeypatch):
    runs = _Runs(create_run=False)
    monkeypatch.setattr(langgraph_client, "_client", lambda: _Client(runs))

    with pytest.raises(langgraph_client.AmbiguousDeliveryError) as error:
        asyncio.run(langgraph_client.resume_thread("thread-1", {"decision": "approved"}, decision_key="decision-1"))
    assert error.value.run_id is None
    assert runs.join_args is None
