"""Add tamper-evident hash chain columns to approvals

Revision ID: 004
Revises: 003
Create Date: 2026-06-28

Phase 3 ledger hardening: each approval is chained to the prior one for the
same task via SHA-256 (see app/ledger.py). Adds nullable prev_hash / entry_hash;
existing rows (if any) stay NULL and verification simply treats them as the
chain start. Personal PoC — no backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("prev_hash", sa.String(length=64), nullable=True))
    op.add_column("approvals", sa.Column("entry_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("approvals", "entry_hash")
    op.drop_column("approvals", "prev_hash")
