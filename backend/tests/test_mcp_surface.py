"""The MCP server's public surface.

Nothing else in the suite imports `app.mcp.server`, which is how an SDK rename
(`FastMCP` -> `MCPServer` in mcp 2.0) reached the tree while CI stayed green:
lint does not resolve imports and no test loaded the module. These tests import
it for real and assert the tools and resources a connected client relies on, so
the next SDK move fails here instead of at an operator's IDE.
"""

import asyncio
import inspect
import sys

import pytest

LEDGER_TOOLS = {
    "list_tasks",
    "list_pending_approvals",
    "get_task",
    "get_drafts",
    "verify_task_ledger",
    "create_task",
    "run_task",
    "approve_task",
    "reject_task",
    "review_pending_task",
    "list_agents",
    "create_agent",
    "update_agent",
    "get_self_actor",
}

COORDINATION_TOOLS = {
    "register_session",
    "heartbeat_session",
    "end_session",
    "get_board",
    "check_conflicts",
    "claim_territory",
    "release_territory",
    "send_relay",
    "read_inbox",
    "ack_relay",
}


@pytest.fixture(scope="module")
def server():
    from app.mcp import server as mcp_server

    return mcp_server


@pytest.fixture(scope="module")
def tools(server):
    return {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}


def test_the_server_module_imports(server):
    assert server.mcp.name == "axonrelay"


def test_every_ledger_tool_is_registered(tools):
    assert set(tools) >= LEDGER_TOOLS


def test_every_coordination_tool_is_registered(tools):
    assert set(tools) >= COORDINATION_TOOLS


def test_no_tool_was_registered_twice(server):
    names = [tool.name for tool in asyncio.run(server.mcp.list_tools())]
    assert len(names) == len(set(names))


def test_every_tool_documents_itself(tools):
    """Tool descriptions are the only instructions the model gets."""
    undocumented = [name for name, tool in tools.items() if not (tool.description or "").strip()]
    assert undocumented == []


def test_resources_and_templates_are_registered(server):
    resources = {str(r.uri) for r in asyncio.run(server.mcp.list_resources())}
    templates = {t.uri_template for t in asyncio.run(server.mcp.list_resource_templates())}

    assert "axonrelay://board" in resources
    assert templates == {
        "axonrelay://tasks/{task_id}",
        "axonrelay://tasks/{task_id}/drafts/{version}",
    }


def test_stdio_and_http_transports_are_both_reachable(server):
    """`--http` is what lets several devices share one instance."""
    assert callable(server.main)
    assert hasattr(server.mcp, "streamable_http_app")


def test_default_invocation_runs_stdio(server, monkeypatch):
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(sys, "argv", ["app.mcp.server"])

    server.main()

    assert calls == [((), {})]


def test_http_flag_passes_host_port_and_path_to_the_transport(server, monkeypatch):
    """Regression: main() used to set `mcp.settings.host`, which mcp 2.x rejects.

    `Settings` has no host/port fields — the server crashed on startup with
    `ValueError: "Settings" object has no field "host"`, so --http never served
    anything. host/port/path are transport kwargs of `run()`, not settings.
    """
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(sys, "argv", ["app.mcp.server", "--http", "--host", "0.0.0.0", "--port", "9999"])

    server.main()

    assert calls == [
        ((), {"transport": "streamable-http", "host": "0.0.0.0", "port": 9999, "streamable_http_path": "/mcp"})
    ]


def test_http_binds_loopback_by_default(server, monkeypatch):
    """The transport has no per-caller auth, so it must not default to a public bind."""
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(sys, "argv", ["app.mcp.server", "--http"])

    server.main()

    assert calls[0][1]["host"] == "127.0.0.1"


def test_the_http_kwargs_match_the_sdk_signature(server):
    """Bind the kwargs against the real SDK method, so an SDK rename fails here."""
    signature = inspect.signature(server.mcp.run_streamable_http_async)
    signature.bind(host="127.0.0.1", port=8765, streamable_http_path="/mcp")


def test_the_streamable_http_app_mounts_the_mcp_endpoint(server):
    app = server.mcp.streamable_http_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/mcp" in paths
