"""Drop User/Project/ProjectMember and migrate Task/Approval to Actor-based FKs

Revision ID: 003
Revises: 002
Create Date: 2026-04-29

Phase 2.1.1 + 2.1.2 + 2.1.3 from docs/step2-plan.md.

Personal PoC pivot: AxonRelay drops the multi-user / multi-project model.
- Drop `users`, `projects`, `project_members` tables (and `roleenum`)
- `tasks`: drop `project_id`, rename `creator_id` -> `creator_actor_id` (FK -> actors)
- `approvals`: rename `reviewer_id` -> `reviewer_actor_id` (FK -> actors)
- Add new task status values: WAITING_REVIEW, NEEDS_REVISION
- Seed a single Actor(type=human, name="self") for the operator if none exists

Data preservation: existing `tasks.creator_id` / `approvals.reviewer_id` values are
mapped to `users.actor_id` before the column rename (best-effort; nullable on miss).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()

    # 1. Add new task status enum values (PG 16 supports ADD VALUE inside transactions)
    op.execute("ALTER TYPE taskstatusenum ADD VALUE IF NOT EXISTS 'WAITING_REVIEW' AFTER 'DRAFT'")
    op.execute("ALTER TYPE taskstatusenum ADD VALUE IF NOT EXISTS 'NEEDS_REVISION' AFTER 'REJECTED'")

    # 2. tasks: drop project_id, migrate creator_id -> creator_actor_id
    op.drop_constraint("tasks_project_id_fkey", "tasks", type_="foreignkey")
    op.drop_column("tasks", "project_id")

    op.drop_constraint("tasks_creator_id_fkey", "tasks", type_="foreignkey")
    connection.execute(sa.text("UPDATE tasks SET creator_id = u.actor_id FROM users u WHERE tasks.creator_id = u.id"))
    op.alter_column("tasks", "creator_id", new_column_name="creator_actor_id")
    op.create_foreign_key(
        "fk_tasks_creator_actor_id",
        "tasks",
        "actors",
        ["creator_actor_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. approvals: reviewer_id -> reviewer_actor_id
    op.drop_constraint("approvals_reviewer_id_fkey", "approvals", type_="foreignkey")
    connection.execute(
        sa.text("UPDATE approvals SET reviewer_id = u.actor_id FROM users u WHERE approvals.reviewer_id = u.id")
    )
    op.alter_column("approvals", "reviewer_id", new_column_name="reviewer_actor_id")
    op.create_foreign_key(
        "fk_approvals_reviewer_actor_id",
        "approvals",
        "actors",
        ["reviewer_actor_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 4. Drop project_members, projects, users
    op.drop_index("ix_project_members_id", table_name="project_members")
    op.drop_table("project_members")

    op.drop_index("ix_projects_id", table_name="projects")
    op.drop_table("projects")

    op.drop_index("ix_users_actor_id", table_name="users")
    op.drop_constraint("fk_users_actor_id", "users", type_="foreignkey")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")

    # 5. Drop unused roleenum
    sa.Enum(name="roleenum").drop(op.get_bind(), checkfirst=True)

    # 6. Seed a single human Actor "self" if none exists
    result = connection.execute(sa.text("SELECT COUNT(*) FROM actors WHERE type = 'human'")).fetchone()
    human_count = result[0] if result else 0
    if human_count == 0:
        connection.execute(sa.text("INSERT INTO actors (type, name, created_at) VALUES ('human', 'self', NOW())"))


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade from migration 003 is not supported (personal PoC). Restore from a database snapshot instead."
    )
