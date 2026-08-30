"""Align enum labels with the names SQLAlchemy actually writes

Revision ID: 007
Revises: 006
Create Date: 2026-08-30

Migration 002 declared actortypeenum / agenttypeenum / assignmentroleenum with
*lowercase* labels ("human", "ai", ...), but `sa.Enum(PythonEnum)` persists a
member's **name**, not its value - so SQLAlchemy sends "HUMAN" and Postgres
rejects it:

    invalid input value for enum actortypeenum: "HUMAN"

The effect was that inserting an Actor, AgentDefinition or TaskAssignment failed
against Postgres. It stayed hidden because the test suite runs on SQLite, where
Enum degrades to VARCHAR and accepts anything, and because the migration chain
itself could not reach a fresh Postgres (see the create_type fix in 002).

taskstatusenum (from 001) and the coordination enums (from 006) already use the
uppercase names, so this converges the three stragglers on that convention
rather than changing the models. Renaming labels preserves existing rows: the
stored value is repointed, not rewritten.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enum type -> the lowercase labels 002 created, renamed to their uppercase form.
ENUM_LABELS: dict[str, list[str]] = {
    "actortypeenum": ["human", "ai"],
    "agenttypeenum": ["writer", "reviewer", "validator", "researcher", "assistant", "custom"],
    "assignmentroleenum": ["executor", "reviewer", "approver", "observer"],
}


def _rename(type_name: str, old: str, new: str) -> None:
    """Rename one enum label, skipping it when it is already renamed.

    Guarded so the migration is safe on a database built after 002 was corrected,
    or one that has been partially migrated by hand.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                WHERE t.typname = '{type_name}' AND e.enumlabel = '{old}'
            ) AND NOT EXISTS (
                SELECT 1 FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                WHERE t.typname = '{type_name}' AND e.enumlabel = '{new}'
            ) THEN
                ALTER TYPE {type_name} RENAME VALUE '{old}' TO '{new}';
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    for type_name, labels in ENUM_LABELS.items():
        for label in labels:
            _rename(type_name, label, label.upper())


def downgrade() -> None:
    for type_name, labels in ENUM_LABELS.items():
        for label in labels:
            _rename(type_name, label.upper(), label)
