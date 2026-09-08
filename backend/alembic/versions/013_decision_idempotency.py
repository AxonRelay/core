"""Retry de-duplication and resume tracking for approval decisions

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
refuses, and the constraint still holds if two rounds race past the lookup on
separate connections.

Nullable, and no backfill: existing rows were written by callers that had no
key, and NULLs are distinct in a unique index on both Postgres and SQLite, so
they neither collide with each other nor with keyed rows.

Two more columns record what used to be *guessed*:

* `resumed_at` - when the graph was told about this decision. A ledger row is
  not evidence that the Platform call after it succeeded, and the two states
  that follow ("recorded, never delivered" and "delivered, and the graph
  interrupted again") are indistinguishable from the task's status alone.
  Guessing between them either parks a task forever or silently replays a
  finished decision instead of asking the reviewer the new question, so it is
  written down. Backfilled to `created_at`: nothing before this migration can
  be shown to be undelivered, and assuming otherwise would replay history.
* `edited_artifact` - whether the decision carried the reviewer's own edited
  text. Recovering a lost resume has to re-send what the original sent, and
  the draft's producer does not answer the question: a reviewer who authored
  the latest draft earlier, by other means, looks identical to one who edited
  it as part of this approval.

All three columns are deliberately *outside* the hash chain (app/ledger.py) -
they identify the request and its delivery, not the attested event - so
`hash_version` does not move and every existing entry still verifies.
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
    op.add_column("approvals", sa.Column("resumed_at", sa.DateTime(), nullable=True))
    op.add_column("approvals", sa.Column("edited_artifact", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Every existing entry is treated as delivered: there is no evidence
    # otherwise, and treating history as undelivered would re-drive old
    # decisions into the graph on the next review.
    op.execute("UPDATE approvals SET resumed_at = created_at WHERE resumed_at IS NULL")


def downgrade() -> None:
    op.drop_column("approvals", "edited_artifact")
    op.drop_column("approvals", "resumed_at")
    op.drop_constraint("uq_approval_decision_key", "approvals", type_="unique")
    op.drop_column("approvals", "decision_key")
