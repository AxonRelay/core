"""Retry de-duplication for approval decisions

Revision ID: 013
Revises: 012
Create Date: 2026-09-07

MCP 2026-07-28 turned a server-initiated question into a *replayed* tool call
(issue #30): the client answers by calling `tools/call` again with the same
arguments and the echoed `requestState`, so the tool body runs once per round
and a transport-level retry runs it again. Everything else in the ledger is
protected by the artifact binding, but two identical rounds naming the same
draft are indistinguishable at the binding level - and an approval ledger that
can gain a duplicate entry is no longer a record of what was decided.

`approvals.decision_key` names the decision the caller believes it is making.
The unique index over `(task_id, decision_key)` is what actually prevents the
second append: `record_approval` looks the key up under the task row lock and
refuses, and the constraint still holds if two rounds race past the lookup on
separate connections.

Nullable, and no backfill: existing rows were written by callers that had no
key, and NULLs are distinct in a unique index on both Postgres and SQLite, so
they neither collide with each other nor with keyed rows.

The column is **inside** the hash chain, as payload v3 (app/ledger.py). It
would have been easier to leave it out - it describes the request rather than
the attested event, and adding it moves `hash_version` - but the server *acts*
on it, and a field that changes behaviour while verification cannot see it
could be cleared for free. Editing a recorded decision costs a rewrite of
every later entry; that difference is the point of the chain, and an
unauthenticated control field would not have it. Existing v1 and v2 rows are
hashed exactly as they were written and keep verifying unchanged.
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
    # A key only means anything on a payload that hashes it. Planted on a
    # legacy row it would cost nothing - that row's hash does not cover the
    # field - and the unique index would then refuse the real decision the key
    # belongs to. `verify_approval_chain` reports such a row as tampered; this
    # stops it being written at all.
    # `hash_version` is a column like any other, so requiring it alone would
    # let a claimed v3 sit on an unhashed row. A key belongs only on a row that
    # is actually hashed, at a payload version that covers it.
    op.create_check_constraint(
        "ck_approval_decision_key_needs_v3",
        "approvals",
        "decision_key IS NULL OR (entry_hash IS NOT NULL AND hash_version IS NOT NULL AND hash_version >= 3)",
    )


def downgrade() -> None:
    """Refuse once any v3 entry exists, because the column is inside its hash.

    Dropping `decision_key` is not reversible in the way a downgrade implies: a
    later re-upgrade recreates the column empty, and every v3 entry that
    carried a key then recomputes to a different hash and reports as tampered
    forever. Losing the ability to verify a legitimate ledger is worse than
    refusing to downgrade, so this stops rather than corrupts. Immediately
    after an upgrade, with nothing keyed yet, it is genuinely reversible and
    proceeds.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Count and drop have to be one window. Otherwise a writer that commits
        # a keyed approval between them loses hashed data to a DROP COLUMN that
        # was already cleared to proceed.
        bind.execute(sa.text("LOCK TABLE approvals IN ACCESS EXCLUSIVE MODE"))
    keyed = bind.execute(sa.text("SELECT COUNT(*) FROM approvals WHERE decision_key IS NOT NULL")).scalar()
    if keyed:
        raise RuntimeError(
            f"refusing to downgrade: {keyed} approval(s) carry a decision_key, which is inside their v3 "
            "entry hash (app/ledger.py). Dropping the column would make those entries unverifiable for "
            "good. Remove or re-record them deliberately if this downgrade is really what you want."
        )
    op.drop_constraint("ck_approval_decision_key_needs_v3", "approvals", type_="check")
    op.drop_constraint("uq_approval_decision_key", "approvals", type_="unique")
    op.drop_column("approvals", "decision_key")
