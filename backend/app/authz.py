"""Caller identity and scoped authorization, shared by MCP and REST (issue #27).

Network isolation and the optional shared bearer token (ADR-008) decide *who
can reach* the process. Neither answers **which caller** performed an action or
**which operations** that caller may perform. This module adds that layer, and
both interfaces consult it — one policy, two presentation layers, the rule the
projection service already follows.

Three ideas, and the reasons they are shaped this way:

* **A credential names an Actor.** The recorded Actor comes from the
  credential, never from a request parameter, so a caller cannot write another
  agent's name into `register_session` and have the board believe it. Where a
  surface still takes an actor name, an authenticated call must either omit it
  or repeat its own; anything else is refused.
* **A credential carries scopes.** Six to start (the issue's list), coarse
  enough to hand out honestly: reading the ledger, writing to it, reading the
  coordination board, writing to it, reading exports, and administration
  (the destructive operations). Every tool and every REST route is mapped to
  exactly one of them in the tables below, and a structural test fails if a new
  surface is added without a mapping — the default cannot be "open".
* **Tokens are never stored, echoed or logged.** A credential row holds only
  the SHA-256 of its token; the token itself exists once, in the output of
  `python -m app.credentials issue`. Lookup is by digest, so there is no scan
  and no plaintext to leak; the digest comparison is constant-time anyway.
  Refusals name the scope that was required and nothing about the resource.

**When enforcement is on.** `AXONRELAY_REQUIRE_AUTH=1` turns it on for every
HTTP caller — REST and the Streamable HTTP MCP transport. Unset, the process
behaves exactly as before and every call runs as the loopback principal (the
operator's human Actor, all scopes), which is the same trust assumption the
personal PoC has always made and which SECURITY.md states. This is the pattern
ADR-008 set: an optional layer that changes nothing until you ask for it.

**stdio is always loopback.** A stdio transport has no HTTP request and no
headers; the caller is a process the operator started on their own machine, so
it runs as the loopback principal even with enforcement on. That is a trust
assumption, not an oversight, and ADR-011 records it.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import logging
import os
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app import models

logger = logging.getLogger("axonrelay.authz")

REQUIRE_AUTH_ENV = "AXONRELAY_REQUIRE_AUTH"
#: Bytes of entropy in an issued token. 32 bytes is well past the point where
#: guessing is the attack; it is why a plain digest (not a password KDF) is the
#: right storage for it.
TOKEN_BYTES = 32


class Scope(enum.StrEnum):
    """What a credential may do. Coarse on purpose: a scope you cannot explain is a scope you cannot grant."""

    LEDGER_READ = "ledger:read"
    LEDGER_WRITE = "ledger:write"
    COORDINATION_READ = "coordination:read"
    COORDINATION_WRITE = "coordination:write"
    #: Reserved for the public-safe aggregate exporter (#28). Declared now so
    #: credentials issued today can carry it; no surface maps to it yet.
    EXPORT_READ = "export:read"
    #: Destructive operations — deleting an agent, a task or an assignment.
    #: Deleting an Actor can NULL a hashed reviewer reference on old approval
    #: rows, so this is a ledger-affecting power, not housekeeping.
    ADMINISTRATION = "administration"


ALL_SCOPES = frozenset(Scope)


class AuthzError(Exception):
    """Base for refusals. Their messages name scopes, never resources or values."""


class Unauthenticated(AuthzError):
    """No usable credential was presented."""

    def __init__(self, reason: str = "a credential is required") -> None:
        super().__init__(reason)


class Forbidden(AuthzError):
    """A valid credential without the scope the operation needs."""

    def __init__(self, scope: Scope) -> None:
        self.scope = scope
        super().__init__(f"this credential does not carry the '{scope.value}' scope")


class ActorMismatch(AuthzError):
    """A valid credential asking to act as, or on behalf of, a different Actor.

    Separate from :class:`Forbidden` on purpose: naming a scope here would tell
    a caller that already holds that scope it does not, and would hide that the
    argument, not the grant, is the problem.
    """


class ApprovalNotPermitted(AuthzError):
    """A credential that may write to the ledger but may not approve *this* task."""


@dataclass(frozen=True)
class Principal:
    """Who is calling, resolved server-side."""

    actor_id: int | None
    actor_name: str
    scopes: frozenset[Scope]
    #: "credential" when a token was presented and matched; "loopback" for
    #: stdio and for an instance that has not turned enforcement on.
    source: str
    credential_id: int | None = None

    def has(self, scope: Scope) -> bool:
        return scope in self.scopes

    def require(self, scope: Scope) -> None:
        if not self.has(scope):
            logger.info("authz denied scope=%s source=%s", scope.value, self.source)
            raise Forbidden(scope)


_current: ContextVar[Principal | None] = ContextVar("axonrelay_principal", default=None)


def require_auth() -> bool:
    """Is per-caller authentication enforced on HTTP transports? Read at call time."""
    return os.environ.get(REQUIRE_AUTH_ENV, "").strip().lower() in {"1", "true", "yes"}


def loopback_principal(db: Session) -> Principal:
    """The operator on their own machine: the human Actor, every scope.

    Used for stdio, and for HTTP when enforcement is off. Named so that a
    reader of a log line or an approval row can tell it apart from a
    credentialled call.
    """
    actor = db.query(models.Actor).filter(models.Actor.type == models.ActorTypeEnum.HUMAN).first()
    return Principal(
        actor_id=actor.id if actor else None,
        actor_name=actor.name if actor else "self",
        scopes=ALL_SCOPES,
        source="loopback",
    )


# --------------------------------------------------------------- credentials


def issue_token() -> str:
    """A new credential token. Returned once; only its digest is ever stored."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def bearer_token(authorization: str | None) -> str | None:
    """The token out of an ``Authorization: Bearer <token>`` header, or None."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def parse_scopes(raw: str | None) -> frozenset[Scope]:
    """Scopes as stored on a credential row. An unknown name is dropped, not guessed."""
    scopes = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            scopes.add(Scope(part))
        except ValueError:
            logger.warning("credential carries an unknown scope name; ignoring it")
    return frozenset(scopes)


def format_scopes(scopes: frozenset[Scope] | set[Scope] | list[Scope]) -> str:
    return ",".join(sorted(s.value for s in scopes))


def authenticate(db: Session, token: str | None) -> Principal | None:
    """Resolve a presented token to a Principal, or None.

    Lookup is by digest — the plaintext never reaches a query, a log or an
    error. The extra `compare_digest` costs nothing and keeps the comparison
    constant-time even if the index ever became a prefix scan.
    """
    if not token:
        return None
    digest = token_digest(token)
    credential = (
        db.query(models.Credential)
        .filter(models.Credential.token_hash == digest, models.Credential.revoked_at.is_(None))
        .first()
    )
    if credential is None or not hmac.compare_digest(credential.token_hash, digest):
        return None
    credential.last_used_at = datetime.utcnow()
    db.commit()
    actor = db.query(models.Actor).filter(models.Actor.id == credential.actor_id).first()
    if actor is None:
        # The Actor was deleted; the credential names nobody and is not usable.
        return None
    return Principal(
        actor_id=actor.id,
        actor_name=actor.name,
        scopes=parse_scopes(credential.scopes),
        source="credential",
        credential_id=credential.id,
    )


def resolve(db: Session, *, authorization: str | None, transport_is_local: bool) -> Principal:
    """The principal for one inbound call, or raise :class:`Unauthenticated`.

    ``transport_is_local`` is true for stdio, which has no request to carry a
    header and is the operator's own process.
    """
    if transport_is_local or not require_auth():
        return loopback_principal(db)
    principal = authenticate(db, bearer_token(authorization))
    if principal is None:
        logger.info("authz rejected an HTTP call: no valid credential")
        raise Unauthenticated()
    return principal


# ------------------------------------------------------------------- context


def current() -> Principal:
    """The principal of the call in flight.

    Set by the REST dependency and the MCP middleware around the handler, so a
    surface deep in the call stack can bind the acting Actor without every
    function threading it through. Outside a call this raises rather than
    inventing an identity.
    """
    principal = _current.get()
    if principal is None:  # pragma: no cover - a programming error, not a runtime path
        raise Unauthenticated("no principal is bound to this call")
    return principal


def bind(principal: Principal):
    """Context manager binding ``principal`` for the duration of one call."""

    class _Bound:
        def __enter__(self) -> Principal:
            self._token = _current.set(principal)
            return principal

        def __exit__(self, *exc: Any) -> None:
            _current.reset(self._token)

    return _Bound()


def acting_actor_id(db: Session) -> int | None:
    """The Actor an operation should be recorded against.

    The credential's Actor when one was presented; otherwise the operator's
    human Actor. Never a value the caller supplied.
    """
    principal = _current.get()
    if principal is not None and principal.actor_id is not None:
        return principal.actor_id
    actor = db.query(models.Actor).filter(models.Actor.type == models.ActorTypeEnum.HUMAN).first()
    return actor.id if actor else None


def check_claimed_actor(name: str | None) -> None:
    """Refuse a call that claims an Actor other than the credential's.

    Surfaces such as ``register_session`` take an actor name. Under a
    credential the name is decided server-side; passing a different one is a
    request to act as somebody else and is refused rather than ignored, so a
    caller cannot believe it succeeded as that actor.
    """
    principal = _current.get()
    if principal is None or principal.source != "credential" or name is None:
        return
    if name != principal.actor_name:
        logger.info("authz refused an actor claim that does not match the credential")
        raise ActorMismatch("this credential acts as its own Actor; omit the name or pass that Actor's name")


def check_session_owner(db: Session, session_id: int | None) -> None:
    """Refuse a call that drives another Actor's coordination session.

    Every coordination surface addresses a session by id, and an id is a
    request parameter: without this, a `coordination:write` credential could
    send relays as another agent, drop its territory claims or end its session,
    and a `coordination:read` one could read an inbox — which marks the
    owner's relays read and so is a write in disguise.

    A loopback caller is the operator's own process, which legitimately drives
    every session on the machine, so the check applies to credentials only.
    """
    principal = _current.get()
    if principal is None or principal.source != "credential" or session_id is None:
        return
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if session is None:
        # Not this layer's 404 to raise; the surface reports a missing session.
        return
    if session.actor_id != principal.actor_id:
        logger.info("authz refused a session that belongs to another actor")
        raise ActorMismatch("this session belongs to another Actor")


def check_may_approve(db: Session, task_id: int) -> None:
    """May this caller record an approval or rejection on this task?

    `ledger:write` covers creating and running tasks as well as deciding on
    them, so on its own it would let an agent credential approve the draft it
    just produced and be recorded as the reviewer — the one thing the ledger
    exists to make legible. So a credential may decide on a task when either:

    * its Actor holds the `approver` assignment on that task (the data model
      already says who may approve; nothing enforced it until now), or
    * its Actor is a human — the operator, who approves by definition.

    A loopback caller is the operator and is unaffected, so the personal PoC
    behaves as before.
    """
    principal = _current.get()
    if principal is None or principal.source != "credential" or principal.actor_id is None:
        return
    assigned = (
        db.query(models.TaskAssignment)
        .filter(
            models.TaskAssignment.task_id == task_id,
            models.TaskAssignment.actor_id == principal.actor_id,
            models.TaskAssignment.role == models.AssignmentRoleEnum.APPROVER,
        )
        .first()
    )
    if assigned is not None:
        return
    actor = db.query(models.Actor).filter(models.Actor.id == principal.actor_id).first()
    if actor is not None and actor.type == models.ActorTypeEnum.HUMAN:
        return
    logger.info("authz refused an approval by an actor with no approver assignment")
    raise ApprovalNotPermitted("this Actor is not an approver on this task; assign it the 'approver' role first")


# ------------------------------------------------------- the surface → scope maps

#: Every MCP tool, mapped to the one scope it needs. A tool missing from this
#: table is a test failure, not an open door.
TOOL_SCOPES: dict[str, Scope] = {
    # Ledger, read
    "list_tasks": Scope.LEDGER_READ,
    "list_pending_approvals": Scope.LEDGER_READ,
    "get_task": Scope.LEDGER_READ,
    "get_drafts": Scope.LEDGER_READ,
    "verify_task_ledger": Scope.LEDGER_READ,
    "list_agents": Scope.LEDGER_READ,
    "get_self_actor": Scope.LEDGER_READ,
    # Ledger, write
    "create_task": Scope.LEDGER_WRITE,
    "run_task": Scope.LEDGER_WRITE,
    "approve_task": Scope.LEDGER_WRITE,
    "reject_task": Scope.LEDGER_WRITE,
    "review_pending_task": Scope.LEDGER_WRITE,
    "create_agent": Scope.LEDGER_WRITE,
    "update_agent": Scope.LEDGER_WRITE,
    # Coordination, read
    "get_board": Scope.COORDINATION_READ,
    "read_inbox": Scope.COORDINATION_READ,
    "check_conflicts": Scope.COORDINATION_READ,
    "check_git_resource": Scope.COORDINATION_READ,
    "list_safe_events": Scope.COORDINATION_READ,
    # Coordination, write
    "register_session": Scope.COORDINATION_WRITE,
    "heartbeat_session": Scope.COORDINATION_WRITE,
    "end_session": Scope.COORDINATION_WRITE,
    "claim_territory": Scope.COORDINATION_WRITE,
    "release_territory": Scope.COORDINATION_WRITE,
    "claim_git_resource": Scope.COORDINATION_WRITE,
    "send_relay": Scope.COORDINATION_WRITE,
    "ack_relay": Scope.COORDINATION_WRITE,
    "ingest_safe_envelope": Scope.COORDINATION_WRITE,
}

#: MCP resources are ambient read-only context; they read the same data the
#: read tools do and take the same scope.
RESOURCE_SCOPES: dict[str, Scope] = {
    "axonrelay://board": Scope.COORDINATION_READ,
    "axonrelay://tasks/{task_id}": Scope.LEDGER_READ,
    "axonrelay://tasks/{task_id}/drafts/{version}": Scope.LEDGER_READ,
}

#: Routes that answer without a credential at all.
#:
#: The health check, because a liveness probe that needs a secret is a
#: liveness probe that stops working. And FastAPI's own schema routes: they
#: describe the API's shape, which this repository publishes anyway, and they
#: are Starlette routes rather than `APIRoute`s so the application-wide
#: dependency never sees them — naming them here makes that a decision rather
#: than an accident, and the structural test checks the whole route table, not
#: just the API ones.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/"),
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/redoc"),
    }
)

#: Every other REST route, by (method, path template). ``None`` means the route
#: needs an authenticated caller but no particular scope; a route in neither
#: table is a test failure.
ROUTE_SCOPES: dict[tuple[str, str], Scope | None] = {
    ("GET", "/actors"): Scope.LEDGER_READ,
    ("GET", "/actors/me"): None,
    ("GET", "/actors/{actor_id}"): Scope.LEDGER_READ,
    ("GET", "/agents"): Scope.LEDGER_READ,
    ("POST", "/agents"): Scope.LEDGER_WRITE,
    ("GET", "/agents/{agent_id}"): Scope.LEDGER_READ,
    ("PUT", "/agents/{agent_id}"): Scope.LEDGER_WRITE,
    ("DELETE", "/agents/{agent_id}"): Scope.ADMINISTRATION,
    ("GET", "/tasks"): Scope.LEDGER_READ,
    ("POST", "/tasks"): Scope.LEDGER_WRITE,
    ("GET", "/tasks/pending/approvals"): Scope.LEDGER_READ,
    ("GET", "/tasks/{task_id}"): Scope.LEDGER_READ,
    ("PUT", "/tasks/{task_id}"): Scope.LEDGER_WRITE,
    ("DELETE", "/tasks/{task_id}"): Scope.ADMINISTRATION,
    ("POST", "/tasks/{task_id}/run"): Scope.LEDGER_WRITE,
    ("POST", "/tasks/{task_id}/approve"): Scope.LEDGER_WRITE,
    ("POST", "/tasks/{task_id}/reject"): Scope.LEDGER_WRITE,
    ("GET", "/tasks/{task_id}/drafts"): Scope.LEDGER_READ,
    ("GET", "/tasks/{task_id}/approvals"): Scope.LEDGER_READ,
    ("GET", "/tasks/{task_id}/ledger/verify"): Scope.LEDGER_READ,
    ("GET", "/tasks/{task_id}/assignments"): Scope.LEDGER_READ,
    ("POST", "/tasks/{task_id}/assignments"): Scope.LEDGER_WRITE,
    ("DELETE", "/tasks/{task_id}/assignments/{actor_id}"): Scope.ADMINISTRATION,
    ("GET", "/coordination/board"): Scope.COORDINATION_READ,
    ("GET", "/coordination/sessions"): Scope.COORDINATION_READ,
    ("GET", "/coordination/claims"): Scope.COORDINATION_READ,
    ("GET", "/coordination/git/guard"): Scope.COORDINATION_READ,
    ("GET", "/coordination/sessions/{session_id}/inbox"): Scope.COORDINATION_READ,
    ("POST", "/envelopes"): Scope.COORDINATION_WRITE,
    ("GET", "/envelopes"): Scope.COORDINATION_READ,
}
