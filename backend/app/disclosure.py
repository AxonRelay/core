"""What a shared boundary is allowed to say about the coordination plane (issue #26).

The board's job is to stop two agents editing the same files. Doing that job
needs far less than the board currently records: a host name, an absolute clone
path, a git directory, and free-form focus, reason, subject, body and
acknowledgement text. On a personal instance that detail is what makes the
board readable. On a shared one it is a description of somebody's machine and,
in the free text, sometimes of the work itself.

So every coordination response passes through here, and what it may contain
depends on the mode:

* **Full-text mode** (the default, the personal PoC): everything, as before.
  The instance holds whatever was sent to it and says so in SECURITY.md.
* **Safe Envelope mode** (`AXONRELAY_SAFE_MODE`, ADR-010): opaque identifiers,
  enums and codes, timestamps and counts. No host, no absolute path, no git
  directory, no arbitrary text, no unrestricted URL. A repository slug is a
  public identifier and is disclosed only under the same policy the envelope
  uses for one (`AXONRELAY_SAFE_PUBLIC_IDENTIFIERS`).

The allowlists below are the policy, not a summary of it: `*_FIELDS` is what a
view may emit, and a test asserts each view's keys equal its allowlist, so a
field added to a serializer without a decision fails rather than leaks.

Two rules do **not** depend on the mode, because they are about secrets rather
than about disclosure preference:

* An agent's `config` blob is where an operator puts credentials, so no
  serializer ever emits its values - only its key names.
* A URL that carries credentials, a query string, a fragment, or a scheme
  other than http/https is refused rather than stored (`sanitize_url`).
"""

from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session as DBSession

from app import models, safe_envelope

#: Bytes of randomness in an opaque identifier. Defined on the models, which
#: default every opaque_id column to one, and mirrored by migration 012.
OPAQUE_ID_BYTES = models.OPAQUE_ID_BYTES

#: Schemes a stored link may use. Anything else - file:, javascript:, ssh: -
#: either reaches outside the browser's trust model or names a local path.
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


def safe_mode() -> bool:
    """Is the content-blind mode on? One source of truth, shared with ADR-010."""
    return safe_envelope.safe_mode()


def public_identifiers_allowed() -> bool:
    return safe_envelope.public_identifiers_allowed()


def new_opaque_id() -> str:
    return secrets.token_urlsafe(OPAQUE_ID_BYTES)


def ensure_opaque_id(db: DBSession, row) -> str:
    """The row's opaque id, minting one if it has none.

    New rows get one from the column default and migration 012 backfilled the
    old ones, so this is the belt to that pair of braces: a row inserted by raw
    SQL, or one built in a test before the default applied, still gets an id
    rather than serialising as ``null``.
    """
    if not row.opaque_id:
        row.opaque_id = new_opaque_id()
        db.flush()
    return row.opaque_id


class UnsafeURL(ValueError):
    """A URL that must not be stored. The message names the problem, never the URL."""


def sanitize_url(url: str | None) -> str | None:
    """Return ``url`` if it is safe to store, else raise :class:`UnsafeURL`.

    Refused, and why:

    * a scheme outside http/https - `file:` and `ssh:` name local resources,
      `javascript:` is executable, and none of them belong in a shared record;
    * userinfo (`https://user:token@host/...`) - the commonest way a credential
      ends up in a link;
    * a query string or a fragment - both routinely carry tokens, session ids
      and document positions, and neither identifies the resource itself.

    The refusal never repeats the value: a URL that must not be stored must not
    be echoed either.
    """
    if url is None:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise UnsafeURL("only http and https URLs may be stored")
    if "@" in parts.netloc:
        raise UnsafeURL("a URL carrying credentials may not be stored")
    if parts.query:
        raise UnsafeURL("a URL query string may not be stored; strip it first")
    if parts.fragment:
        raise UnsafeURL("a URL fragment may not be stored; strip it first")
    if not parts.netloc:
        raise UnsafeURL("a URL must name a host")
    return url.strip()


def config_keys(config: Any) -> list[str] | None:
    """The key names of an agent config, never its values.

    A config blob is where an operator puts an API key. Its *shape* is useful
    to a reader ("this agent is configured for a model and a temperature");
    its contents are a secret store, and a serializer is not the place to open
    one.
    """
    if config is None:
        return None
    if isinstance(config, dict):
        return sorted(str(k) for k in config)
    return []


# --------------------------------------------------------------- allowlists

ACTOR_FIELDS = ("id", "type", "name")
ACTOR_SAFE_FIELDS = ("id", "type", "actor_ref")

