"""Per-caller credentials for scoped authorization

Revision ID: 011
Revises: 010
Create Date: 2026-09-07

Caller identity and scoped authorization (issue #27, app/authz.py). Until now
reaching the port was the whole of authorization: the REST API asked nothing,
and the MCP HTTP transport asked at most for one shared secret (ADR-008), which
says "somebody who holds the secret" and not "which somebody".

A row here binds a token to an Actor and a set of scopes. Only the token's
SHA-256 is stored, and it is unique so a lookup is one indexed hit with no
plaintext anywhere in the query. `actor_id` cascades: deleting an Actor must
revoke its credentials, or a deleted identity would keep authenticating.

No data is created here. An instance without credentials behaves exactly as
before; enforcement is opt-in through AXONRELAY_REQUIRE_AUTH.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["actors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_credentials_token_hash"),
    )
    op.create_index("ix_credentials_id", "credentials", ["id"])
    op.create_index("ix_credentials_actor_id", "credentials", ["actor_id"])
    op.create_index("ix_credentials_token_hash", "credentials", ["token_hash"])


def downgrade() -> None:
    op.drop_index("ix_credentials_token_hash", table_name="credentials")
    op.drop_index("ix_credentials_actor_id", table_name="credentials")
    op.drop_index("ix_credentials_id", table_name="credentials")
    op.drop_table("credentials")
