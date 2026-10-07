"""Evidence Clips as commitments: digest and provenance, never the text

Revision ID: 014
Revises: 013
Create Date: 2026-10-07

Re-lands the Evidence Clip parts of PR #53 on top of the P0 governance core
(ADR-014). The shared ledger records that an operator captured an excerpt for
a task - where from, when, by whom, and the SHA-256 of its exact UTF-8 text -
and nothing that would let it reproduce the excerpt. There is no column for
the quote, the page title or model annotations; those stay with the capturing
client and are resolved against `content_sha256` on that side.

`source_url` is held to the same rule as `external_links.url` (ADR-012):
http(s), no userinfo, no query, no fragment. That is enforced by the model's
validator, so it is not repeated as a database constraint here.

Feedback is a closed verdict vocabulary with no comment column, for the same
reason.

Both tables are additive. Nothing existing is altered, and the approval hash
chain does not reference either table: a draft that cites `[E-n]` is bound
through the draft's own commitment (ADR-009), and the clip it names is
immutable.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# SQLAlchemy persists enum member *names*; the labels must be those (see 007).
source_type = postgresql.ENUM("PUBLIC", "PERSONAL", name="evidencesourcetypeenum", create_type=False)
feedback_verdict = postgresql.ENUM(
    "RELEVANT", "IRRELEVANT", "MISLEADING", name="evidencefeedbackverdictenum", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    source_type.create(bind, checkfirst=True)
    feedback_verdict.create(bind, checkfirst=True)

    op.create_table(
        "evidence_clips",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("captured_by_actor_id", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(length=2000), nullable=False),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("content_algorithm", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["captured_by_actor_id"], ["actors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "source_url", "content_sha256", name="uq_evidence_task_source_content"),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_evidence_content_sha256_hex"),
    )
    op.create_index("ix_evidence_clips_id", "evidence_clips", ["id"])
    op.create_index("ix_evidence_clips_task_id", "evidence_clips", ["task_id"])
    op.create_index("ix_evidence_clips_captured_by_actor_id", "evidence_clips", ["captured_by_actor_id"])
    op.create_index("ix_evidence_clips_content_sha256", "evidence_clips", ["content_sha256"])
    op.create_index("ix_evidence_task_captured", "evidence_clips", ["task_id", "captured_at"])

    op.create_table(
        "evidence_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("evidence_clip_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("verdict", feedback_verdict, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["actors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["evidence_clip_id"], ["evidence_clips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_feedback_id", "evidence_feedback", ["id"])
    op.create_index("ix_evidence_feedback_evidence_clip_id", "evidence_feedback", ["evidence_clip_id"])
    op.create_index("ix_evidence_feedback_actor_id", "evidence_feedback", ["actor_id"])


def downgrade() -> None:
    op.drop_table("evidence_feedback")
    op.drop_table("evidence_clips")
    feedback_verdict.drop(op.get_bind(), checkfirst=True)
    source_type.drop(op.get_bind(), checkfirst=True)
