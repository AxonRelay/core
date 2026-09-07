"""Retry de-duplication for approval decisions

Revision ID: 013
Revises: 012
Create Date: 2026-09-07

MCP 2026-07-28 turned a server-initiated question into a *replayed* tool call
(issue #30): the client answers by calling `tools/call` again with the same
arguments and the echoed `requestState`, so the tool body runs once per round
and a transport-level retry runs it again. Everything else in the ledger is
protected by the artifact binding, but two identical rounds naming the same
draft are indistinguishable from one another at the binding level - and an
approval ledger that can gain a duplicate entry is no longer a record of what
was decided.

`approvals.decision_key` names the decision the caller believes it is making.
The unique index over `(task_id, decision_key)` is what actually prevents the
second append: `record_approval` looks the key up under the task row lock and
returns the existing row, and the constraint still holds if two rounds race
past the lookup on separate connections.

Nullable, and no backfill: existing rows were written by callers that had no
key, and NULLs are distinct in a unique index on both Postgres and SQLite, so
they neither collide with each other nor with keyed rows. The column is
deliberately *outside* the hash chain (app/ledger.py) - it identifies the
request, not the attested event - so `hash_version` does not move and every
existing entry still verifies.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("decision_key", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_approval_decision_key", "approvals", ["task_id", "decision_key"])


def downgrade() -> None:
    op.drop_constraint("uq_approval_decision_key", "approvals", type_="unique")
    op.drop_column("approvals", "decision_key")
