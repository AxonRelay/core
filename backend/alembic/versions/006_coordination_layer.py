"""Add the coordination layer: workspaces, sessions, claims, relays, receipts

Revision ID: 006
Revises: 005
Create Date: 2026-08-30

Introduces the tables that let several agents work at once across machines,
repositories and sibling clones of one repository:

  workspaces      one checkout of one repo on one machine
  sessions        an Actor working inside a workspace over a stretch of time
  claims          advisory, expiring leases on paths within a repo
  relays          durable, addressed messages between sessions
  relay_receipts  per-recipient read / ack state for a relay

Purely additive - no existing table is touched, so this is reversible without
data loss. See docs/coordination-spec.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

session_status = sa.Enum("ACTIVE", "ENDED", name="sessionstatusenum")
claim_mode = sa.Enum("EXCLUSIVE", "SHARED", name="claimmodeenum")
claim_status = sa.Enum("HELD", "RELEASED", name="claimstatusenum")
relay_kind = sa.Enum("NOTE", "QUESTION", "ANSWER", "HANDOFF", "WARNING", name="relaykindenum")


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("clone_path", sa.String(length=1000), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("host", "repo", "clone_path", name="uq_workspace_identity"),
    )
    op.create_index("ix_workspaces_id", "workspaces", ["id"])
    op.create_index("ix_workspaces_host", "workspaces", ["host"])
    op.create_index("ix_workspaces_repo", "workspaces", ["repo"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("focus", sa.Text(), nullable=True),
        sa.Column("status", session_status, nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["actors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_id", "sessions", ["id"])
    op.create_index("ix_sessions_actor_id", "sessions", ["actor_id"])
    op.create_index("ix_sessions_workspace_id", "sessions", ["workspace_id"])
    op.create_index("ix_sessions_status", "sessions", ["status"])
    op.create_index("ix_sessions_last_heartbeat_at", "sessions", ["last_heartbeat_at"])
    op.create_index("ix_sessions_workspace_status", "sessions", ["workspace_id", "status"])

    op.create_table(
        "claims",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("paths", sa.JSON(), nullable=False),
        sa.Column("mode", claim_mode, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", claim_status, nullable=False),
        sa.Column("forced_over", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claims_id", "claims", ["id"])
    op.create_index("ix_claims_session_id", "claims", ["session_id"])
    op.create_index("ix_claims_repo", "claims", ["repo"])
    op.create_index("ix_claims_status", "claims", ["status"])
    op.create_index("ix_claims_expires_at", "claims", ["expires_at"])
    op.create_index("ix_claims_repo_status", "claims", ["repo", "status"])

    op.create_table(
        "relays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("from_session_id", sa.Integer(), nullable=True),
        sa.Column("from_actor_id", sa.Integer(), nullable=True),
        sa.Column("to_actor_id", sa.Integer(), nullable=True),
        sa.Column("to_workspace_id", sa.Integer(), nullable=True),
        sa.Column("to_repo", sa.String(length=255), nullable=True),
        sa.Column("kind", relay_kind, nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("in_reply_to_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["from_session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["from_actor_id"], ["actors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["to_actor_id"], ["actors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["in_reply_to_id"], ["relays.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_relays_id", "relays", ["id"])
    op.create_index("ix_relays_from_session_id", "relays", ["from_session_id"])
    op.create_index("ix_relays_from_actor_id", "relays", ["from_actor_id"])
    op.create_index("ix_relays_to_actor_id", "relays", ["to_actor_id"])
    op.create_index("ix_relays_to_workspace_id", "relays", ["to_workspace_id"])
    op.create_index("ix_relays_to_repo", "relays", ["to_repo"])
    op.create_index("ix_relays_kind", "relays", ["kind"])
    op.create_index("ix_relays_created", "relays", ["created_at"])

    op.create_table(
        "relay_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("relay_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("acked_at", sa.DateTime(), nullable=True),
        sa.Column("ack_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["relay_id"], ["relays.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relay_id", "session_id", name="uq_receipt_relay_session"),
    )
    op.create_index("ix_relay_receipts_id", "relay_receipts", ["id"])
    op.create_index("ix_relay_receipts_relay_id", "relay_receipts", ["relay_id"])
    op.create_index("ix_relay_receipts_session_id", "relay_receipts", ["session_id"])


def downgrade() -> None:
    op.drop_table("relay_receipts")
    op.drop_table("relays")
    op.drop_table("claims")
    op.drop_table("sessions")
    op.drop_table("workspaces")

    bind = op.get_bind()
    for enum_type in (relay_kind, claim_status, claim_mode, session_status):
        enum_type.drop(bind, checkfirst=True)
