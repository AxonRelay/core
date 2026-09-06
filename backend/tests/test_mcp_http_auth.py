"""The optional bearer token in front of the Streamable HTTP transport.

Three properties, each a separate test because each is a separate promise:
with the variable unset nothing changes; with it set, a request without the
token never reaches the MCP app; with it set, a request carrying the token
does.
"""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.mcp import http_auth


def _inner_app():
    async def ok(request):
        return PlainTextResponse("reached")

    return Starlette(routes=[Route("/mcp", ok, methods=["GET", "POST"])])


def test_unset_means_untouched(monkeypatch):
    monkeypatch.delenv(http_auth.TOKEN_ENV, raising=False)
    inner = _inner_app()
    assert http_auth.wrap_if_configured(inner) is inner


def test_blank_counts_as_unset(monkeypatch):
    monkeypatch.setenv(http_auth.TOKEN_ENV, "   ")
    inner = _inner_app()
    assert http_auth.wrap_if_configured(inner) is inner


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Basic c2VjcmV0"},
        {"Authorization": "secret"},
    ],
)
def test_set_refuses_without_the_token(monkeypatch, headers):
    monkeypatch.setenv(http_auth.TOKEN_ENV, "secret")
    client = TestClient(http_auth.wrap_if_configured(_inner_app()))
    response = client.post("/mcp", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert response.json()["error"] == "unauthorized"


def test_set_admits_the_token(monkeypatch):
    monkeypatch.setenv(http_auth.TOKEN_ENV, "secret")
    client = TestClient(http_auth.wrap_if_configured(_inner_app()))
    response = client.post("/mcp", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    assert response.text == "reached"


def test_real_mcp_app_is_wrapped(monkeypatch):
    """The entry point wraps the SDK's own app, not a stand-in.

    Only the refusal is exercised end to end: a request that carries the token
    would enter the SDK's streaming session machinery, which is not something a
    synchronous test client can drive to completion. The admit path is covered
    above against a plain app, and the wrapper is the same object either way.
    """
    monkeypatch.setenv(http_auth.TOKEN_ENV, "secret")
    from app.mcp.server import mcp

    app = http_auth.wrap_if_configured(mcp.streamable_http_app())
    assert isinstance(app, http_auth.BearerTokenMiddleware)
    assert TestClient(app).post("/mcp", json={}).status_code == 401
