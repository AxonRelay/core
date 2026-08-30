"""Actor unified model for human and AI assignment (ADR-005)

Revision ID: 002
Revises: 001
Create Date: 2026-02-06

The enum types are declared with ``create_type=False`` so that only the explicit
``.create(..., checkfirst=True)`` calls below emit ``CREATE TYPE``. Passing a
plain ``sa.Enum`` to ``op.create_table`` makes the table creation emit its own
``CREATE TYPE`` as well - which does not honour ``checkfirst`` - and the second
one fails with "type already exists" on a fresh database.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create actor_type enum
    actor_type_enum = postgresql.ENUM("human", "ai", name="actortypeenum", create_type=False)
    actor_type_enum.create(op.get_bind(), checkfirst=True)

    # Create agent_type enum
    agent_type_enum = postgresql.ENUM(
        "writer", "reviewer", "validator", "researcher", "assistant", "custom", name="agenttypeenum", create_type=False
    )
    agent_type_enum.create(op.get_bind(), checkfirst=True)

    # Create assignment_role enum
    assignment_role_enum = postgresql.ENUM(
        "executor", "reviewer", "approver", "observer", name="assignmentroleenum", create_type=False
    )
    assignment_role_enum.create(op.get_bind(), checkfirst=True)

    # Create actors table
    op.create_table(
        "actors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", actor_type_enum, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_actors_id", "actors", ["id"])
    op.create_index("ix_actors_type", "actors", ["type"])

    # Create agent_definitions table
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("agent_type", agent_type_enum, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["actor_id"], ["actors.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("actor_id"),
    )
    op.create_index("ix_agent_definitions_id", "agent_definitions", ["id"])
    op.create_index("ix_agent_definitions_agent_type", "agent_definitions", ["agent_type"])

    # Create task_assignments table
    op.create_table(
        "task_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("role", assignment_role_enum, nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["actors.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_task_assignments_id", "task_assignments", ["id"])
    op.create_index("ix_task_assignments_task_id", "task_assignments", ["task_id"])
    op.create_index("ix_task_assignments_actor_id", "task_assignments", ["actor_id"])
    op.create_index("ix_task_assignments_role", "task_assignments", ["role"])

    # Add actor_id column to users table
    op.add_column("users", sa.Column("actor_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_users_actor_id", "users", "actors", ["actor_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_users_actor_id", "users", ["actor_id"], unique=True)

    # Data migration: Create Actor for existing users
    # Using raw SQL for data migration
    connection = op.get_bind()

    # Get all existing users
    users = connection.execute(sa.text("SELECT id, name, email FROM users")).fetchall()

    for user in users:
        user_id, name, email = user
        display_name = name if name else email.split("@")[0]

        # Insert actor for this user
        result = connection.execute(
            sa.text("INSERT INTO actors (type, name, created_at) VALUES ('human', :name, NOW()) RETURNING id"),
            {"name": display_name},
        )
        actor_id = result.fetchone()[0]

        # Update user with actor_id
        connection.execute(
            sa.text("UPDATE users SET actor_id = :actor_id WHERE id = :user_id"),
            {"actor_id": actor_id, "user_id": user_id},
        )


def downgrade() -> None:
    # Remove actor_id from users
    op.drop_index("ix_users_actor_id", "users")
    op.drop_constraint("fk_users_actor_id", "users", type_="foreignkey")
    op.drop_column("users", "actor_id")

    # Drop task_assignments table
    op.drop_index("ix_task_assignments_role", "task_assignments")
    op.drop_index("ix_task_assignments_actor_id", "task_assignments")
    op.drop_index("ix_task_assignments_task_id", "task_assignments")
    op.drop_index("ix_task_assignments_id", "task_assignments")
    op.drop_table("task_assignments")

    # Drop agent_definitions table
    op.drop_index("ix_agent_definitions_agent_type", "agent_definitions")
    op.drop_index("ix_agent_definitions_id", "agent_definitions")
    op.drop_table("agent_definitions")

    # Drop actors table
    op.drop_index("ix_actors_type", "actors")
    op.drop_index("ix_actors_id", "actors")
    op.drop_table("actors")

    # Drop enums
    sa.Enum(name="assignmentroleenum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="agenttypeenum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="actortypeenum").drop(op.get_bind(), checkfirst=True)
