"""Add git resource claims and the workspace git_dir

Revision ID: 008
Revises: 007
Create Date: 2026-08-31

Path claims answer "who edits which files". They cannot describe the mutable
singletons a checkout has exactly one of - the working tree, the index, HEAD,
and the stash stack - and `git stash pop` names no path at all. That is how one
session ends up applying another session's parked work.

Two additions:

  claims.resource     WORKTREE / STASH / REFS / REMOTE - a claim on a shared
                      git singleton rather than on paths. REMOTE is the shared
                      remote's refs, contended across every clone of the repo.
  workspaces.git_dir  `git rev-parse --git-common-dir`. `refs/stash` is a
                      per-repository ref, so sibling *worktrees* of one clone
                      share a stash stack while having different clone_paths.
                      Scoping STASH conflicts by repo would be too wide
                      (separate clones are independent) and by workspace too
                      narrow (it would miss exactly the sibling-worktree case),
                      so the git common dir is the correct identity.

Purely additive; `claims.paths` keeps its NOT NULL and resource claims store [].
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

claim_resource = sa.Enum("WORKTREE", "STASH", "REFS", "REMOTE", name="claimresourceenum")


def upgrade() -> None:
    claim_resource.create(op.get_bind(), checkfirst=True)
    op.add_column("claims", sa.Column("resource", claim_resource, nullable=True))
    op.create_index("ix_claims_resource", "claims", ["resource"])

    op.add_column("workspaces", sa.Column("git_dir", sa.String(length=1000), nullable=True))
    op.create_index("ix_workspaces_git_dir", "workspaces", ["git_dir"])


def downgrade() -> None:
    op.drop_index("ix_workspaces_git_dir", table_name="workspaces")
    op.drop_column("workspaces", "git_dir")

    op.drop_index("ix_claims_resource", table_name="claims")
    op.drop_column("claims", "resource")
    claim_resource.drop(op.get_bind(), checkfirst=True)
