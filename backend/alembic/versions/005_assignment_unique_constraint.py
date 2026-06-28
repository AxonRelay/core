"""Add unique constraint on task_assignments (task_id, actor_id, role)

Revision ID: 005
Revises: 004
Create Date: 2026-06-28

An actor should hold a given role on a task at most once. Existing duplicate
rows (if any) are collapsed to the lowest id before the constraint is added.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    # Collapse any pre-existing duplicates, keeping the earliest row.
    connection.execute(
        sa.text(
            "DELETE FROM task_assignments WHERE id NOT IN ("
            "  SELECT MIN(id) FROM task_assignments GROUP BY task_id, actor_id, role"
            ")"
        )
    )
    op.create_unique_constraint(
        "uq_assignment_task_actor_role",
        "task_assignments",
        ["task_id", "actor_id", "role"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_assignment_task_actor_role", "task_assignments", type_="unique")
