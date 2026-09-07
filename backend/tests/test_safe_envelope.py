"""Content-blind Safe Envelope ingestion (issue #25, app/safe_envelope.py).

Three promises, each with its own tests:

1. **The envelope admits metadata only.** Every field is a bounded identifier,
   enum, digest or timestamp; unknown fields are rejected; a schema failure
   names fields, never values. The published JSON Schema equals the model.
2. **Safe mode is explicit and closes every free-text surface.** With
   ``AXONRELAY_SAFE_MODE`` unset nothing changes; with it set, each REST
   endpoint and MCP tool that accepts arbitrary text refuses with one fixed,
   value-free message — before any database write or outbound call.
3. **Canaries.** A forbidden string sent through any surface, in either mode,
   must be found nowhere afterwards: not in any table, not in the log, not in
   the response, not in the arguments of a mocked outbound call.

MCP and REST are exercised through the same shared service, which is the
point: there is one implementation to get right.
"""

import asyncio
import json
import logging
import pathlib
from datetime import UTC, datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import inspect, text

from app import langgraph_client, models, safe_envelope
from app.mcp import server

CANARY = "CANARY-7f3a-do-not-store-me"
OPAQUE = "evt_0123456789abcdef"  # 20 chars, URL-safe


def _envelope(**overrides):
    base = {
        "schema_version": 1,
        "policy_version": "p1",
        "event_id": OPAQUE,
        "actor_id": "actor_0123456789abcd",
        "repository_id": "repo_0123456789abcde",
        "action": "artifact_produced",
        "outcome": "success",
        "artifact_id": "art_0123456789abcdef",
        "artifact_version": 3,
        "artifact_commitment": "a" * 64,
        "artifact_commitment_algorithm": "sha256-utf8-v1",
        "occurred_at": "2026-09-07T01:02:03+00:00",
    }
    base.update(overrides)
    return base


@pytest.fixture
def full_text_mode(monkeypatch):
    monkeypatch.delenv(safe_envelope.SAFE_MODE_ENV, raising=False)
    monkeypatch.delenv(safe_envelope.PUBLIC_IDENTIFIERS_ENV, raising=False)


@pytest.fixture
def safe_mode(monkeypatch):
    monkeypatch.setenv(safe_envelope.SAFE_MODE_ENV, "1")
    monkeypatch.delenv(safe_envelope.PUBLIC_IDENTIFIERS_ENV, raising=False)


@pytest.fixture
def outbound(monkeypatch):
    """Every LangGraph Platform call recorded, none made."""
    calls: list[tuple[str, tuple, dict]] = []

    async def _create_thread(*a, **k):
        calls.append(("create_thread", a, k))
        return "thread-mock"

    async def _run(*a, **k):
        calls.append(("run_until_interrupt", a, k))
        return {}

    async def _resume(*a, **k):
        calls.append(("resume_thread", a, k))
        return {}

    monkeypatch.setattr(langgraph_client, "create_thread", _create_thread)
    monkeypatch.setattr(langgraph_client, "run_until_interrupt", _run)
    monkeypatch.setattr(langgraph_client, "resume_thread", _resume)
    return calls


def _everything_stored(db) -> str:
    """Every row of every table, as one string, for canary search."""
    chunks = []
    for table in inspect(db.get_bind()).get_table_names():
        rows = db.execute(text(f'SELECT * FROM "{table}"')).all()
        chunks.append(f"{table}: {rows!r}")
    return "\n".join(chunks)


def _call_tool(tool_name: str, arguments: dict):
    """Invoke an MCP tool the way the SDK does, returning its result or raising its error."""
    tool = server.mcp._tool_manager.get_tool(tool_name)
    return asyncio.run(tool.run(arguments, context=None))


# ------------------------------------------------------------------ 1. schema


def test_a_well_formed_envelope_is_accepted_and_stored_field_for_field(db, full_text_mode):
    event, created = safe_envelope.ingest(db, _envelope())
    assert created is True
    stored = safe_envelope.event_to_dict(event)
    assert stored["event_id"] == OPAQUE
    assert stored["action"] == "artifact_produced"
    assert stored["artifact_commitment"] == "a" * 64
    assert stored["identifier_policy"] == "opaque"
    assert stored["occurred_at"] == "2026-09-07T01:02:03Z"


