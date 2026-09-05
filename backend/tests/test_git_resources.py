"""Claims on the git resources no path pattern can describe.

The incident these exist for: a session ran `git stash pop` in a clone shared
with another session and consumed work that session had parked. `git stash pop`
names no path, so no path claim could have guarded it — and `refs/stash` is a
per-repository ref, so a *sibling git worktree* shares the same stash stack
despite having a different clone_path.

That last point drives the design: each resource has its own contention domain.
WORKTREE is per checkout; STASH and REFS reach across every worktree of a clone.
"""

import pytest

from app import coordination, models

GIT_DIR_A = "/Users/dev/work/core/.git"
GIT_DIR_B = "/Users/dev/other/core/.git"


def _session(db, actor, host, clone_path, git_dir, repo="AxonRelay/core"):
    return coordination.register_session(
        db, actor_name=actor, host=host, repo=repo, clone_path=clone_path, git_dir=git_dir
    )


@pytest.fixture
def primary(db):
    """The main checkout of a clone."""
    return _session(db, "claude", "mbp16", "/Users/dev/work/core", GIT_DIR_A)


@pytest.fixture
def sibling_worktree(db):
    """A `git worktree add` of the SAME clone — different path, same .git."""
    return _session(db, "codex", "mbp16", "/Users/dev/work/core-wt", GIT_DIR_A)


@pytest.fixture
def separate_clone(db):
    """An independent clone on the same machine — its own .git, its own stash."""
    return _session(db, "codex", "mbp16", "/Users/dev/other/core", GIT_DIR_B)


class TestStashDomain:
    """`refs/stash` is per-repository: worktrees share it, clones do not."""

    def test_a_sibling_worktree_contends_for_the_stash(self, db, primary, sibling_worktree):
        first = coordination.claim_resource(
            db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH, reason="parking wip"
        )
        assert first["granted"] is True

        second = coordination.claim_resource(
            db, session_id=sibling_worktree.id, resource=models.ClaimResourceEnum.STASH
        )
        assert second["granted"] is False
        assert second["conflicts"][0]["holder"]["actor"] == "claude"

    def test_a_separate_clone_does_not_contend_for_the_stash(self, db, primary, separate_clone):
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        other = coordination.claim_resource(db, session_id=separate_clone.id, resource=models.ClaimResourceEnum.STASH)
        assert other["granted"] is True

    def test_an_unknown_git_dir_is_treated_conservatively(self, db, primary):
        """Missing git_dir must over-report, never miss a collision."""
        unknown = _session(db, "codex", "mbp16", "/Users/dev/mystery/core", git_dir=None)
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)

        result = coordination.claim_resource(db, session_id=unknown.id, resource=models.ClaimResourceEnum.STASH)
        assert result["granted"] is False

    def test_a_different_host_never_contends(self, db, primary):
        """Same absolute path on another machine is a different filesystem."""
        elsewhere = _session(db, "codex", "studio", "/Users/dev/work/core", GIT_DIR_A)
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)

        result = coordination.claim_resource(db, session_id=elsewhere.id, resource=models.ClaimResourceEnum.STASH)
        assert result["granted"] is True


class TestWorktreeDomain:
    """The working tree, index and HEAD belong to one checkout only."""

    def test_a_sibling_worktree_does_not_contend_for_the_worktree(self, db, primary, sibling_worktree):
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.WORKTREE)
        result = coordination.claim_resource(
            db, session_id=sibling_worktree.id, resource=models.ClaimResourceEnum.WORKTREE
        )
        assert result["granted"] is True

    def test_two_sessions_in_one_checkout_contend(self, db, primary):
        """Two agents in the *same* directory share one working tree."""
        roommate = _session(db, "codex", "mbp16", "/Users/dev/work/core", GIT_DIR_A)
        assert roommate.workspace_id == primary.workspace_id

        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.WORKTREE)
        result = coordination.claim_resource(db, session_id=roommate.id, resource=models.ClaimResourceEnum.WORKTREE)
        assert result["granted"] is False


