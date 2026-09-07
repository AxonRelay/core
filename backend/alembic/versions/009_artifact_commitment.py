"""Bind approvals to an immutable artifact commitment

Revision ID: 009
Revises: 008
Create Date: 2026-09-07

The approval hash chain (004) protects the approval *event* but does not say
which draft was approved: an entry stays cryptographically valid even if the
relationship between the approval and its artifact becomes ambiguous. This
migration makes the approved object part of every new ledger entry.

drafts
  * commitment / commitment_algorithm — SHA-256 of the draft's UTF-8 bytes,
    labelled so a future algorithm change is not mistaken for a mismatch.
    **Backfilled** for existing rows (the content is present, so the value is
    deterministic); the producer is unknown for those rows and stays NULL.
  * producer_actor_id — the Actor that produced this version (a reviewer who
    submitted a modified draft, for example). Plain integer, not a FK: a
    ledger field must not change when an Actor row is deleted.
  * uq_drafts_task_version — (task_id, version) is now unique, so the "new
    artifact version before approval" rule cannot be raced into a duplicate.
  * ix_drafts_task_id — every read filters on it.

approvals
  * hash_version — NULL for rows hashed with the v1 payload (004–008), 2 for
    rows that include the artifact binding. Verification picks the payload by
    this column and reports v1 rows as *not artifact-bound*, not as tampered.
  * artifact_ref / artifact_version / artifact_commitment /
    artifact_commitment_algorithm / producer_actor_id — the binding. All five
    are inside the v2 hash. NULL on legacy rows; **no backfill**, because a
    retroactive binding would be a guess, and the point is that it is not.
  * ix_approvals_task_id — every read filters on it.

Reversible: downgrade drops what was added. A downgrade after v2 rows exist
leaves them verifying as v1 payloads (which they are not) — restore from a
backup instead of downgrading a ledger that has been written to.
"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must equal app.ledger.COMMITMENT_ALGORITHM. Duplicated rather than imported so
# the migration stays self-contained.
COMMITMENT_ALGORITHM = "sha256-utf8-v1"


def upgrade() -> None:
    op.add_column("drafts", sa.Column("commitment", sa.String(length=64), nullable=True))
    op.add_column("drafts", sa.Column("commitment_algorithm", sa.String(length=32), nullable=True))
    op.add_column("drafts", sa.Column("producer_actor_id", sa.Integer(), nullable=True))
    op.create_index("ix_drafts_task_id", "drafts", ["task_id"])
    op.create_unique_constraint("uq_drafts_task_version", "drafts", ["task_id", "version"])

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, content FROM drafts WHERE commitment IS NULL")).all()
    for draft_id, content in rows:
        bind.execute(
            sa.text("UPDATE drafts SET commitment = :c, commitment_algorithm = :a WHERE id = :i"),
            {
                "c": hashlib.sha256((content or "").encode("utf-8")).hexdigest(),
                "a": COMMITMENT_ALGORITHM,
                "i": draft_id,
            },
        )

    op.add_column("approvals", sa.Column("hash_version", sa.Integer(), nullable=True))
    op.add_column("approvals", sa.Column("artifact_ref", sa.String(length=255), nullable=True))
    op.add_column("approvals", sa.Column("artifact_version", sa.Integer(), nullable=True))
    op.add_column("approvals", sa.Column("artifact_commitment", sa.String(length=64), nullable=True))
    op.add_column("approvals", sa.Column("artifact_commitment_algorithm", sa.String(length=32), nullable=True))
    op.add_column("approvals", sa.Column("producer_actor_id", sa.Integer(), nullable=True))
    op.create_index("ix_approvals_task_id", "approvals", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_approvals_task_id", table_name="approvals")
    op.drop_column("approvals", "producer_actor_id")
    op.drop_column("approvals", "artifact_commitment_algorithm")
    op.drop_column("approvals", "artifact_commitment")
    op.drop_column("approvals", "artifact_version")
    op.drop_column("approvals", "artifact_ref")
    op.drop_column("approvals", "hash_version")

    op.drop_constraint("uq_drafts_task_version", "drafts", type_="unique")
    op.drop_index("ix_drafts_task_id", table_name="drafts")
    op.drop_column("drafts", "producer_actor_id")
    op.drop_column("drafts", "commitment_algorithm")
    op.drop_column("drafts", "commitment")