WORKSPACE_FIELDS = ("id", "host", "repo", "clone_path", "git_dir", "label")
WORKSPACE_SAFE_FIELDS = ("id", "workspace_ref", "repo")

SESSION_FIELDS = (
    "session_id",
    "actor_id",
    "actor",
    "workspace",
    "branch",
    "focus",
    "focus_code",
    "status",
    "started_at",
    "last_heartbeat_at",
    "ended_at",
)
SESSION_SAFE_FIELDS = (
    "session_id",
    "actor_id",
    "actor",
    "workspace",
    "focus_code",
    "status",
    "started_at",
    "last_heartbeat_at",
    "ended_at",
)

CLAIM_FIELDS = (
    "claim_id",
    "session_id",
    "repo",
    "paths",
    "resource",
    "mode",
    "reason",
    "reason_code",
    "status",
    "forced_over",
    "created_at",
    "expires_at",
    "released_at",
)
CLAIM_SAFE_FIELDS = (
    "claim_id",
    "session_id",
    "repo",
    "path_count",
    "resource",
    "mode",
    "reason_code",
    "status",
    "forced",
    "created_at",
    "expires_at",
    "released_at",
)

RELAY_FIELDS = (
    "relay_id",
    "kind",
    "subject",
    "body",
    "code",
    "from_session_id",
    "from_actor_id",
    "to_actor_id",
    "to_workspace_id",
    "to_repo",
    "in_reply_to_id",
    "created_at",
)
BOARD_SESSION_FIELDS = (
    "session_id",
    "actor",
    "actor_type",
    "host",
    "repo",
    "clone_path",
    "branch",
    "focus",
    "focus_code",
    "stale",
    "last_heartbeat_at",
)
BOARD_SESSION_SAFE_FIELDS = (
    "session_id",
    "actor_ref",
    "actor_type",
    "repo",
    "focus_code",
    "stale",
    "last_heartbeat_at",
)

HOLDER_FIELDS = ("session_id", "actor", "host", "clone_path", "git_dir", "branch", "focus", "focus_code", "stale")
HOLDER_SAFE_FIELDS = ("session_id", "actor_ref", "workspace_ref", "focus_code", "stale")

INBOX_FIELDS = ("relay_id", "kind", "code", "subject", "body", "in_reply_to_id", "created_at", "from", "acked")
INBOX_SAFE_FIELDS = ("relay_id", "kind", "code", "in_reply_to_id", "created_at", "from", "acked")

OPEN_RELAY_FIELDS = (
    "relay_id",
    "kind",
    "code",
    "subject",
    "to_repo",
    "to_actor_id",
    "to_workspace_id",
    "from_actor",
    "from_actor_id",
    "created_at",
)
OPEN_RELAY_SAFE_FIELDS = ("relay_id", "kind", "code", "to_actor_id", "to_workspace_id", "from_actor_id", "created_at")

BOARD_CLAIM_FIELDS = (*(), "holder")  # claim_view's fields plus a holder; asserted as a superset in tests

GUARD_CALLER_FIELDS = ("host", "clone_path", "repo", "registered")
GUARD_CALLER_SAFE_FIELDS = ("workspace_ref", "repo", "registered")

CONFLICT_FIELDS = (
    "claim_id",
    "session_id",
    "mode",
    "paths",
    "overlapping_paths",
    "resource",
    "reason",
    "reason_code",
    "expires_at",
    "holder",
)
CONFLICT_SAFE_FIELDS = (
    "claim_id",
    "session_id",
    "mode",
    "overlap_count",
    "resource",
    "reason_code",
    "expires_at",
    "holder",
)

INBOX_FROM_FIELDS = ("session_id", "actor", "host", "repo", "clone_path")
INBOX_FROM_SAFE_FIELDS = ("session_id", "actor_ref", "workspace_ref", "repo")

RELAY_SAFE_FIELDS = (
    "relay_id",
    "kind",
    "code",
    "from_session_id",
    "from_actor_id",
    "to_actor_id",
    "to_workspace_id",
    "in_reply_to_id",
    "created_at",
)


def _ref(row) -> str | None:
    """A row's opaque reference, minted in memory if the row somehow lacks one.

    Returning ``None`` here would quietly turn the pseudonym scheme off for
    that row, which is the failure mode hardest to notice: every response
    still renders, and every reference is null.
    """
    if row is None:
        return None
    if not row.opaque_id:
        row.opaque_id = new_opaque_id()
    return row.opaque_id


def _repo(value: str | None) -> str | None:
    """A repository slug is a public identifier; disclose it under that policy only."""
    if not safe_mode() or public_identifiers_allowed():
        return value
    return None


