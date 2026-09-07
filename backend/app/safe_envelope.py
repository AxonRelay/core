"""Safe Envelope: content-blind, metadata-only ingestion (issue #25).

A shared AxonRelay instance must be able to coordinate work **without ever
receiving the work product**. Redacting after receipt is not good enough — the
core has already touched the data — so this module defines a mode in which the
only thing a producer can send is a strictly allowlisted, versioned envelope:

* opaque identifiers for the event, actor, repository, workspace and artifact;
* an action and an outcome from closed enumerations;
* a **source-produced** artifact commitment (the producer hashes the artifact;
  the core receives only the digest and never recomputes it);
* timestamps and schema / policy versions;
* an identifier-policy classification and an optional producer signature.

Anything else — a title, a description, a prompt, a draft, feedback, a relay
body, command output, file contents, a URL — has nowhere to go: the schema
forbids unknown fields and every declared field is a bounded, pattern-checked
scalar. Rejections name **field names only**, never values, in errors and in
logs, so a rejected value cannot leak through the refusal either.

Two things decide what a deployment accepts:

* ``AXONRELAY_SAFE_MODE`` — the explicit, opt-in switch. When set, every
  surface that accepts free text (task / draft / approval / agent / coordination
  writes over MCP and REST) refuses with a fixed, value-free message, and the
  envelope endpoint is the only write path. When unset nothing changes and the
  full-text local PoC keeps working; it is labelled as such in the docs.
* ``AXONRELAY_SAFE_PUBLIC_IDENTIFIERS`` — policy for envelopes whose
  ``identifier_policy`` is ``public``. Public identifiers (``owner/repo``
  slugs) are accepted only when this is set; otherwise every identifier must
  be opaque (an unguessable token with no slashes, dots or colons, so a path,
  URL or slug cannot be smuggled in as an id).

Envelope validation (the schema) is enforced regardless of mode — it is what
makes the envelope endpoint safe to expose at all. The mode switch governs the
*other* surfaces.

The MCP tool and the REST endpoint are thin wrappers over :func:`ingest`, so
the two cannot drift (the same rule the projection service follows).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from sqlalchemy.orm import Session

from app import models

logger = logging.getLogger("axonrelay.safe_envelope")

SAFE_MODE_ENV = "AXONRELAY_SAFE_MODE"
PUBLIC_IDENTIFIERS_ENV = "AXONRELAY_SAFE_PUBLIC_IDENTIFIERS"

SCHEMA_VERSION = 1
#: Only commitment algorithm the envelope admits today. Same label as
#: app.ledger.COMMITMENT_ALGORITHM so a draft commitment and an envelope
#: commitment are comparable.
COMMITMENT_ALGORITHMS = ("sha256-utf8-v1",)

#: Message every refused free-text surface returns. Fixed text: it must never
#: interpolate anything the caller sent.
SAFE_MODE_REFUSAL = (
    "Safe Envelope mode is enabled on this instance: this operation accepts free text and is disabled. "
    "Send a Safe Envelope (POST /envelopes or the ingest_safe_envelope tool) instead."
)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def safe_mode() -> bool:
    """Is the content-blind mode switched on? Read at call time, like the other flags."""
    return _truthy(SAFE_MODE_ENV)


def public_identifiers_allowed() -> bool:
    return _truthy(PUBLIC_IDENTIFIERS_ENV)


class SafeModeRefused(PermissionError):
    """Raised by a free-text surface when the instance runs in Safe Envelope mode."""

    def __init__(self) -> None:
        super().__init__(SAFE_MODE_REFUSAL)


def refuse_free_text_if_safe_mode() -> None:
    """Guard for every surface that would accept arbitrary text. Value-free by construction."""
    if safe_mode():
        raise SafeModeRefused()


# --------------------------------------------------------------------------- schema

# An opaque identifier: URL-safe token, long enough not to be a word, and with
# none of the characters a path, URL, e-mail or repository slug needs.
OpaqueId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,128}$")]
# A public identifier: an `owner` or `owner/repo` slug. Admitted only under policy.
PublicId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,100}(/[A-Za-z0-9._-]{1,100})?$")]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PolicyVersion = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,32}$")]
# A detached signature over the envelope, produced by the source. Opaque to
# the core (it is stored and returned, never verified here); bounded base64url.
Signature = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_=-]{16,1024}$")]


class SafeEnvelope(BaseModel):
    """Version 1 of the metadata-only event envelope. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    schema_version: Literal[1] = Field(description="Envelope schema version. Only 1 exists.")
    policy_version: PolicyVersion = Field(description="Producer-side disclosure policy the envelope was built under.")
    identifier_policy: Literal["opaque", "public"] = Field(
        default="opaque",
        description=(
            "'opaque': every identifier is an unguessable token. 'public': repository / workspace identifiers "
            "may be public slugs; accepted only when the instance allows public identifiers."
        ),
    )

    event_id: OpaqueId = Field(description="Producer-assigned, globally unique. Re-sending the same id is idempotent.")
    actor_id: OpaqueId = Field(description="Opaque identifier of the acting agent or person.")
    repository_id: OpaqueId | PublicId = Field(description="Opaque token, or a public slug under the public policy.")
    workspace_id: OpaqueId | None = Field(default=None, description="Opaque identifier of the checkout, if any.")
    session_id: OpaqueId | None = Field(default=None, description="Opaque identifier of the producer's session.")

    action: models.SafeActionEnum
    outcome: models.SafeOutcomeEnum

    artifact_id: OpaqueId | None = Field(default=None, description="Opaque identifier of the artifact acted on.")
    artifact_version: int | None = Field(default=None, ge=1)
    artifact_commitment: Sha256Hex | None = Field(
        default=None, description="Source-produced digest of the artifact. The core never sees the bytes."
    )
    artifact_commitment_algorithm: Literal["sha256-utf8-v1"] | None = None

    occurred_at: datetime = Field(description="When the event happened at the source. Timezone-aware.")
    producer_signature: Signature | None = None

    # Field-level checks report the field's own name in the rejection.

    @field_validator("repository_id")
    @classmethod
    def _repository_id_matches_policy(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("identifier_policy", "opaque") == "opaque" and "/" in value:
            raise ValueError("must be an opaque identifier under the opaque identifier policy")
        return value

    @field_validator("occurred_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _artifact_fields_go_together(self) -> SafeEnvelope:
        artifact_fields = (self.artifact_id, self.artifact_commitment, self.artifact_commitment_algorithm)
        if any(f is not None for f in artifact_fields) and not all(f is not None for f in artifact_fields):
            raise ValueError("artifact_id, artifact_commitment and artifact_commitment_algorithm go together")
        if self.artifact_version is not None and self.artifact_id is None:
            raise ValueError("artifact_version requires artifact_id")
        return self


def published_json_schema() -> dict[str, Any]:
    """The JSON Schema a producer builds against (mirrored in docs/schemas/)."""
    schema = SafeEnvelope.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://github.com/AxonRelay/core/docs/schemas/safe-envelope-v{SCHEMA_VERSION}.json"
    schema["title"] = "AxonRelay Safe Envelope"
    return schema


# -------------------------------------------------------------------------- service


class EnvelopeRejected(ValueError):
    """The envelope did not validate. Carries field names only — never values."""

    def __init__(self, fields: list[str], reason: str = "invalid") -> None:
        self.fields = fields
        self.reason = reason
        detail = ", ".join(fields) if fields else "(envelope)"
        super().__init__(f"Safe Envelope rejected ({reason}): {detail}")


def _field_names(error: ValidationError) -> list[str]:
    names: list[str] = []
    for item in error.errors(include_input=False, include_url=False, include_context=False):
        loc = ".".join(str(part) for part in item.get("loc", ()) if not isinstance(part, int))
        names.append(loc or "(envelope)")
    return sorted(set(names))


def validate_envelope(payload: Any) -> SafeEnvelope:
    """Parse ``payload`` into a :class:`SafeEnvelope` or raise :class:`EnvelopeRejected`.

    The rejection names the offending fields and nothing else. Pydantic's own
    error objects hold the rejected input; they are consumed here and not
    re-raised, so neither the caller nor the log ever formats them.
    """
    if not isinstance(payload, dict):
        raise EnvelopeRejected(["(envelope)"], "not an object")
    try:
        envelope = SafeEnvelope.model_validate(payload)
    except ValidationError as exc:
        fields = _field_names(exc)
        logger.info("safe_envelope rejected fields=%s", fields)
        raise EnvelopeRejected(fields) from None
    if envelope.identifier_policy == "public" and not public_identifiers_allowed():
        logger.info("safe_envelope rejected fields=%s", ["identifier_policy"])
        raise EnvelopeRejected(["identifier_policy"], "public identifiers are not allowed on this instance")
    return envelope


def ingest(db: Session, payload: Any) -> tuple[models.SafeEvent, bool]:
    """Validate and persist one envelope. Returns ``(event, created)``.

    Idempotent on ``event_id``: a re-sent envelope returns the stored row and
    ``created=False`` without writing. Shared by the MCP tool and the REST
    endpoint; neither adds logic of its own.
    """
    envelope = validate_envelope(payload)
    existing = db.query(models.SafeEvent).filter(models.SafeEvent.event_id == envelope.event_id).first()
    if existing is not None:
        return existing, False

    event = models.SafeEvent(
        schema_version=envelope.schema_version,
        policy_version=envelope.policy_version,
        identifier_policy=envelope.identifier_policy,
        event_id=envelope.event_id,
        actor_ref=envelope.actor_id,
        repository_ref=envelope.repository_id,
        workspace_ref=envelope.workspace_id,
        session_ref=envelope.session_id,
        action=envelope.action,
        outcome=envelope.outcome,
        artifact_ref=envelope.artifact_id,
        artifact_version=envelope.artifact_version,
        artifact_commitment=envelope.artifact_commitment,
        artifact_commitment_algorithm=envelope.artifact_commitment_algorithm,
        occurred_at=envelope.occurred_at.astimezone(UTC).replace(tzinfo=None),
        received_at=datetime.utcnow(),
        producer_signature=envelope.producer_signature,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    logger.info("safe_envelope stored action=%s outcome=%s", event.action.value, event.outcome.value)
    return event, True


def list_events(
    db: Session, *, limit: int = 100, action: models.SafeActionEnum | None = None
) -> list[models.SafeEvent]:
    query = db.query(models.SafeEvent)
    if action is not None:
        query = query.filter(models.SafeEvent.action == action)
    return query.order_by(models.SafeEvent.id.desc()).limit(min(max(limit, 1), 1000)).all()


def event_to_dict(event: models.SafeEvent) -> dict[str, Any]:
    """The stored envelope, field for field. Nothing here was ever free text."""
    return {
        "id": event.id,
        "schema_version": event.schema_version,
        "policy_version": event.policy_version,
        "identifier_policy": event.identifier_policy,
        "event_id": event.event_id,
        "actor_id": event.actor_ref,
        "repository_id": event.repository_ref,
        "workspace_id": event.workspace_ref,
        "session_id": event.session_ref,
        "action": event.action.value,
        "outcome": event.outcome.value,
        "artifact_id": event.artifact_ref,
        "artifact_version": event.artifact_version,
        "artifact_commitment": event.artifact_commitment,
        "artifact_commitment_algorithm": event.artifact_commitment_algorithm,
        "occurred_at": event.occurred_at.isoformat() + "Z",
        "received_at": event.received_at.isoformat() + "Z",
        "producer_signature": event.producer_signature,
    }
