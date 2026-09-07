"""The MCP compatibility matrix this server is actually tested against (issue #30).

Two things live here, and they are the same thing seen from two sides:

* **What we claim.** `SPEC_REVISIONS`, `SDK_RANGE` and `TESTED_CLIENTS` are the
  matrix docs/mcp-server.md publishes. They are data rather than prose so a
  test can hold the documentation to the code, and so a client can read the
  claim over the wire (`axonrelay://compat`) instead of trusting a README.
* **What we do about it.** `uses_input_required()` and `client_can_elicit()`
  are the two runtime decisions that follow from the matrix.

Why this module exists at all: the 2026-07-28 revision changed how a server
asks the human a question mid-tool-call. Up to 2025-11-25 the server sent a
standalone `elicitation/create` back down the open connection and awaited the
answer inside the same `tools/call`. From 2026-07-28 the call *returns* an
`InputRequiredResult` carrying the questions plus an opaque `requestState`,
and the client asks the same tool again with the answers. The second shape is
resumable and survives a dropped connection; the first does not.

AxonRelay does not pick between them by hand. `review_pending_task` declares
its question with the SDK's `Resolve`/`Elicit` resolvers and the SDK renders it
in whichever shape the negotiated revision requires, so one code path serves
both. What this module adds is the part the SDK cannot decide for us: whether
the client in front of us can answer a question at all, and - since a
2026-07-28 round trip *replays the whole tool call* - what that means for a
ledger that must not gain a duplicate entry (app/crud.py, migration 013).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_types.version import is_version_at_least

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp_types import ClientCapabilities

# The first revision whose `tools/call` carries elicitation inside an
# `InputRequiredResult` instead of a standalone server-to-client request.
# Pinned deliberately: a newer revision does not move this boundary, and
# reading it from the SDK's "latest" constant would make it move silently.
INPUT_REQUIRED_SINCE = "2026-07-28"

SPEC_REVISIONS: dict[str, str] = {
    "2024-11-05": "tools and resources only; no elicitation, so approval is approve_task / reject_task",
    "2025-03-26": "tools and resources only; no elicitation, so approval is approve_task / reject_task",
    "2025-06-18": "standalone elicitation/create inside tools/call; approval is held open on the connection",
    "2025-11-25": "standalone elicitation/create inside tools/call; approval is held open on the connection",
    "2026-07-28": "multi-round tools/call: InputRequiredResult + requestState; approval survives a reconnect",
}
"""Every revision the SDK will negotiate, and how an approval reaches the human on it.

Not a wish list - `test_mcp_protocol_compat.py` drives a real client over each
interaction model, and the keys are asserted against the SDK's own supported set,
so a revision the SDK drops or adds fails here rather than at an operator's IDE.
"""

SDK_RANGE = ">=2.1,<3"
"""The `mcp` Python SDK line this server is written against (backend/requirements.txt).

2.0 renamed `FastMCP` to `MCPServer`; 2.1 is the first line carrying the
`Resolve`/`Elicit` resolvers and the sealed `requestState` boundary that the
2026-07-28 interaction model needs. The upper cap is moved by hand.
"""

TESTED_CLIENTS: tuple[str, ...] = (
    "Claude Code (stdio, .mcp.json) - form elicitation",
    "the SDK's own ClientSession over stdio and Streamable HTTP - both interaction models, in CI",
)
"""Clients an approval has actually been driven through end to end.

A client not named here is not refused; it simply has not been exercised, and
one without elicitation support still has the direct-tool fallback below.
"""

FALLBACK_TOOLS = ("approve_task", "reject_task")
"""What a client that cannot be asked a question uses instead.

They take `artifact_version` / `expected_commitment` explicitly, so the same
"decide only on the draft you read" guarantee holds without any interaction
round: the caller reads `get_drafts`, then names what it read.
"""


def uses_input_required(protocol_version: str | None) -> bool:
    """True when `protocol_version` carries elicitation as `InputRequiredResult`.

    `None` - no negotiated version on the context - reads as the older model,
    matching the SDK's own default for a connection that never announced one.
    """
    return protocol_version is not None and is_version_at_least(protocol_version, INPUT_REQUIRED_SINCE)


def client_can_elicit(capabilities: ClientCapabilities | None) -> bool:
    """True when the client declared it can answer a *form* elicitation.

    Mirrors the rule the SDK enforces when it renders the request, so we reach
    the same verdict one step earlier and can answer with the fallback instead
    of a protocol error. A bare `elicitation: {}` predates the mode split and
    counts as form support; a client that declared only `url` mode does not,
    because an approval decision is a form.
    """
    elicitation = capabilities.elicitation if capabilities is not None else None
    if elicitation is None:
        return False
    return elicitation.form is not None or elicitation.url is None


def matrix() -> dict[str, object]:
    """The whole claim, as the `axonrelay://compat` resource serves it."""
    return {
        "spec_revisions": dict(SPEC_REVISIONS),
        "input_required_since": INPUT_REQUIRED_SINCE,
        "sdk": {"package": "mcp", "range": SDK_RANGE},
        "tested_clients": list(TESTED_CLIENTS),
        "fallback_tools": list(FALLBACK_TOOLS),
    }
