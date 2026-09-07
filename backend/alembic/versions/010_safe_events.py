"""Safe Envelope ingestion table

Revision ID: 010
Revises: 009
Create Date: 2026-09-07

Content-blind, metadata-only ingestion (issue #25, app/safe_envelope.py). The
table deliberately has no Text column: every field is a bounded identifier,
an enum, a digest or a timestamp, so a producer has nowhere to put a title,
a body, a path or a URL. Unknown envelope fields are rejected before insert.

Enum labels are UPPERCASE — SQLAlchemy persists member *names* (migration 007
is the canonical statement of that rule).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

safe_action = sa.Enum(
    "SESSION_START",
    "SESSION_HEARTBEAT",
    "SESSION_END",
    "CLAIM_REQUEST",
    "CLAIM_RELEASE",
    "ARTIFACT_PRODUCED",
    "DECISION_APPROVE",
    "DECISION_REJECT",
    "RELAY_NOTICE",
    "RELAY_ACK",
    name="safeactionenum",
)
safe_outcome = sa.Enum("SUCCESS", "REFUSED", "CONFLICT", "ERROR", name="safeoutcomeenum")


def upgrade() -> None:
    # No explicit `.create()`: create_table emits CREATE TYPE for its Enum
    # columns itself, and a second CREATE TYPE is exactly the defect migration
    # 002 had (see test_postgres_schema.py). 008 needs the explicit call only
    # because add_column does not create types.
    op.create_table(
        "safe_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("identifier_policy", sa.String(length=16), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("actor_ref", sa.String(length=128), nullable=False),
        sa.Column("repository_ref", sa.String(length=201), nullable=False),
        sa.Column("workspace_ref", sa.String(length=128), nullable=True),
        sa.Column("session_ref", sa.String(length=128), nullable=True),
        sa.Column("action", safe_action, nullable=False),
        sa.Column("outcome", safe_outcome, nullable=False),
        sa.Column("artifact_ref", sa.String(length=128), nullable=True),
        sa.Column("artifact_version", sa.Integer(), nullable=True),
        sa.Column("artifact_commitment", sa.String(length=64), nullable=True),
        sa.Column("artifact_commitment_algorithm", sa.String(length=32), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("producer_signature", sa.String(length=1024), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_safe_events_event_id"),
    )
    op.create_index("ix_safe_events_id", "safe_events", ["id"])
    op.create_index("ix_safe_events_event_id", "safe_events", ["event_id"])
    op.create_index("ix_safe_events_actor_ref", "safe_events", ["actor_ref"])
    op.create_index("ix_safe_events_repository_ref", "safe_events", ["repository_ref"])
    op.create_index("ix_safe_events_session_ref", "safe_events", ["session_ref"])
    op.create_index("ix_safe_events_action", "safe_events", ["action"])
    op.create_index("ix_safe_events_occurred_at", "safe_events", ["occurred_at"])


def downgrade() -> None:
    for name in (
        "ix_safe_events_occurred_at",
        "ix_safe_events_action",
        "ix_safe_events_session_ref",
        "ix_safe_events_repository_ref",
        "ix_safe_events_actor_ref",
        "ix_safe_events_event_id",
        "ix_safe_events_id",
    ):
        op.drop_index(name, table_name="safe_events")
    op.drop_table("safe_events")
    safe_outcome.drop(op.get_bind(), checkfirst=True)
    safe_action.drop(op.get_bind(), checkfirst=True)
