"""Opaque identifiers and structured codes for the coordination plane

Revision ID: 012
Revises: 011
Create Date: 2026-09-07

Data minimisation for the coordination plane (issue #26, app/disclosure.py).

The board's rows describe machines and filesystems: a host name, an absolute
clone path, a git directory, and free-form focus / reason / subject / body /
ack text. All of it is more than a peer needs in order to avoid editing the
same files, and none of it should cross a shared boundary.

Two additions:

* `actors.opaque_id` / `workspaces.opaque_id` - a stable, unguessable stand-in
  for a name and for a checkout. Backfilled for every existing row, and unique
  so it can be the identity at a shared boundary. Random rather than derived
  from the values it replaces: a digest of a guessable name is not opaque.
* `sessions.focus_code`, `claims.reason_code`, `relays.code`,
  `relay_receipts.ack_code` - the disclosable form of the four free-text
  fields. Nullable, and nothing is inferred from the existing prose: a code
  guessed from a sentence would be a claim this migration cannot support.

Nothing is removed and nothing existing changes, so a deployment that keeps
running in full-text mode sees no difference. Enum labels are UPPERCASE
(migration 007 is the canonical statement of that rule).
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

focus_code = sa.Enum(
    "EXPLORING",
    "IMPLEMENTING",
    "REVIEWING",
    "TESTING",
    "DEBUGGING",
    "DOCUMENTING",
    "RELEASING",
    "BLOCKED",
    "IDLE",
    name="focuscodeenum",
)
reason_code = sa.Enum(
    "EDITING",
    "REFACTORING",
    "RUNNING_TESTS",
    "MIGRATING",
    "RELEASING",
    "INVESTIGATING",
    name="claimreasoncodeenum",
)
relay_code = sa.Enum(
    "HANDOFF_READY",
    "NEEDS_REVIEW",
    "BLOCKED_ON_YOU",
    "CONFLICT_DETECTED",
    "RELEASE_REQUESTED",
    "HEADS_UP",
    "ANSWERED",
    name="relaycodeenum",
)
ack_code = sa.Enum("ACKNOWLEDGED", "DONE", "DECLINED", "DEFERRED", "NOT_APPLICABLE", name="ackcodeenum")

#: Must equal app.disclosure.OPAQUE_ID_BYTES.
OPAQUE_ID_BYTES = 12


def _backfill_opaque_ids(bind, table: str) -> None:
    """Give every existing row an opaque id, one UPDATE per row.

    Per row because each value must be independently random; a set-based
    expression would need a database-specific random generator and would still
    have to be checked for collisions against the unique index.
    """
    rows = bind.execute(sa.text(f"SELECT id FROM {table} WHERE opaque_id IS NULL")).all()
    for (row_id,) in rows:
        bind.execute(
            sa.text(f"UPDATE {table} SET opaque_id = :o WHERE id = :i"),
            {"o": secrets.token_urlsafe(OPAQUE_ID_BYTES), "i": row_id},
        )


def upgrade() -> None:
    bind = op.get_bind()

    for table in ("actors", "workspaces"):
        op.add_column(table, sa.Column("opaque_id", sa.String(length=32), nullable=True))
        _backfill_opaque_ids(bind, table)
        op.create_index(f"ix_{table}_opaque_id", table, ["opaque_id"], unique=True)

    focus_code.create(bind, checkfirst=True)
    reason_code.create(bind, checkfirst=True)
    relay_code.create(bind, checkfirst=True)
    ack_code.create(bind, checkfirst=True)

    op.add_column("sessions", sa.Column("focus_code", focus_code, nullable=True))
    op.add_column("claims", sa.Column("reason_code", reason_code, nullable=True))
    op.add_column("relays", sa.Column("code", relay_code, nullable=True))
    op.add_column("relay_receipts", sa.Column("ack_code", ack_code, nullable=True))


def downgrade() -> None:
    op.drop_column("relay_receipts", "ack_code")
    op.drop_column("relays", "code")
    op.drop_column("claims", "reason_code")
    op.drop_column("sessions", "focus_code")

    bind = op.get_bind()
    ack_code.drop(bind, checkfirst=True)
    relay_code.drop(bind, checkfirst=True)
    reason_code.drop(bind, checkfirst=True)
    focus_code.drop(bind, checkfirst=True)

    for table in ("workspaces", "actors"):
        op.drop_index(f"ix_{table}_opaque_id", table_name=table)
        op.drop_column(table, "opaque_id")
