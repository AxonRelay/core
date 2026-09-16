"""Add task-scoped Evidence Clips and append-only feedback.

Revision ID: 009
Revises: 008
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

source_type = postgresql.ENUM("PUBLIC", "PERSONAL", name="evidencesourcetypeenum", create_type=False)
evidence_status = postgresql.ENUM("CANDIDATE", "CONFIRMED", "REJECTED", name="evidencestatusenum", create_type=False)
feedback_verdict = postgresql.ENUM(
    "RELEVANT", "IRRELEVANT", "MISLEADING", name="evidencefeedbackverdictenum", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    source_type.create(bind, checkfirst=True)
    evidence_status.create(bind, checkfirst=True)
    feedback_verdict.create(bind, checkfirst=True)

    op.add_column("tasks", sa.Column("approval_episode_id", sa.String(length=255), nullable=True))
    op.create_index("ix_tasks_approval_episode_id", "tasks", ["approval_episode_id"])

    op.add_column("approvals", sa.Column("approved_content", sa.Text(), nullable=True))
    op.add_column("approvals", sa.Column("approved_draft_sha256", sa.String(length=64), nullable=True))
    op.add_column("approvals", sa.Column("evidence_manifest", sa.JSON(), nullable=True))
    op.add_column("approvals", sa.Column("approval_episode_id", sa.String(length=255), nullable=True))
    op.add_column("approvals", sa.Column("decision_key", sa.String(length=64), nullable=True))
    op.add_column(
        "approvals", sa.Column("delivery_status", sa.String(length=20), nullable=False, server_default="legacy")
    )
    op.alter_column("approvals", "delivery_status", server_default="pending")
    op.add_column("approvals", sa.Column("delivery_run_id", sa.String(length=255), nullable=True))
    op.add_column("approvals", sa.Column("delivery_error", sa.Text(), nullable=True))
    op.create_index("ix_approvals_approval_episode_id", "approvals", ["approval_episode_id"])
    op.create_index("ix_approvals_decision_key", "approvals", ["decision_key"], unique=True)
    op.create_index("ix_approvals_delivery_run_id", "approvals", ["delivery_run_id"])
    op.create_unique_constraint(
        "uq_approval_task_episode",
        "approvals",
        ["task_id", "approval_episode_id"],
    )
    op.create_check_constraint(
        "ck_approval_decision_has_episode",
        "approvals",
        "decision_key IS NULL OR approval_episode_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_approval_approved_payload",
        "approvals",
        "approval_episode_id IS NULL OR action <> 'approved' OR "
        "(approved_content IS NOT NULL AND approved_draft_sha256 IS NOT NULL)",
    )

    op.create_table(
        "evidence_clips",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("captured_by_actor_id", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(length=2000), nullable=False),
        sa.Column("source_title", sa.String(length=500), nullable=False),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_sha256", sa.String(length=64), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=True),
        sa.Column("annotations", sa.JSON(), nullable=True),
        sa.Column("extractor", sa.String(length=100), nullable=True),
        sa.Column("extractor_version", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("inference_location", sa.String(length=20), nullable=False),
        sa.Column("extraction_ms", sa.Float(), nullable=True),
        sa.Column("status", evidence_status, nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["captured_by_actor_id"], ["actors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "source_url", "quote_sha256", name="uq_evidence_task_source_quote"),
    )
    op.create_index("ix_evidence_clips_id", "evidence_clips", ["id"])
    op.create_index("ix_evidence_clips_task_id", "evidence_clips", ["task_id"])
    op.create_index("ix_evidence_clips_captured_by_actor_id", "evidence_clips", ["captured_by_actor_id"])
    op.create_index("ix_evidence_clips_quote_sha256", "evidence_clips", ["quote_sha256"])
    op.create_index("ix_evidence_clips_status", "evidence_clips", ["status"])
    op.create_index("ix_evidence_task_captured", "evidence_clips", ["task_id", "captured_at"])

    op.create_table(
        "evidence_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("evidence_clip_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("verdict", feedback_verdict, nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["actors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["evidence_clip_id"], ["evidence_clips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_feedback_id", "evidence_feedback", ["id"])
    op.create_index("ix_evidence_feedback_evidence_clip_id", "evidence_feedback", ["evidence_clip_id"])
    op.create_index("ix_evidence_feedback_actor_id", "evidence_feedback", ["actor_id"])
    op.create_index("ix_evidence_feedback_verdict", "evidence_feedback", ["verdict"])


def downgrade() -> None:
    op.drop_table("evidence_feedback")
    op.drop_table("evidence_clips")
    op.drop_constraint("ck_approval_approved_payload", "approvals", type_="check")
    op.drop_constraint("ck_approval_decision_has_episode", "approvals", type_="check")
    op.drop_constraint("uq_approval_task_episode", "approvals", type_="unique")
    op.drop_index("ix_approvals_delivery_run_id", table_name="approvals")
    op.drop_index("ix_approvals_decision_key", table_name="approvals")
    op.drop_index("ix_approvals_approval_episode_id", table_name="approvals")
    op.drop_column("approvals", "delivery_error")
    op.drop_column("approvals", "delivery_run_id")
    op.drop_column("approvals", "delivery_status")
    op.drop_column("approvals", "decision_key")
    op.drop_column("approvals", "approval_episode_id")
    op.drop_column("approvals", "evidence_manifest")
    op.drop_column("approvals", "approved_draft_sha256")
    op.drop_column("approvals", "approved_content")
    op.drop_index("ix_tasks_approval_episode_id", table_name="tasks")
    op.drop_column("tasks", "approval_episode_id")
    feedback_verdict.drop(op.get_bind(), checkfirst=True)
    evidence_status.drop(op.get_bind(), checkfirst=True)
    source_type.drop(op.get_bind(), checkfirst=True)
