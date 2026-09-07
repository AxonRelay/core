"""Caller identity and scoped authorization (issue #27, app/authz.py).

What is actually being pinned here:

* **Identity is decided server-side.** A credential names an Actor; the
  approval, the session and the task creator are recorded against that Actor,
  and a request parameter that names a different one is refused rather than
  ignored.
* **One policy, two surfaces.** REST and MCP consult the same tables and the
  same `resolve` / `require`, so a scope means the same thing on both. A
  structural test fails if any tool or route is added without a decision.
* **Refusals say nothing.** A 401 or 403 carries the scope that was needed and
  nothing about the resource, and no token or Authorization header reaches the
  database, a log record or a response body.
* **Off means off.** Without `AXONRELAY_REQUIRE_AUTH` every call runs as the
  loopback principal and the personal PoC behaves exactly as before.

The REST half is end to end through the app. The MCP half drives
`AuthorizationMiddleware` with the `ServerRequestContext` the SDK builds for
each inbound message (there is no in-process client transport in the SDK to
drive a full handshake), and a separate test pins that the middleware is
installed and that the SDK's middleware contract still has the shape this code
depends on — the same guard `test_mcp_surface.py` keeps over the tool surface.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

import pytest
from fastapi.routing import APIRoute
from mcp.server.mcpserver.exceptions import ToolError

from app import authz, crud, models
from app.mcp import server

TOKEN_CANARY_HEADER = "Bearer "  # prefix; the token itself is generated per test


def _credential(db, actor, scopes, *, revoked=False, label="test"):
    """Issue a credential the way `python -m app.credentials issue` does."""
    token = authz.issue_token()
    row = models.Credential(
        actor_id=actor.id,
        label=label,
        token_hash=authz.token_digest(token),
        scopes=authz.format_scopes(scopes),
        revoked_at=datetime.utcnow() if revoked else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return token, row


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv(authz.REQUIRE_AUTH_ENV, "1")


@pytest.fixture
def unenforced(monkeypatch):
    monkeypatch.delenv(authz.REQUIRE_AUTH_ENV, raising=False)


@pytest.fixture
def agent_actor(db):
    return (
        crud.get_or_create_actor(db, "writer-bot", models.ActorTypeEnum.AI)
        if hasattr(crud, "get_or_create_actor")
        else _make_ai(db)
    )


def _make_ai(db):
    actor = models.Actor(type=models.ActorTypeEnum.AI, name="writer-bot")
    db.add(actor)
    db.commit()
    db.refresh(actor)
    return actor


# --------------------------------------------------------------- credentials


def test_a_token_is_stored_only_as_a_digest(db, self_actor):
    token, row = _credential(db, self_actor, {authz.Scope.LEDGER_READ})

    assert row.token_hash == authz.token_digest(token)
    assert token not in row.token_hash
    stored = db.execute(models.Credential.__table__.select()).all()
    assert not any(token in str(value) for record in stored for value in record)


def test_an_issued_token_has_real_entropy():
    tokens = {authz.issue_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 40 for t in tokens)


def test_a_credential_resolves_to_its_actor_and_scopes(db, self_actor):
    token, row = _credential(db, self_actor, {authz.Scope.LEDGER_READ, authz.Scope.EXPORT_READ})

    principal = authz.authenticate(db, token)

    assert principal.actor_id == self_actor.id
    assert principal.actor_name == self_actor.name
    assert principal.scopes == {authz.Scope.LEDGER_READ, authz.Scope.EXPORT_READ}
    assert principal.source == "credential"
    assert principal.credential_id == row.id
    db.refresh(row)
    assert row.last_used_at is not None


@pytest.mark.parametrize("presented", [None, "", "not-a-token", "x" * 43])
def test_a_token_that_was_never_issued_resolves_to_nobody(db, self_actor, presented):
    _credential(db, self_actor, {authz.Scope.LEDGER_READ})
    assert authz.authenticate(db, presented) is None


def test_a_revoked_credential_stops_working(db, self_actor):
    token, row = _credential(db, self_actor, {authz.Scope.LEDGER_READ})
    assert authz.authenticate(db, token) is not None

    row.revoked_at = datetime.utcnow()
    db.commit()

    assert authz.authenticate(db, token) is None


def test_a_credential_whose_actor_is_gone_stops_working(db):
    actor = _make_ai(db)
    token, _ = _credential(db, actor, {authz.Scope.LEDGER_READ})
    db.delete(actor)
    db.commit()

    assert authz.authenticate(db, token) is None


def test_an_unknown_scope_name_is_dropped_not_guessed(db, self_actor, caplog):
    caplog.set_level(logging.DEBUG)
    token, row = _credential(db, self_actor, {authz.Scope.LEDGER_READ})
    row.scopes = "ledger:read,ledger:everything"
    db.commit()

    principal = authz.authenticate(db, token)
    assert principal.scopes == {authz.Scope.LEDGER_READ}


@pytest.mark.parametrize(
    "header, expected",
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("Bearer   abc  ", "abc"),
        ("Basic abc", None),
        ("abc", None),
        ("Bearer", None),
        ("Bearer  ", None),
        (None, None),
    ],
)
def test_only_a_bearer_header_yields_a_token(header, expected):
    assert authz.bearer_token(header) == expected


# ------------------------------------------------------------- loopback mode


def test_without_enforcement_every_caller_is_the_operator(db, self_actor, unenforced):
    principal = authz.resolve(db, authorization=None, transport_is_local=False)

    assert principal.source == "loopback"
    assert principal.actor_id == self_actor.id
    assert principal.scopes == authz.ALL_SCOPES


def test_stdio_is_loopback_even_under_enforcement(db, self_actor, enforced):
    """A stdio transport is a process the operator started; ADR-011 records the assumption."""
    principal = authz.resolve(db, authorization=None, transport_is_local=True)
    assert principal.source == "loopback"
    assert principal.scopes == authz.ALL_SCOPES


def test_under_enforcement_an_http_caller_without_a_credential_is_refused(db, self_actor, enforced):
    with pytest.raises(authz.Unauthenticated):
        authz.resolve(db, authorization=None, transport_is_local=False)
    with pytest.raises(authz.Unauthenticated):
        authz.resolve(db, authorization="Bearer nope", transport_is_local=False)


# ------------------------------------------------------------------- binding


def test_the_acting_actor_is_the_credentials_actor(db, self_actor):
    agent = _make_ai(db)
    principal = authz.Principal(
        actor_id=agent.id, actor_name=agent.name, scopes=authz.ALL_SCOPES, source="credential", credential_id=1
    )
    with authz.bind(principal):
        assert authz.acting_actor_id(db) == agent.id
    # Outside the call the operator is the fallback, never a leftover identity.
    assert authz.acting_actor_id(db) == self_actor.id


def test_claiming_another_actor_is_refused_not_ignored(db, self_actor):
    agent = _make_ai(db)
    principal = authz.Principal(
        actor_id=agent.id, actor_name=agent.name, scopes=authz.ALL_SCOPES, source="credential", credential_id=1
    )
    with authz.bind(principal):
        authz.check_claimed_actor(None)  # omitted: fine
        authz.check_claimed_actor(agent.name)  # its own: fine
        with pytest.raises(authz.Forbidden):
            authz.check_claimed_actor("self")


def test_a_loopback_caller_may_still_name_any_actor(db, self_actor, unenforced):
    """The local PoC registers sessions for several agents from one process."""
    with authz.bind(authz.loopback_principal(db)):
        authz.check_claimed_actor("any-agent-name")


# ----------------------------------------------------------------- REST, end to end


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_health_answers_without_a_credential(client, enforced):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["auth_required"] is True


def test_rest_refuses_an_uncredentialled_caller(client, db, self_actor, enforced, caplog):
    caplog.set_level(logging.DEBUG)
    response = client.get("/tasks")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    body = response.text
    assert "task" not in body.lower() or "detail" in body  # no resource contents
    assert "credential is required" in response.json()["detail"]


def test_rest_admits_a_credential_with_the_scope(client, db, self_actor, enforced):
    token, _ = _credential(db, self_actor, {authz.Scope.LEDGER_READ})
    assert client.get("/tasks", headers=_auth(token)).status_code == 200


def test_rest_refuses_a_credential_without_the_scope(client, db, self_actor, enforced):
    token, _ = _credential(db, self_actor, {authz.Scope.COORDINATION_READ})

    response = client.get("/tasks", headers=_auth(token))

    assert response.status_code == 403
    assert response.json()["required_scope"] == authz.Scope.LEDGER_READ.value


def test_rest_refuses_a_revoked_credential(client, db, self_actor, enforced):
    token, row = _credential(db, self_actor, {authz.Scope.LEDGER_READ})
    row.revoked_at = datetime.utcnow()
    db.commit()

    assert client.get("/tasks", headers=_auth(token)).status_code == 401


def test_a_read_scope_does_not_write_and_a_write_scope_does_not_administer(client, db, self_actor, enforced):
    reader, _ = _credential(db, self_actor, {authz.Scope.LEDGER_READ})
    writer, _ = _credential(db, self_actor, {authz.Scope.LEDGER_READ, authz.Scope.LEDGER_WRITE}, label="w")

    assert client.post("/agents", json={"name": "x", "agent_type": "writer"}, headers=_auth(reader)).status_code == 403
    created = client.post("/agents", json={"name": "x", "agent_type": "writer"}, headers=_auth(writer))
    assert created.status_code == 200
    agent_id = created.json()["id"]
    # Deleting cascades to the Actor, which can NULL a hashed reviewer field: administration.
    assert client.delete(f"/agents/{agent_id}", headers=_auth(writer)).status_code == 403


def test_the_authenticated_actor_is_what_actors_me_reports(client, db, self_actor, enforced):
    agent = _make_ai(db)
    token, _ = _credential(db, agent, {authz.Scope.LEDGER_READ})

    body = client.get("/actors/me", headers=_auth(token)).json()

    assert body["id"] == agent.id
    assert body["name"] == "writer-bot"


def test_an_approval_is_attributed_to_the_credential_not_a_parameter(client, db, self_actor, enforced, monkeypatch):
    """Cross-actor: two credentials approving one task are two different reviewers."""
    from app import langgraph_client

    async def _resume(*a, **k):
        return {}

    monkeypatch.setattr(langgraph_client, "resume_thread", _resume)
    agent = _make_ai(db)
    token, _ = _credential(db, agent, {authz.Scope.LEDGER_WRITE})

    task = models.Task(thread_id="t-authz", title="t", status=models.TaskStatusEnum.WAITING_APPROVAL)
    db.add(task)
    db.commit()
    db.refresh(task)
    crud.add_draft(db, task_id=task.id, content="draft")

    response = client.post(f"/tasks/{task.id}/approve", json={"comment": "ok"}, headers=_auth(token))

    assert response.status_code == 200
    assert response.json()["reviewer_actor_id"] == agent.id
    assert crud.get_approvals(db, task.id)[0].reviewer_actor_id == agent.id


def test_no_token_reaches_a_response_body_or_a_log_record(client, db, self_actor, enforced, caplog):
    caplog.set_level(logging.DEBUG)
    token, _ = _credential(db, self_actor, {authz.Scope.COORDINATION_READ})

    ok = client.get("/coordination/board", headers=_auth(token))
    denied = client.get("/tasks", headers=_auth(token))
    rejected = client.get("/tasks", headers=_auth("a-token-that-was-never-issued-aaaaaaaa"))

    assert (ok.status_code, denied.status_code, rejected.status_code) == (200, 403, 401)
    for text in (ok.text, denied.text, rejected.text, caplog.text):
        assert token not in text
        assert "a-token-that-was-never-issued" not in text
        assert "Bearer" not in text


def test_a_denial_reveals_nothing_about_the_resource(client, db, self_actor, enforced):
    token, _ = _credential(db, self_actor, {authz.Scope.COORDINATION_READ})

    missing = client.get("/tasks/424242", headers=_auth(token))
    present = client.get("/tasks/1", headers=_auth(token))

    # Same answer whether or not the task exists: the scope check runs first.
    assert missing.status_code == present.status_code == 403
    assert missing.json() == present.json()


def test_without_enforcement_rest_behaves_exactly_as_before(client, db, self_actor, unenforced):
    assert client.get("/tasks").status_code == 200
    assert client.get("/actors/me").json()["id"] == self_actor.id


# ------------------------------------------------------------------ MCP


@dataclass
class _FakeRequest:
    headers: dict = field(default_factory=dict)


@dataclass
class _FakeCtx:
    """The fields AuthorizationMiddleware reads off the SDK's ServerRequestContext."""

    method: str
    params: dict | None = None
    request: _FakeRequest | None = None