def test_resending_an_event_id_is_idempotent(db, full_text_mode):
    first, created = safe_envelope.ingest(db, _envelope())
    second, again = safe_envelope.ingest(db, _envelope(outcome="error"))
    assert created and not again
    assert second.id == first.id
    assert second.outcome == models.SafeOutcomeEnum.SUCCESS  # the first envelope stands
    assert db.query(models.SafeEvent).count() == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"title": CANARY},
        {"description": CANARY},
        {"prompt": CANARY},
        {"draft": CANARY},
        {"feedback": CANARY},
        {"body": CANARY},
        {"subject": CANARY},
        {"command_output": CANARY},
        {"file_contents": CANARY},
        {"url": "https://example.invalid/" + CANARY},
        {"clone_path": "/home/someone/" + CANARY},
        {"host": CANARY},
    ],
    ids=lambda d: next(iter(d)),
)
def test_every_free_text_field_is_rejected_by_name_only(db, full_text_mode, caplog, bad):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(safe_envelope.EnvelopeRejected) as exc:
        safe_envelope.ingest(db, _envelope(**bad))
    (field,) = bad
    assert exc.value.fields == [field]
    assert CANARY not in str(exc.value)
    assert CANARY not in caplog.text
    assert db.query(models.SafeEvent).count() == 0


@pytest.mark.parametrize(
    "field, value",
    [
        ("event_id", "short"),
        ("event_id", "has/slash_and_is_long_enough"),
        ("event_id", "has.dot.and.is.long.enough"),
        ("actor_id", "actor:with:colons_is_long"),
        ("repository_id", "owner/repo"),  # a slug is public, not opaque
        ("artifact_commitment", "not-a-digest"),
        ("artifact_commitment_algorithm", "md5"),
        ("policy_version", "p" * 40),
        ("action", "free_form_note"),
        ("outcome", "whatever"),
        ("schema_version", 2),
        ("occurred_at", "2026-09-07T01:02:03"),  # naive
        ("producer_signature", "x"),
    ],
)
def test_bounded_fields_reject_anything_outside_their_shape(db, full_text_mode, field, value):
    with pytest.raises(safe_envelope.EnvelopeRejected) as exc:
        safe_envelope.ingest(db, _envelope(**{field: value}))
    assert field in exc.value.fields
    assert str(value) not in str(exc.value)


def test_artifact_fields_go_together(db, full_text_mode):
    with pytest.raises(safe_envelope.EnvelopeRejected):
        safe_envelope.ingest(db, _envelope(artifact_commitment=None, artifact_commitment_algorithm=None))
    with pytest.raises(safe_envelope.EnvelopeRejected):
        safe_envelope.ingest(
            db,
            _envelope(
                artifact_id=None, artifact_commitment=None, artifact_commitment_algorithm=None, artifact_version=2
            ),
        )


def test_a_non_object_is_rejected_without_being_repeated(db, full_text_mode):
    with pytest.raises(safe_envelope.EnvelopeRejected) as exc:
        safe_envelope.ingest(db, CANARY)
    assert CANARY not in str(exc.value)


def test_public_identifiers_need_the_policy(db, monkeypatch, full_text_mode):
    public = _envelope(identifier_policy="public", repository_id="AxonRelay/core")
    with pytest.raises(safe_envelope.EnvelopeRejected) as exc:
        safe_envelope.ingest(db, public)
    assert exc.value.fields == ["identifier_policy"]
    assert "AxonRelay/core" not in str(exc.value)

    monkeypatch.setenv(safe_envelope.PUBLIC_IDENTIFIERS_ENV, "1")
    event, _ = safe_envelope.ingest(db, public)
    assert event.repository_ref == "AxonRelay/core"