class TestResourceClaimBehaviour:
    def test_resources_are_independent_of_each_other(self, db, primary, sibling_worktree):
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        result = coordination.claim_resource(db, session_id=sibling_worktree.id, resource=models.ClaimResourceEnum.REFS)
        assert result["granted"] is True

    def test_a_resource_claim_does_not_collide_with_path_claims(self, db, primary, sibling_worktree):
        """Different axes: holding the stash must not block editing files."""
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        result = coordination.claim_territory(db, session_id=sibling_worktree.id, paths=["backend/app"])
        assert result["granted"] is True

    def test_a_path_claim_does_not_collide_with_resource_claims(self, db, primary, sibling_worktree):
        coordination.claim_territory(db, session_id=primary.id, paths=["backend/app"])
        result = coordination.claim_resource(
            db, session_id=sibling_worktree.id, resource=models.ClaimResourceEnum.STASH
        )
        assert result["granted"] is True

    def test_reclaiming_your_own_resource_is_a_renewal(self, db, primary):
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        again = coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        assert again["granted"] is True

    def test_force_records_the_override(self, db, primary, sibling_worktree):
        first = coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        forced = coordination.claim_resource(
            db, session_id=sibling_worktree.id, resource=models.ClaimResourceEnum.STASH, force=True
        )
        assert forced["granted"] is True
        assert forced["claim"].forced_over == [first["claim"].id]

    def test_an_expired_resource_claim_stops_blocking(self, db, primary, sibling_worktree):
        from datetime import datetime, timedelta

        result = coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        result["claim"].expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()

        retry = coordination.claim_resource(db, session_id=sibling_worktree.id, resource=models.ClaimResourceEnum.STASH)
        assert retry["granted"] is True

    def test_ending_a_session_releases_its_resource_claims(self, db, primary, sibling_worktree):
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        coordination.end_session(db, primary.id)

        retry = coordination.claim_resource(db, session_id=sibling_worktree.id, resource=models.ClaimResourceEnum.STASH)
        assert retry["granted"] is True

    def test_resource_claims_appear_on_the_board(self, db, primary):
        coordination.claim_resource(
            db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH, reason="parking wip"
        )
        claims = coordination.board(db)["claims"]
        assert [c["resource"] for c in claims] == ["stash"]
        assert claims[0]["paths"] == []


