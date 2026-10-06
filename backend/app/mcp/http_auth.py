"""Optional shared-secret check in front of the Streamable HTTP transport.

The transport is meant to stay on a private network, and by default nothing
here changes that: with ``AXONRELAY_MCP_TOKEN`` unset the ASGI app is returned
untouched and the guarantees are exactly what they were. Set it, and every
HTTP request must carry ``Authorization: Bearer <token>`` or it is answered
with 401 before the MCP layer sees it.

Since ADR-011 the same header may instead carry a per-caller credential. The
two do not stack - there is one header - so they are ordered: while per-caller
authentication is enforced (``AXONRELAY_REQUIRE_AUTH``), this gate admits a live
credential and nothing else, and the shared secret no longer opens anything.
Admitting either would have been the worst of both: the shared secret would
pass here only to be refused as an unknown credential behind it, and a
credential would skip the secret the operator thought was still required.
Configuring both logs a warning saying so.

This is a shared secret, not OAuth. The MCP specification's authorization for
HTTP transports is OAuth 2.1, and the SDK has hooks for it (``token_verifier``
plus ``AuthSettings``), but those want an issuer and resource-server metadata
that a single-operator deployment has no honest values for. A static token is
the smallest thing that turns "reachable means trusted" into "reachable and
holding the secret means trusted", which is the gap this closes - see
docs/adr-008-optional-bearer-token.md.
"""

import hmac
import logging
import os

from starlette.responses import JSONResponse

TOKEN_ENV = "AXONRELAY_MCP_TOKEN"

logger = logging.getLogger(__name__)


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

        if _credentials_enforced():
            # The credential supersedes the shared secret (module docstring).
            admitted = _is_a_valid_credential(presented)
        else:
            admitted = presented.startswith(b"Bearer ") and hmac.compare_digest(presented[7:].strip(), self._token)
        if admitted:
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {"error": "unauthorized", "detail": f"This endpoint requires a bearer token ({TOKEN_ENV})."},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="axonrelay"'},
        )
        await response(scope, receive, send)


def _credentials_enforced() -> bool:
    from app import authz

    return authz.require_auth()


def _is_a_valid_credential(presented: bytes) -> bool:
    """Does this header carry a live credential?

    Imported lazily: `app.authz` pulls in the models and the engine, and this
    module is deliberately importable on its own.
    """
    from app import authz
    from app.database import SessionLocal

    token = authz.bearer_token(presented.decode("latin-1", "replace"))
    if not token:
        return False
    db = SessionLocal()
    try:
        return authz.authenticate(db, token) is not None
    finally:
        db.close()


def wrap_if_configured(app):
    """Return ``app`` as-is, or wrapped in the token check when the env var is set."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        return app
    if _credentials_enforced():
        logger.warning(
            "%s is set but per-caller authentication is enforced: only credentials are admitted, "
            "and the shared token opens nothing",
            TOKEN_ENV,
        )
    return BearerTokenMiddleware(app, token)