def test_the_published_json_schema_matches_the_model():
    """docs/schemas/safe-envelope-v1.json is what producers build against; it must not drift."""
    path = pathlib.Path(__file__).resolve().parents[2] / "docs" / "schemas" / "safe-envelope-v1.json"
    published = json.loads(path.read_text())
    assert published == safe_envelope.published_json_schema()
    assert published["additionalProperties"] is False
    assert set(published["required"]) >= {"schema_version", "event_id", "actor_id", "repository_id", "action"}


def test_the_stored_table_has_no_text_column():
    """Structural guarantee: nothing free-form can be persisted even by a future bug."""
    for column in models.SafeEvent.__table__.columns:
        assert column.type.__class__.__name__ != "Text", column.name


# -------------------------------------------------------- 2. mode is explicit


def test_full_text_surfaces_work_when_safe_mode_is_unset(client, full_text_mode, outbound, self_actor):
    response = client.post("/tasks", json={"title": "ordinary local PoC task"})
    assert response.status_code == 200
    assert outbound[0][0] == "create_thread"


def test_blank_or_zero_counts_as_unset(monkeypatch):
    for value in ("", "  ", "0", "no", "false"):
        monkeypatch.setenv(safe_envelope.SAFE_MODE_ENV, value)
        assert safe_envelope.safe_mode() is False
    monkeypatch.setenv(safe_envelope.SAFE_MODE_ENV, "1")
    assert safe_envelope.safe_mode() is True


REST_FREE_TEXT = [
    ("post", "/tasks", {"title": CANARY}),
    ("put", "/tasks/1", {"description": CANARY}),
    ("post", "/tasks/1/run", None),
    ("post", "/tasks/1/approve", {"comment": CANARY, "modified_draft": CANARY}),
    ("post", "/tasks/1/reject", {"comment": CANARY, "reason": CANARY}),
    ("post", "/agents", {"name": "n", "agent_type": "writer", "description": CANARY}),
    ("put", "/agents/1", {"description": CANARY}),
]


@pytest.mark.parametrize("method, path, body", REST_FREE_TEXT, ids=lambda x: x if isinstance(x, str) else "")
def test_safe_mode_refuses_every_rest_free_text_surface(client, db, safe_mode, outbound, caplog, method, path, body):
    caplog.set_level(logging.DEBUG)
    before = _everything_stored(db)

    response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)

    assert response.status_code == 403
    assert response.json() == {"detail": safe_envelope.SAFE_MODE_REFUSAL}
    assert CANARY not in response.text
    assert outbound == []
    assert _everything_stored(db) == before
    assert CANARY not in caplog.text


MCP_FREE_TEXT = [
    ("create_task", {"title": CANARY}),
    ("run_task", {"task_id": 1}),
    ("approve_task", {"task_id": 1, "comment": CANARY, "modified_draft": CANARY}),
    ("reject_task", {"task_id": 1, "comment": CANARY, "reason": CANARY}),
    ("create_agent", {"name": "n", "agent_type": "writer", "description": CANARY}),
    ("update_agent", {"agent_id": 1, "description": CANARY}),
    ("register_session", {"actor_name": "a", "host": CANARY, "repo": "r", "clone_path": "/" + CANARY}),
    ("heartbeat_session", {"session_id": 1, "focus": CANARY}),
    ("claim_territory", {"session_id": 1, "paths": ["x"], "reason": CANARY}),
    ("claim_git_resource", {"session_id": 1, "resource": "stash", "reason": CANARY}),
    ("send_relay", {"from_session_id": 1, "subject": CANARY, "body": CANARY}),
    ("ack_relay", {"relay_id": 1, "session_id": 1, "note": CANARY}),
]


@pytest.mark.parametrize("name, args", MCP_FREE_TEXT, ids=[n for n, _ in MCP_FREE_TEXT])
def test_safe_mode_refuses_every_mcp_free_text_tool(mcp_db, safe_mode, outbound, caplog, name, args):
    caplog.set_level(logging.DEBUG)
    before = _everything_stored(mcp_db)

    with pytest.raises(ToolError) as exc:
        _call_tool(name, args)

    # The SDK prefixes the tool name (not caller data); the message itself is the fixed one.
    assert str(exc.value).endswith(safe_envelope.SAFE_MODE_REFUSAL)
    assert CANARY not in str(exc.value)
    assert outbound == []
    assert _everything_stored(mcp_db) == before
    assert CANARY not in caplog.text