def _run_middleware(ctx, mcp_db):
    """Drive the middleware; `call_next` records the principal it was given."""
    seen = {}

    async def call_next(inner_ctx):
        seen["principal"] = authz.current()
        return {"ok": True}

    middleware = next(m for m in server.mcp.middleware if isinstance(m, server.AuthorizationMiddleware))
    result = asyncio.run(middleware(ctx, call_next))
    return result, seen.get("principal")


def test_the_middleware_is_installed_and_matches_the_sdk_contract():
    """`Server.middleware` is marked provisional upstream; a change must fail here, not in production."""
    import inspect

    from mcp.server.context import ServerMiddleware, ServerRequestContext

    assert any(isinstance(m, server.AuthorizationMiddleware) for m in server.mcp.middleware)
    assert {"method", "params", "request"} <= set(ServerRequestContext.__dataclass_fields__)
    signature = inspect.signature(ServerMiddleware.__call__)
    assert list(signature.parameters) == ["self", "ctx", "call_next"]


def test_stdio_calls_run_as_the_operator(mcp_db, self_actor, enforced):
    ctx = _FakeCtx(method="tools/call", params={"name": "list_tasks"}, request=None)

    result, principal = _run_middleware(ctx, mcp_db)

    assert result == {"ok": True}
    assert principal.source == "loopback"


