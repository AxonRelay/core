"""The browser capture REST write is disabled-by-default and token gated."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app import crud, main, models
from app.database import Base, get_db


def _request(authorization: str | None = None) -> Request:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers})


def test_capture_is_disabled_when_no_token_is_configured(monkeypatch):
    monkeypatch.delenv("AXONRELAY_CAPTURE_TOKEN", raising=False)

    with pytest.raises(HTTPException) as exc:
        main._require_capture_token(_request())

    assert exc.value.status_code == 503


def test_capture_rejects_the_wrong_token(monkeypatch):
    monkeypatch.setenv("AXONRELAY_CAPTURE_TOKEN", "correct-token")

    with pytest.raises(HTTPException) as exc:
        main._require_capture_token(_request("Bearer wrong-token"))

    assert exc.value.status_code == 401


def test_capture_accepts_the_configured_bearer_token(monkeypatch):
    monkeypatch.setenv("AXONRELAY_CAPTURE_TOKEN", "correct-token")

    assert main._require_capture_token(_request("Bearer correct-token")) is None


@pytest.fixture
def api_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'evidence-api.sqlite3'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    def override_db():
        yield db

    main.app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(main.app) as test_client:
            yield test_client, db
    finally:
        main.app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_evidence_endpoints_reject_unauthenticated_reads(api_client, monkeypatch):
    client, db = api_client
    monkeypatch.setenv("AXONRELAY_CAPTURE_TOKEN", "correct-token")
    task = crud.create_task(db, thread_id="private-evidence", title="Private evidence")

    assert client.get(f"/tasks/{task.id}/evidence-clips").status_code == 401
    assert client.get(f"/tasks/{task.id}/context-pack").status_code == 401
    approval_history = client.get(f"/tasks/{task.id}/approvals")
    assert approval_history.status_code == 200
    assert approval_history.json() == []


def test_authenticated_capture_sanitizes_url_and_can_be_retrieved(api_client, monkeypatch):
    client, db = api_client
    monkeypatch.setenv("AXONRELAY_CAPTURE_TOKEN", "correct-token")
    self_actor = models.Actor(type=models.ActorTypeEnum.HUMAN, name="self")
    db.add(self_actor)
    db.commit()
    db.refresh(self_actor)
    task = crud.create_task(
        db,
        thread_id="authenticated-evidence",
        title="Authenticated evidence",
        creator_actor_id=self_actor.id,
    )
    headers = {"Authorization": "Bearer correct-token"}

    response = client.post(
        f"/tasks/{task.id}/evidence-clips",
        headers=headers,
        json={
            "source_url": "https://user:password@example.test/path?token=secret&view=full#fragment",
            "source_title": "A selected source",
            "source_type": "personal",
            "quote": "Exact operator-selected text.",
        },
    )

    assert response.status_code == 200
    assert response.json()["source_url"] == "https://example.test/path?token=%5BREDACTED%5D&view=full"
    listed = client.get(f"/tasks/{task.id}/evidence-clips", headers=headers)
    assert listed.status_code == 200
    assert [item["evidence_ref"] for item in listed.json()] == [response.json()["evidence_ref"]]


def test_approval_stops_before_ledger_write_when_an_evidence_reference_is_missing(api_client):
    client, db = api_client
    task = models.Task(
        thread_id="approval-evidence-check",
        title="Approval evidence check",
        status=models.TaskStatusEnum.WAITING_APPROVAL,
        current_draft="This unsupported claim cites [E-9999].",
        approval_episode_id="episode:missing-evidence",
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    response = client.post(
        f"/tasks/{task.id}/approve",
        json={"approval_episode_id": task.approval_episode_id, "comment": "ship"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Draft cites unavailable Evidence IDs: E-9999"
    assert db.query(models.Approval).count() == 0


def test_failed_delivery_is_visible_and_retry_is_idempotent(api_client, monkeypatch):
    client, db = api_client
    task = models.Task(
        thread_id="approval-delivery-outbox",
        title="Approval delivery outbox",
        status=models.TaskStatusEnum.WAITING_APPROVAL,
        current_draft="Exact approved bytes.",
        approval_episode_id="episode:offline",
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    async def unavailable(*_args, **_kwargs):
        raise main.langgraph_client.PlatformNotConfiguredError("offline")

    monkeypatch.setattr(main.langgraph_client, "resume_thread", unavailable)
    payload = {"approval_episode_id": task.approval_episode_id, "comment": "ship"}
    first = client.post(f"/tasks/{task.id}/approve", json=payload)
    second = client.post(f"/tasks/{task.id}/approve", json=payload)

    assert first.status_code == second.status_code == 503
    approvals = db.query(models.Approval).all()
    assert len(approvals) == 1
    assert approvals[0].approved_content == "Exact approved bytes."
    assert approvals[0].delivery_status == "failed"
    assert approvals[0].delivery_error == "offline"
    public_history = client.get(f"/tasks/{task.id}/approvals").json()
    assert public_history[0]["action"] == "approved"
    assert "approved_content" not in public_history[0]
    assert "evidence_manifest" not in public_history[0]
    assert "delivery_error" not in public_history[0]


def test_ambiguous_pre_callback_failure_is_not_retried(api_client, monkeypatch):
    client, db = api_client
    task = models.Task(
        thread_id="approval-ambiguous-no-run",
        title="Ambiguous delivery",
        status=models.TaskStatusEnum.WAITING_APPROVAL,
        current_draft="Exact approved bytes.",
        approval_episode_id="episode:ambiguous",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    calls = 0

    async def ambiguous(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise main.langgraph_client.AmbiguousDeliveryError(None, OSError("connection dropped"))

    monkeypatch.setattr(main.langgraph_client, "resume_thread", ambiguous)
    payload = {"approval_episode_id": task.approval_episode_id, "comment": "ship"}
    first = client.post(f"/tasks/{task.id}/approve", json=payload)
    second = client.post(f"/tasks/{task.id}/approve", json=payload)

    assert first.status_code == 502
    assert second.status_code == 409
    assert calls == 1
    approval = db.query(models.Approval).one()
    assert approval.delivery_status == "unknown"
    assert approval.delivery_run_id is None

    resolved = client.post(
        f"/approvals/{approval.id}/delivery/resolve",
        json={"outcome": "confirmed_not_delivered"},
    )
    assert resolved.status_code == 200
    assert "delivery_error" not in resolved.json()
    db.refresh(approval)
    assert approval.delivery_status == "failed"


def test_unknown_delivery_with_run_id_is_reconciled_without_resend(api_client, monkeypatch):
    client, db = api_client
    task = models.Task(
        thread_id="approval-ambiguous-known-run",
        title="Reconcile delivery",
        status=models.TaskStatusEnum.WAITING_APPROVAL,
        current_draft="Exact approved bytes.",
        approval_episode_id="episode:known-run",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    resume_calls = 0

    async def ambiguous(*_args, **kwargs):
        nonlocal resume_calls
        resume_calls += 1
        kwargs["on_run_created"]("run-42")
        raise main.langgraph_client.AmbiguousDeliveryError("run-42", TimeoutError("join timed out"))

    async def joined(_thread_id, run_id):
        assert run_id == "run-42"
        return {"final_output": "Exact approved bytes."}

    async def state(_thread_id):
        return {"values": {"drafts": ["Exact approved bytes."], "final_output": "Exact approved bytes."}}

    monkeypatch.setattr(main.langgraph_client, "resume_thread", ambiguous)
    monkeypatch.setattr(main.langgraph_client, "join_run", joined)
    monkeypatch.setattr(main.langgraph_client, "get_state", state)

    payload = {"approval_episode_id": task.approval_episode_id, "comment": "ship"}
    first = client.post(f"/tasks/{task.id}/approve", json=payload)
    second = client.post(f"/tasks/{task.id}/approve", json=payload)

    assert first.status_code == 502
    assert second.status_code == 200
    assert "approved_content" not in second.json()
    assert "evidence_manifest" not in second.json()
    assert "delivery_run_id" not in second.json()
    assert resume_calls == 1
    db.refresh(task)
    approval = db.query(models.Approval).one()
    assert approval.delivery_status == "delivered"
    assert approval.delivery_run_id == "run-42"
    assert task.status == models.TaskStatusEnum.COMPLETED


def test_delivered_decision_repairs_projection_on_retry(api_client, monkeypatch):
    client, db = api_client
    task = models.Task(
        thread_id="approval-projection-repair",
        title="Repair projection",
        status=models.TaskStatusEnum.WAITING_APPROVAL,
        current_draft="Exact approved bytes.",
        approval_episode_id="episode:projection",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    resume_calls = 0
    state_calls = 0

    async def delivered(*_args, **kwargs):
        nonlocal resume_calls
        resume_calls += 1
        kwargs["on_run_created"]("run-99")
        return {"final_output": "Exact approved bytes."}

    async def state(_thread_id):
        nonlocal state_calls
        state_calls += 1
        if state_calls == 1:
            raise OSError("state endpoint unavailable")
        return {"values": {"drafts": ["Exact approved bytes."], "final_output": "Exact approved bytes."}}

    monkeypatch.setattr(main.langgraph_client, "resume_thread", delivered)
    monkeypatch.setattr(main.langgraph_client, "get_state", state)

    payload = {"approval_episode_id": task.approval_episode_id, "comment": "ship"}
    first = client.post(f"/tasks/{task.id}/approve", json=payload)
    second = client.post(f"/tasks/{task.id}/approve", json=payload)

    assert first.status_code == 502
    assert second.status_code == 200
    assert resume_calls == 1
    db.refresh(task)
    assert db.query(models.Approval).one().delivery_status == "delivered"
    assert task.status == models.TaskStatusEnum.COMPLETED


def test_retry_of_delivered_rejection_cannot_reject_the_next_episode(api_client, monkeypatch):
    client, db = api_client
    task = models.Task(
        thread_id="rejection-response-loss",
        title="Do not reject unseen revision",
        status=models.TaskStatusEnum.WAITING_APPROVAL,
        current_draft="Draft reviewed by the operator.",
        approval_episode_id="checkpoint:reviewed",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    resume_calls = 0

    async def delivered(*_args, **kwargs):
        nonlocal resume_calls
        resume_calls += 1
        kwargs["on_run_created"]("run-reject")
        return {"drafts": ["Draft reviewed by the operator.", "Unseen revision."]}

    async def state(_thread_id):
        return {
            "values": {"drafts": ["Draft reviewed by the operator.", "Unseen revision."]},
            "next": ["human_approval"],
            "checkpoint": {"checkpoint_id": "next-episode"},
        }

    monkeypatch.setattr(main.langgraph_client, "resume_thread", delivered)
    monkeypatch.setattr(main.langgraph_client, "get_state", state)
    stale_payload = {"approval_episode_id": "checkpoint:reviewed", "reason": "revise"}

    first = client.post(f"/tasks/{task.id}/reject", json=stale_payload)
    retry_after_lost_response = client.post(f"/tasks/{task.id}/reject", json=stale_payload)

    assert first.status_code == 200
    assert retry_after_lost_response.status_code == 409
    assert resume_calls == 1
    db.refresh(task)
    assert task.approval_episode_id == "checkpoint:next-episode"
    assert db.query(models.Approval).count() == 1
