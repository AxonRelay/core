"""The MCP server's public surface.

Nothing else in the suite imports `app.mcp.server`, which is how an SDK rename
(`FastMCP` -> `MCPServer` in mcp 2.0) reached the tree while CI stayed green:
lint does not resolve imports and no test loaded the module. These tests import
it for real and assert the tools and resources a connected client relies on, so
the next SDK move fails here instead of at an operator's IDE.
"""

import asyncio

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