def test_an_http_call_without_a_credential_is_refused(mcp_db, self_actor, enforced):
    ctx = _FakeCtx(method="tools/call", params={"name": "list_tasks"}, request=_FakeRequest(headers={}))

    with pytest.raises(ToolError) as exc:
        _run_middleware(ctx, mcp_db)

    assert "credential is required" in str(exc.value)


def test_an_http_call_needs_the_tools_scope(mcp_db, self_actor, enforced):
    token, _ = _credential(mcp_db, self_actor, {authz.Scope.COORDINATION_READ})
    headers = {"authorization": f"Bearer {token}"}

    allowed = _FakeCtx(method="tools/call", params={"name": "get_board"}, request=_FakeRequest(headers=headers))
    _, principal = _run_middleware(allowed, mcp_db)
    assert principal.actor_id == self_actor.id

    refused = _FakeCtx(method="tools/call", params={"name": "approve_task"}, request=_FakeRequest(headers=headers))
    with pytest.raises(ToolError) as exc:
        _run_middleware(refused, mcp_db)
    assert authz.Scope.LEDGER_WRITE.value in str(exc.value)


def test_resource_reads_take_the_scope_of_the_data_behind_them(mcp_db, self_actor, enforced):
    token, _ = _credential(mcp_db, self_actor, {authz.Scope.COORDINATION_READ})
    headers = {"authorization": f"Bearer {token}"}

    board = _FakeCtx(
        method="resources/read", params={"uri": "axonrelay://board"}, request=_FakeRequest(headers=headers)
    )
    _run_middleware(board, mcp_db)  # coordination:read - allowed

    task = _FakeCtx(
        method="resources/read",
        params={"uri": "axonrelay://tasks/7/drafts/2"},
        request=_FakeRequest(headers=headers),
    )
    with pytest.raises(ToolError) as exc:
        _run_middleware(task, mcp_db)
    assert authz.Scope.LEDGER_READ.value in str(exc.value)


