"""Optional shared-secret check in front of the Streamable HTTP transport.

The transport is meant to stay on a private network, and by default nothing
here changes that: with ``AXONRELAY_MCP_TOKEN`` unset the ASGI app is returned
untouched and the guarantees are exactly what they were. Set it, and every
HTTP request must carry ``Authorization: Bearer <token>`` or it is answered
with 401 before the MCP layer sees it.

This is a shared secret, not OAuth. The MCP specification's authorization for
HTTP transports is OAuth 2.1, and the SDK has hooks for it (``token_verifier``
plus ``AuthSettings``), but those want an issuer and resource-server metadata
that a single-operator deployment has no honest values for. A static token is
the smallest thing that turns "reachable means trusted" into "reachable and
holding the secret means trusted", which is the gap this closes - see
docs/adr-008-optional-bearer-token.md.
"""

import hmac
import os

from starlette.responses import JSONResponse

TOKEN_ENV = "AXONRELAY_MCP_TOKEN"


class BearerTokenMiddleware:
    """ASGI middleware: refuse HTTP requests that do not present the token."""

    def __init__(self, app, token: str):
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        presented = b""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                presented = value
                break

        if presented.startswith(b"Bearer ") and hmac.compare_digest(presented[7:].strip(), self._token):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {"error": "unauthorized", "detail": f"This endpoint requires a bearer token ({TOKEN_ENV})."},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="axonrelay"'},
        )
        await response(scope, receive, send)


def wrap_if_configured(app):
    """Return ``app`` as-is, or wrapped in the token check when the env var is set."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        return app
    return BearerTokenMiddleware(app, token)