class TestGuard:
    """What `tools/gitsafe` asks before letting a destructive command through."""

    def test_the_holder_is_allowed_through(self, db, primary):
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        verdict = coordination.guard_git_operation(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        assert verdict["allowed"] is True

    def test_a_contending_session_is_refused(self, db, primary, sibling_worktree):
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        verdict = coordination.guard_git_operation(
            db, session_id=sibling_worktree.id, resource=models.ClaimResourceEnum.STASH
        )
        assert verdict["allowed"] is False
        assert verdict["conflicts"][0]["holder"]["actor"] == "claude"

    def test_an_unclaimed_resource_is_allowed(self, db, sibling_worktree):
        """Claims are advisory; a guard that blocked uncoordinated work gets disabled."""
        verdict = coordination.guard_git_operation(
            db, session_id=sibling_worktree.id, resource=models.ClaimResourceEnum.STASH
        )
        assert verdict["allowed"] is True


class TestUnregisteredCaller:
    """The subagent case: a process that spawned inside someone's clone.

    It never registered, so it cannot hold a claim — and it is exactly the thing
    the holder took the claim to keep out.
    """

    def test_an_unregistered_caller_is_refused_in_a_claimed_clone(self, db, primary):
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        verdict = coordination.guard_unregistered_caller(
            db, host="mbp16", clone_path="/Users/dev/work/core", resource=models.ClaimResourceEnum.STASH
        )
        assert verdict["allowed"] is False
        assert verdict["caller"]["registered"] is True

    def test_an_unregistered_caller_is_refused_from_a_sibling_worktree(self, db, primary, sibling_worktree):
        """A subagent in a sibling worktree still shares the stash stack."""
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        verdict = coordination.guard_unregistered_caller(
            db, host="mbp16", clone_path="/Users/dev/work/core-wt", resource=models.ClaimResourceEnum.STASH
        )
        assert verdict["allowed"] is False

    def test_an_unknown_clone_on_the_same_host_is_refused_conservatively(self, db, primary):
        """We cannot prove an unregistered path is a different checkout."""
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        verdict = coordination.guard_unregistered_caller(
            db, host="mbp16", clone_path="/tmp/who-knows", resource=models.ClaimResourceEnum.STASH
        )
        assert verdict["allowed"] is False
        assert verdict["caller"]["registered"] is False

    def test_an_unregistered_caller_on_another_host_is_unaffected(self, db, primary):
        coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        verdict = coordination.guard_unregistered_caller(
            db, host="studio", clone_path="/Users/dev/work/core", resource=models.ClaimResourceEnum.STASH
        )
        assert verdict["allowed"] is True

    def test_an_unregistered_caller_passes_when_nothing_is_claimed(self, db, primary):
        verdict = coordination.guard_unregistered_caller(
            db, host="mbp16", clone_path="/Users/dev/work/core", resource=models.ClaimResourceEnum.WORKTREE
        )
        assert verdict["allowed"] is True


class TestGitDirIdentity:
    """Two spellings of one `.git` must contend, or sibling worktrees slip through.

    `git rev-parse --git-common-dir` prints a *relative* `.git` from the main
    worktree and an absolute path from a linked one, so the server resolves a
    relative value against clone_path and normalises both.
    """

    def test_a_relative_git_dir_is_resolved_against_the_clone_path(self, db, primary):
        # The main worktree registers with git's own relative spelling.
        main = _session(db, "codex", "mbp16", "/Users/dev/work/core", ".git")
        assert main.workspace.git_dir == GIT_DIR_A
        assert main.workspace.id == primary.workspace.id

    def test_normalised_spellings_of_one_git_dir_contend(self, db, primary):
        sibling = _session(db, "codex", "mbp16", "/Users/dev/work/core-wt", "/Users/dev/work//core/./.git/")
        assert sibling.workspace.git_dir == GIT_DIR_A

        held = coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        assert held["granted"] is True
        contended = coordination.claim_resource(db, session_id=sibling.id, resource=models.ClaimResourceEnum.STASH)
        assert contended["granted"] is False

    def test_a_linked_worktree_relative_gitdir_is_resolved_too(self, db, primary):
        # A linked worktree's own `.git` is a file; `--git-common-dir` from inside
        # it is usually absolute, but a relative form still resolves sensibly.
        sibling = _session(db, "codex", "mbp16", "/Users/dev/work/core-wt", "../core/.git")
        assert sibling.workspace.git_dir == GIT_DIR_A

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_blank_git_dir_stays_unknown(self, db, value):
        session = _session(db, "codex", "mbp16", "/Users/dev/elsewhere/core", value)
        assert session.workspace.git_dir is None


class TestClaimWritersAreSerialised:
    """SQLite has no row locks, so here we only assert the lock is *requested*.

    The Postgres parity suite (tests/test_postgres_schema.py) races real
    connections and checks that exactly one of N racing STASH claims is granted.
    """

    def test_claiming_a_resource_locks_the_repo_workspaces(self, db, primary):
        from sqlalchemy import event

        statements: list[str] = []
        bind = db.get_bind()

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(bind, "before_cursor_execute", capture)
        try:
            coordination.claim_resource(db, session_id=primary.id, resource=models.ClaimResourceEnum.STASH)
        finally:
            event.remove(bind, "before_cursor_execute", capture)

        locking = [s for s in statements if "FROM workspaces" in s and "ORDER BY workspaces.id" in s]
        assert locking, "claim_resource should read the repo's workspaces with a row lock before checking conflicts"

    def test_claiming_paths_locks_the_repo_workspaces(self, db, primary):
        from sqlalchemy import event

        statements: list[str] = []
        bind = db.get_bind()

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(bind, "before_cursor_execute", capture)
        try:
            coordination.claim_territory(db, session_id=primary.id, paths=["backend/app/"])
        finally:
            event.remove(bind, "before_cursor_execute", capture)

        locking = [s for s in statements if "FROM workspaces" in s and "ORDER BY workspaces.id" in s]
        assert locking