def test_handshake_and_listing_need_a_credential_but_no_scope(mcp_db, self_actor, enforced):
    token, _ = _credential(mcp_db, self_actor, set())  # no scopes at all
    headers = {"authorization": f"Bearer {token}"}

    for method in ("initialize", "tools/list", "ping"):
        ctx = _FakeCtx(method=method, params=None, request=_FakeRequest(headers=headers))
        result, principal = _run_middleware(ctx, mcp_db)
        assert result == {"ok": True}
        assert principal.scopes == frozenset()

    anonymous = _FakeCtx(method="tools/list", params=None, request=_FakeRequest(headers={}))
    with pytest.raises(ToolError):
        _run_middleware(anonymous, mcp_db)


def test_without_enforcement_an_http_mcp_call_is_loopback(mcp_db, self_actor, unenforced):
    ctx = _FakeCtx(method="tools/call", params={"name": "approve_task"}, request=_FakeRequest(headers={}))
    _, principal = _run_middleware(ctx, mcp_db)
    assert principal.source == "loopback"


def test_the_mcp_refusal_carries_no_token(mcp_db, self_actor, enforced, caplog):
    caplog.set_level(logging.DEBUG)
    token, _ = _credential(mcp_db, self_actor, {authz.Scope.COORDINATION_READ})
    ctx = _FakeCtx(
        method="tools/call",
        params={"name": "approve_task"},
        request=_FakeRequest(headers={"authorization": f"Bearer {token}"}),
    )

    with pytest.raises(ToolError) as exc:
        _run_middleware(ctx, mcp_db)

    assert token not in str(exc.value)
    assert token not in caplog.text