def test_review_pending_task_is_refused_before_it_shows_anything(mcp_db, safe_mode):
    tool = server.mcp._tool_manager.get_tool("review_pending_task")
    assert tool is not None
    # It needs an elicitation context; the guard runs before that matters.
    with pytest.raises(ToolError) as exc:
        asyncio.run(tool.run({"task_id": 1}, context=None))
    assert str(exc.value).endswith(safe_envelope.SAFE_MODE_REFUSAL)


def test_health_reports_the_mode(client, safe_mode):
    assert client.get("/").json()["safe_mode"] is True


# ------------------------------------------------------------------ 3. canaries


def test_rest_ingestion_shares_the_service_and_never_echoes_a_rejected_value(client, db, safe_mode, caplog):
    caplog.set_level(logging.DEBUG)

    ok = client.post("/envelopes", json=_envelope())
    assert ok.status_code == 201
    assert ok.json()["created"] is True
    again = client.post("/envelopes", json=_envelope())
    assert again.status_code == 200 and again.json()["created"] is False

    bad = client.post("/envelopes", json=_envelope(title=CANARY, event_id="second_0123456789abc"))
    assert bad.status_code == 422
    assert bad.json() == {"detail": {"reason": "invalid", "fields": ["title"]}}
    assert CANARY not in bad.text
    assert CANARY not in caplog.text
    assert CANARY not in _everything_stored(db)

    listed = client.get("/envelopes").json()
    assert [e["event_id"] for e in listed] == [OPAQUE]


def test_mcp_ingestion_shares_the_service_and_never_echoes_a_rejected_value(mcp_db, safe_mode, caplog):
    caplog.set_level(logging.DEBUG)

    result = _call_tool("ingest_safe_envelope", {"envelope": _envelope()})
    payload = result.structured_content if hasattr(result, "structured_content") else result
    assert json.dumps(payload, default=str).count(OPAQUE) >= 1

    with pytest.raises(ToolError) as exc:
        _call_tool("ingest_safe_envelope", {"envelope": _envelope(body=CANARY, event_id="third_0123456789abcd")})
    assert "body" in str(exc.value)
    assert CANARY not in str(exc.value)
    assert CANARY not in caplog.text
    assert CANARY not in _everything_stored(mcp_db)


def test_the_generic_422_no_longer_echoes_the_input(client, full_text_mode):
    """FastAPI's default validation body carried `input`; ours carries loc and type only."""
    response = client.post("/tasks", json={"title": ""})  # min_length=1
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] and all(set(item) == {"loc", "type"} for item in body["detail"])

    long_title = CANARY * 30  # > max_length=500
    response = client.post("/tasks", json={"title": long_title})
    assert response.status_code == 422
    assert CANARY not in response.text


def test_a_rejected_envelope_leaves_no_trace_in_the_full_text_mode_either(db, full_text_mode, caplog):
    """The schema, not the mode switch, is what keeps content out of the envelope path."""
    caplog.set_level(logging.DEBUG)
    with pytest.raises(safe_envelope.EnvelopeRejected):
        safe_envelope.ingest(db, _envelope(draft=CANARY))
    assert CANARY not in caplog.text
    assert CANARY not in _everything_stored(db)


def test_ingest_never_calls_outbound_services(db, safe_mode, outbound):
    safe_envelope.ingest(db, _envelope())
    assert outbound == []


def test_the_tool_surface_lists_the_ingestion_tools():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"ingest_safe_envelope", "list_safe_events"} <= names


def test_datetime_round_trip_is_utc(db, full_text_mode):
    event, _ = safe_envelope.ingest(db, _envelope(occurred_at="2026-09-07T10:02:03+09:00"))
    assert event.occurred_at == datetime(2026, 9, 7, 1, 2, 3, tzinfo=UTC).replace(tzinfo=None)