# -------------------------------------------------------------------- views


def actor_view(actor: models.Actor | None) -> dict | None:
    if not actor:
        return None
    if not safe_mode():
        return {"id": actor.id, "type": str(actor.type), "name": actor.name}
    return {"id": actor.id, "type": str(actor.type), "actor_ref": _ref(actor)}


def workspace_view(workspace: models.Workspace | None) -> dict | None:
    if not workspace:
        return None
    if not safe_mode():
        return {
            "id": workspace.id,
            "host": workspace.host,
            "repo": workspace.repo,
            "clone_path": workspace.clone_path,
            "git_dir": workspace.git_dir,
            "label": workspace.label,
        }
    return {"id": workspace.id, "workspace_ref": _ref(workspace), "repo": _repo(workspace.repo)}


def session_view(session: models.Session) -> dict:
    common = {
        "session_id": session.id,
        "actor_id": session.actor_id,
        "actor": actor_view(session.actor),
        "workspace": workspace_view(session.workspace),
        "focus_code": str(session.focus_code) if session.focus_code else None,
        "status": str(session.status),
        "started_at": session.started_at.isoformat(),
        "last_heartbeat_at": session.last_heartbeat_at.isoformat(),
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
    }
    if safe_mode():
        return common
    # A branch name is a working detail, not a secret, and the board is much
    # harder to read without it - so it stays in full-text mode only.
    return {**common, "branch": session.branch, "focus": session.focus}


def claim_view(claim: models.Claim) -> dict:
    common = {
        "claim_id": claim.id,
        "session_id": claim.session_id,
        "resource": str(claim.resource) if claim.resource else None,
        "mode": str(claim.mode),
        "reason_code": str(claim.reason_code) if claim.reason_code else None,
        "status": str(claim.status),
        "created_at": claim.created_at.isoformat(),
        "expires_at": claim.expires_at.isoformat(),
        "released_at": claim.released_at.isoformat() if claim.released_at else None,
    }
    if safe_mode():
        # Paths are the caller's own input and describe a filesystem; the count
        # is what a peer needs to size the overlap it was already told about.
        # `forced_over` quotes the displaced claims, so it collapses to a flag.
        return {
            **common,
            "repo": _repo(claim.repo),
            "path_count": len(claim.paths or []),
            "forced": bool(claim.forced_over),
        }
    return {
        **common,
        "repo": claim.repo,
        "paths": list(claim.paths or []),
        "reason": claim.reason,
        "forced_over": claim.forced_over,
    }


def relay_view(relay: models.Relay) -> dict:
    common = {
        "relay_id": relay.id,
        "kind": str(relay.kind),
        "code": str(relay.code) if relay.code else None,
        "from_session_id": relay.from_session_id,
        "from_actor_id": relay.from_actor_id,
        "to_actor_id": relay.to_actor_id,
        "to_workspace_id": relay.to_workspace_id,
        "in_reply_to_id": relay.in_reply_to_id,
        "created_at": relay.created_at.isoformat(),
    }
    if safe_mode():
        return common
    return {**common, "subject": relay.subject, "body": relay.body, "to_repo": relay.to_repo}


def board_session_view(session: models.Session, *, stale: bool) -> dict:
    """One row of the board's "who is active" list.

    The board is the read a peer starts its turn with, so it is the response
    most likely to be looked at by somebody who is not the operator — and the
    one that named a host, a clone path and a focus sentence in one line.
    """
    common = {
        "session_id": session.id,
        "actor_type": str(session.actor.type) if session.actor else None,
        "focus_code": str(session.focus_code) if session.focus_code else None,
        "stale": stale,
        "last_heartbeat_at": session.last_heartbeat_at.isoformat(),
    }
    if safe_mode():
        return {
            **common,
            "actor_ref": _ref(session.actor),
            "repo": _repo(session.workspace.repo if session.workspace else None),
        }
    workspace = session.workspace
    return {
        **common,
        "actor": session.actor.name if session.actor else None,
        "host": workspace.host if workspace else None,
        "repo": workspace.repo if workspace else None,
        "clone_path": workspace.clone_path if workspace else None,
        "branch": session.branch,
        "focus": session.focus,
    }