# ---------------------------------------------- one policy, no unclassified surface


def test_every_mcp_tool_has_a_scope():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names - set(authz.TOOL_SCOPES) == set(), "a tool without a scope would default to open"
    assert set(authz.TOOL_SCOPES) - names == set(), "TOOL_SCOPES names a tool that no longer exists"


def test_every_mcp_resource_template_has_a_scope():
    templates = {t.uri_template for t in asyncio.run(server.mcp.list_resource_templates())}
    resources = {str(r.uri) for r in asyncio.run(server.mcp.list_resources())}
    assert (templates | resources) == set(authz.RESOURCE_SCOPES)


def test_every_rest_route_has_a_decision():
    from app.main import app

    routes = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    assert routes - set(authz.ROUTE_SCOPES) - authz.PUBLIC_ROUTES == set()
    assert set(authz.ROUTE_SCOPES) - routes == set()


def test_the_two_surfaces_agree_on_what_a_scope_means():
    """The same operation over REST and over MCP must need the same scope."""
    pairs = [
        (("GET", "/tasks"), "list_tasks"),
        (("POST", "/tasks"), "create_task"),
        (("POST", "/tasks/{task_id}/approve"), "approve_task"),
        (("GET", "/tasks/{task_id}/ledger/verify"), "verify_task_ledger"),
        (("GET", "/coordination/board"), "get_board"),
        (("POST", "/envelopes"), "ingest_safe_envelope"),
        (("GET", "/envelopes"), "list_safe_events"),
    ]
    for route, tool in pairs:
        assert authz.ROUTE_SCOPES[route] == authz.TOOL_SCOPES[tool], f"{route} vs {tool}"


def test_export_read_is_declared_and_reserved():
    """#28's exporter will use it; nothing maps to it yet, and that is deliberate."""
    assert authz.Scope.EXPORT_READ in authz.ALL_SCOPES
    mapped = set(authz.TOOL_SCOPES.values()) | {s for s in authz.ROUTE_SCOPES.values() if s}
    assert authz.Scope.EXPORT_READ not in mapped