def holder_view(session: models.Session | None, *, stale: bool) -> dict | None:
    """Who holds a claim.

    In full-text mode this is deliberately findable — the point is that a peer
    can go and talk to them. In safe mode the same purpose is served by an
    opaque reference plus a relay: you can reach the holder without being told
    which machine they are on.
    """
    if not session:
        return None
    common = {
        "session_id": session.id,
        "focus_code": str(session.focus_code) if session.focus_code else None,
        "stale": stale,
    }
    if safe_mode():
        return {
            **common,
            "actor_ref": _ref(session.actor),
            "workspace_ref": _ref(session.workspace),
        }
    workspace = session.workspace
    return {
        **common,
        "actor": session.actor.name if session.actor else None,
        "host": workspace.host if workspace else None,
        "clone_path": workspace.clone_path if workspace else None,
        "git_dir": workspace.git_dir if workspace else None,
        "branch": session.branch,
        "focus": session.focus,
    }


def inbox_entry_view(relay: models.Relay, receipt: models.RelayReceipt | None) -> dict:
    """One inbox row: the message plus this recipient's state on it."""
    sender = relay.from_session
    sender_workspace = sender.workspace if sender else None
    common = {
        "relay_id": relay.id,
        "kind": str(relay.kind),
        "code": str(relay.code) if relay.code else None,
        "in_reply_to_id": relay.in_reply_to_id,
        "created_at": relay.created_at.isoformat(),
        "acked": bool(receipt and receipt.acked_at),
    }
    if safe_mode():
        return {
            **common,
            "from": {
                "session_id": relay.from_session_id,
                "actor_ref": _ref(relay.from_actor),
                "workspace_ref": _ref(sender_workspace),
                "repo": _repo(sender_workspace.repo if sender_workspace else None),
            },
        }
    return {
        **common,
        "subject": relay.subject,
        "body": relay.body,
        "from": {
            "session_id": relay.from_session_id,
            "actor": relay.from_actor.name if relay.from_actor else None,
            "host": sender_workspace.host if sender_workspace else None,
            "repo": sender_workspace.repo if sender_workspace else None,
            "clone_path": sender_workspace.clone_path if sender_workspace else None,
        },
    }


def board_claim_view(claim: models.Claim, *, holder: dict | None) -> dict:
    """A board claim row: the claim's own view plus who holds it."""
    return {**claim_view(claim), "holder": holder}


def open_relay_view(relay: models.Relay) -> dict:
    """A board row for a relay nobody has acknowledged yet.

    Narrower than :func:`relay_view` on purpose. The board lists open relays
    for *everyone*, not only for their addressee, so this row is read by peers
    the message is not for - it may say that something is waiting, and who for,
    but never what it says. (It carried `subject` before the coordination
    layer had a shared policy; routing it through the full relay view would
    have widened it to the body.)
    """
    common = {
        "relay_id": relay.id,
        "kind": str(relay.kind),
        "code": str(relay.code) if relay.code else None,
        "to_actor_id": relay.to_actor_id,
        "to_workspace_id": relay.to_workspace_id,
        "from_actor_id": relay.from_actor_id,
        "created_at": relay.created_at.isoformat(),
    }
    if safe_mode():
        return common
    return {
        **common,
        "subject": relay.subject,
        "to_repo": relay.to_repo,
        "from_actor": relay.from_actor.name if relay.from_actor else None,
    }


def guard_caller_view(*, host: str, clone_path: str, repo: str | None, workspace: models.Workspace | None) -> dict:
    """Who asked the git guard, echoed back.

    The wrapper sends its own host and path, so echoing them tells the caller
    nothing it did not already know - but the response is a record like any
    other, and in safe mode it says which checkout asked without describing it.
    """
    if safe_mode():
        return {
            "workspace_ref": _ref(workspace),
            "repo": _repo(repo),
            "registered": workspace is not None,
        }
    return {"host": host, "clone_path": clone_path, "repo": repo, "registered": workspace is not None}


def conflict_view(
    claim: models.Claim,
    *,
    holder: dict | None,
    overlapping_paths: list[str] | None = None,
) -> dict:
    """Why a claim was refused.

    A refusal has to be actionable — you need to know whom to talk to and how
    much of your intended work is already taken. In safe mode that is the
    holder's reference and the size of the overlap; the paths are your own
    input, so being told them back adds nothing and describes a filesystem.
    """
    common = {
        "claim_id": claim.id,
        "session_id": claim.session_id,
        "mode": str(claim.mode),
        "resource": str(claim.resource) if claim.resource else None,
        "reason_code": str(claim.reason_code) if claim.reason_code else None,
        "expires_at": claim.expires_at.isoformat(),
        "holder": holder,
    }
    if safe_mode():
        return {**common, "overlap_count": len(overlapping_paths or [])}
    return {
        **common,
        "paths": list(claim.paths or []),
        "overlapping_paths": sorted(overlapping_paths or []),
        "reason": claim.reason,
    }
