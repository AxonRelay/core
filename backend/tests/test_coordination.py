"""Coordination layer: presence, territory claims, and relays.

These exercise the multi-agent scenario the layer exists for - two agents in two
clones of one repository, plus a third on another repo - rather than each
function in isolation.
"""

from datetime import datetime, timedelta

import pytest

from app import coordination, models


@pytest.fixture
def alice(db):
    """Claude, working in the first clone of AxonRelay/core."""
    return coordination.register_session(
        db,
        actor_name="claude",
        host="mbp16",
        repo="AxonRelay/core",
        clone_path="/Users/dev/workspace/core",
        branch="feature/a",
        focus="refactoring the ledger",
    )


@pytest.fixture
def bob(db):
    """Codex, working in a *second clone of the same repo* on another machine."""
    return coordination.register_session(
        db,
        actor_name="codex",
        host="studio",
        repo="AxonRelay/core",
        clone_path="/Users/dev/clones/core-2",
        branch="feature/b",
        focus="writing docs",
    )


class TestPresence:
    def test_registering_creates_actor_workspace_and_session(self, db, alice):
        assert alice.status == models.SessionStatusEnum.ACTIVE
        assert alice.actor.name == "claude"
        assert alice.actor.type == models.ActorTypeEnum.AI
        assert alice.workspace.repo == "AxonRelay/core"

    def test_ai_actor_gets_an_agent_definition(self, db, alice):
        agent = db.query(models.AgentDefinition).filter_by(actor_id=alice.actor_id).one()
        assert agent.agent_type == models.AgentTypeEnum.ASSISTANT

    def test_re_registering_resumes_the_same_session(self, db, alice):
        again = coordination.register_session(
            db,
            actor_name="claude",
            host="mbp16",
            repo="AxonRelay/core",
            clone_path="/Users/dev/workspace/core",
            focus="picked up after a crash",
        )
        assert again.id == alice.id
        assert again.focus == "picked up after a crash"
        assert db.query(models.Session).count() == 1

    def test_sibling_clones_are_distinct_workspaces(self, db, alice, bob):
        assert alice.workspace_id != bob.workspace_id
        assert db.query(models.Workspace).count() == 2

    def test_same_agent_in_two_clones_gets_two_sessions_one_actor(self, db, alice):
        second = coordination.register_session(
            db,
            actor_name="claude",
            host="mbp16",
            repo="AxonRelay/core",
            clone_path="/Users/dev/clones/core-2",
        )
        assert second.id != alice.id
        assert second.actor_id == alice.actor_id

    def test_heartbeat_updates_focus_and_liveness(self, db, alice):
        alice.last_heartbeat_at = datetime.utcnow() - timedelta(hours=2)
        db.commit()
        assert coordination.is_stale(alice)

        refreshed = coordination.heartbeat_session(db, alice.id, focus="now on tests")
        assert refreshed.focus == "now on tests"
        assert not coordination.is_stale(refreshed)

    def test_a_quiet_session_reads_as_stale_but_stays_active(self, db, alice):
        alice.last_heartbeat_at = datetime.utcnow() - timedelta(minutes=coordination.STALE_AFTER_MINUTES + 1)
        db.commit()
        assert coordination.is_stale(alice)
        assert alice.status == models.SessionStatusEnum.ACTIVE

    def test_list_sessions_filters_by_repo(self, db, alice, bob):
        coordination.register_session(db, actor_name="claude", host="mbp16", repo="other/repo", clone_path="/tmp/other")
        assert len(coordination.list_sessions(db, repo="AxonRelay/core")) == 2
        assert len(coordination.list_sessions(db)) == 3

    def test_ending_a_session_hides_it_and_frees_its_claims(self, db, alice):
        claim = coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])["claim"]
        ended, released_ids = coordination.end_session(db, alice.id)

        assert ended.status == models.SessionStatusEnum.ENDED
        assert released_ids == [claim.id]
        assert coordination.list_sessions(db) == []
        assert coordination.live_claims(db) == []

    def test_ending_reports_only_the_claims_that_call_released(self, db, alice):
        """An already-released claim must not be counted as freed by the exit."""
        earlier = coordination.claim_territory(db, session_id=alice.id, paths=["docs"])["claim"]
        still_held = coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])["claim"]
        coordination.release_claim(db, earlier.id)

        _, released_ids = coordination.end_session(db, alice.id)
        assert released_ids == [still_held.id]

    def test_ending_an_unknown_session_returns_none(self, db):
        assert coordination.end_session(db, 999) is None


class TestTerritory:
    def test_an_uncontested_claim_is_granted(self, db, alice):
        result = coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"], reason="refactor")
        assert result["granted"] is True
        assert result["conflicts"] == []
        assert result["claim"].repo == "AxonRelay/core"

    def test_repo_defaults_to_the_sessions_workspace(self, db, alice):
        result = coordination.claim_territory(db, session_id=alice.id, paths=["docs"])
        assert result["claim"].repo == "AxonRelay/core"

    def test_paths_are_normalized_and_deduplicated(self, db, alice):
        result = coordination.claim_territory(db, session_id=alice.id, paths=["./backend/app/", "backend/app", "docs"])
        assert result["claim"].paths == ["backend/app", "docs"]

    def test_an_overlapping_claim_from_another_clone_is_refused(self, db, alice, bob):
        coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"], reason="mine")
        result = coordination.claim_territory(db, session_id=bob.id, paths=["backend/app/crud.py"])

        assert result["granted"] is False
        assert result["claim"] is None
        assert len(result["conflicts"]) == 1

    def test_a_refusal_says_who_holds_it_and_where(self, db, alice, bob):
        coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"], reason="mine")
        conflict = coordination.claim_territory(db, session_id=bob.id, paths=["backend/app/crud.py"])["conflicts"][0]

        assert conflict["holder"]["actor"] == "claude"
        assert conflict["holder"]["host"] == "mbp16"
        assert conflict["holder"]["clone_path"] == "/Users/dev/workspace/core"
        assert conflict["overlapping_paths"] == ["backend/app"]
        assert conflict["reason"] == "mine"

    def test_disjoint_claims_coexist(self, db, alice, bob):
        coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])
        result = coordination.claim_territory(db, session_id=bob.id, paths=["frontend/src"])
        assert result["granted"] is True

    def test_a_session_never_conflicts_with_itself(self, db, alice):
        coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])
        again = coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])
        assert again["granted"] is True

    def test_two_shared_claims_coexist(self, db, alice, bob):
        coordination.claim_territory(db, session_id=alice.id, paths=["docs"], mode=models.ClaimModeEnum.SHARED)
        result = coordination.claim_territory(db, session_id=bob.id, paths=["docs"], mode=models.ClaimModeEnum.SHARED)
        assert result["granted"] is True

    def test_exclusive_collides_with_an_existing_shared_claim(self, db, alice, bob):
        coordination.claim_territory(db, session_id=alice.id, paths=["docs"], mode=models.ClaimModeEnum.SHARED)
        result = coordination.claim_territory(
            db, session_id=bob.id, paths=["docs"], mode=models.ClaimModeEnum.EXCLUSIVE
        )
        assert result["granted"] is False

    def test_shared_collides_with_an_existing_exclusive_claim(self, db, alice, bob):
        coordination.claim_territory(db, session_id=alice.id, paths=["docs"], mode=models.ClaimModeEnum.EXCLUSIVE)
        result = coordination.claim_territory(db, session_id=bob.id, paths=["docs"], mode=models.ClaimModeEnum.SHARED)
        assert result["granted"] is False

    def test_claims_in_another_repo_do_not_collide(self, db, alice):
        elsewhere = coordination.register_session(
            db, actor_name="codex", host="studio", repo="other/repo", clone_path="/tmp/other"
        )
        coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])
        result = coordination.claim_territory(db, session_id=elsewhere.id, paths=["backend/app"])
        assert result["granted"] is True

    def test_force_grants_over_a_conflict_and_records_the_override(self, db, alice, bob):
        first = coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])
        forced = coordination.claim_territory(db, session_id=bob.id, paths=["backend/app"], force=True)

        assert forced["granted"] is True
        assert forced["claim"].forced_over == [first["claim"].id]
        assert len(forced["conflicts"]) == 1

        # The displaced claim is retired, so the board shows one holder and a
        # later check by the forcing session is clean.
        db.refresh(first["claim"])
        assert first["claim"].status == models.ClaimStatusEnum.RELEASED
        assert (
            coordination.find_conflicts(db, repo=first["claim"].repo, paths=["backend/app"], exclude_session_id=bob.id)
            == []
        )

    def test_an_expired_claim_stops_blocking(self, db, alice, bob):
        result = coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])
        result["claim"].expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()

        assert coordination.live_claims(db) == []
        assert coordination.claim_territory(db, session_id=bob.id, paths=["backend/app"])["granted"]

    def test_ttl_is_capped(self, db, alice):
        result = coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"], ttl_minutes=999_999)
        horizon = datetime.utcnow() + timedelta(minutes=coordination.MAX_CLAIM_TTL_MINUTES)
        assert result["claim"].expires_at <= horizon + timedelta(seconds=5)

    def test_releasing_frees_the_territory(self, db, alice, bob):
        result = coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])
        released = coordination.release_claim(db, result["claim"].id)

        assert released.status == models.ClaimStatusEnum.RELEASED
        assert released.released_at is not None
        assert coordination.claim_territory(db, session_id=bob.id, paths=["backend/app"])["granted"]

    def test_releasing_twice_is_harmless(self, db, alice):
        result = coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])
        first = coordination.release_claim(db, result["claim"].id).released_at
        assert coordination.release_claim(db, result["claim"].id).released_at == first

    def test_release_session_claims_returns_the_count(self, db, alice):
        coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])
        coordination.claim_territory(db, session_id=alice.id, paths=["docs"])
        assert coordination.release_session_claims(db, alice.id) == 2
        assert coordination.release_session_claims(db, alice.id) == 0

    def test_check_conflicts_does_not_create_a_claim(self, db, alice, bob):
        coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])
        conflicts = coordination.find_conflicts(
            db, repo="AxonRelay/core", paths=["backend/app"], exclude_session_id=bob.id
        )
        assert len(conflicts) == 1
        assert db.query(models.Claim).count() == 1

    def test_claiming_needs_at_least_one_path(self, db, alice):
        with pytest.raises(ValueError, match="at least one path"):
            coordination.claim_territory(db, session_id=alice.id, paths=[])

    def test_an_ended_session_cannot_claim(self, db, alice):
        coordination.end_session(db, alice.id)
        with pytest.raises(ValueError, match="not active"):
            coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"])


class TestRelays:
    def test_a_directed_relay_reaches_only_its_recipient(self, db, alice, bob):
        third = coordination.register_session(
            db, actor_name="gemini", host="mbp16", repo="AxonRelay/core", clone_path="/tmp/c3"
        )
        coordination.send_relay(
            db,
            from_session_id=alice.id,
            to_actor_id=bob.actor_id,
            subject="heads up",
            body="I am about to rewrite the migration chain",
            kind=models.RelayKindEnum.WARNING,
        )

        assert len(coordination.read_inbox(db, bob.id)) == 1
        assert coordination.read_inbox(db, third.id) == []

    def test_a_broadcast_reaches_everyone_but_the_sender(self, db, alice, bob):
        coordination.send_relay(db, from_session_id=alice.id, subject="fleet-wide notice")

        assert len(coordination.read_inbox(db, bob.id)) == 1
        assert coordination.read_inbox(db, alice.id) == []

    def test_a_repo_relay_reaches_that_repo_only(self, db, alice, bob):
        elsewhere = coordination.register_session(
            db, actor_name="codex", host="studio", repo="other/repo", clone_path="/tmp/other"
        )
        coordination.send_relay(db, from_session_id=alice.id, to_repo="AxonRelay/core", subject="repo notice")

        assert len(coordination.read_inbox(db, bob.id)) == 1
        assert coordination.read_inbox(db, elsewhere.id) == []

    def test_a_workspace_relay_reaches_that_clone_only(self, db, alice, bob):
        coordination.send_relay(db, from_session_id=bob.id, to_workspace_id=alice.workspace_id, subject="clone notice")
        assert len(coordination.read_inbox(db, alice.id)) == 1
        assert coordination.read_inbox(db, bob.id) == []

    def test_a_relay_waits_for_a_recipient_that_registers_later(self, db, alice):
        coordination.send_relay(db, from_session_id=alice.id, subject="posted before you arrived")
        latecomer = coordination.register_session(
            db, actor_name="codex", host="studio", repo="AxonRelay/core", clone_path="/tmp/late"
        )
        assert len(coordination.read_inbox(db, latecomer.id)) == 1

    def test_the_inbox_names_the_sender_and_where_they_are(self, db, alice, bob):
        coordination.send_relay(db, from_session_id=alice.id, to_actor_id=bob.actor_id, subject="hi")
        entry = coordination.read_inbox(db, bob.id)[0]

        assert entry["from"]["actor"] == "claude"
        assert entry["from"]["host"] == "mbp16"
        assert entry["from"]["clone_path"] == "/Users/dev/workspace/core"

    def test_reading_is_idempotent_until_acked(self, db, alice, bob):
        coordination.send_relay(db, from_session_id=alice.id, to_actor_id=bob.actor_id, subject="hi")
        assert len(coordination.read_inbox(db, bob.id)) == 1
        assert len(coordination.read_inbox(db, bob.id)) == 1

    def test_acking_clears_it_from_that_inbox_only(self, db, alice, bob):
        third = coordination.register_session(
            db, actor_name="gemini", host="mbp16", repo="AxonRelay/core", clone_path="/tmp/c3"
        )
        relay = coordination.send_relay(db, from_session_id=alice.id, subject="broadcast")

        coordination.ack_relay(db, relay_id=relay.id, session_id=bob.id, note="seen")

        assert coordination.read_inbox(db, bob.id) == []
        assert len(coordination.read_inbox(db, third.id)) == 1

    def test_an_acked_relay_is_still_retrievable(self, db, alice, bob):
        relay = coordination.send_relay(db, from_session_id=alice.id, subject="broadcast")
        coordination.ack_relay(db, relay_id=relay.id, session_id=bob.id)

        entries = coordination.read_inbox(db, bob.id, include_acked=True)
        assert len(entries) == 1
        assert entries[0]["acked"] is True

    def test_the_ack_receipt_records_who_saw_it(self, db, alice, bob):
        relay = coordination.send_relay(db, from_session_id=alice.id, subject="broadcast")
        receipt = coordination.ack_relay(db, relay_id=relay.id, session_id=bob.id, note="on it")

        assert receipt.acked_at is not None
        assert receipt.ack_note == "on it"
        assert receipt.session_id == bob.id

    def test_acking_without_reading_first_still_works(self, db, alice, bob):
        relay = coordination.send_relay(db, from_session_id=alice.id, subject="broadcast")
        assert coordination.ack_relay(db, relay_id=relay.id, session_id=bob.id).acked_at is not None

    def test_replies_thread_to_the_original(self, db, alice, bob):
        question = coordination.send_relay(
            db,
            from_session_id=alice.id,
            to_actor_id=bob.actor_id,
            subject="which migration head?",
            kind=models.RelayKindEnum.QUESTION,
        )
        answer = coordination.send_relay(
            db,
            from_session_id=bob.id,
            to_actor_id=alice.actor_id,
            subject="re: which migration head?",
            kind=models.RelayKindEnum.ANSWER,
            in_reply_to_id=question.id,
        )
        assert answer.in_reply_to_id == question.id

    def test_the_inbox_is_oldest_first(self, db, alice, bob):
        for n in range(3):
            coordination.send_relay(db, from_session_id=alice.id, to_actor_id=bob.actor_id, subject=f"m{n}")
        assert [e["subject"] for e in coordination.read_inbox(db, bob.id)] == ["m0", "m1", "m2"]

    def test_sending_from_an_unknown_session_is_rejected(self, db):
        with pytest.raises(ValueError, match="not found"):
            coordination.send_relay(db, from_session_id=999, subject="ghost")

    def test_reading_an_unknown_inbox_is_rejected(self, db):
        with pytest.raises(ValueError, match="not found"):
            coordination.read_inbox(db, 999)


class TestBoard:
    def test_the_board_shows_the_whole_fleet(self, db, alice, bob):
        coordination.claim_territory(db, session_id=alice.id, paths=["backend/app"], reason="refactor")
        coordination.send_relay(db, from_session_id=bob.id, subject="anyone touching docs?")

        board = coordination.board(db)

        assert {s["actor"] for s in board["sessions"]} == {"claude", "codex"}
        assert board["claims"][0]["paths"] == ["backend/app"]
        assert board["claims"][0]["holder"]["actor"] == "claude"
        assert board["open_relays"][0]["subject"] == "anyone touching docs?"

    def test_the_board_can_be_scoped_to_one_repo(self, db, alice):
        coordination.register_session(db, actor_name="codex", host="studio", repo="other/repo", clone_path="/tmp/other")
        board = coordination.board(db, repo="AxonRelay/core")

        assert len(board["sessions"]) == 1
        assert board["repo"] == "AxonRelay/core"

    def test_the_board_flags_stale_sessions(self, db, alice):
        alice.last_heartbeat_at = datetime.utcnow() - timedelta(hours=3)
        db.commit()
        assert coordination.board(db)["sessions"][0]["stale"] is True

    def test_acked_relays_leave_the_board(self, db, alice, bob):
        relay = coordination.send_relay(db, from_session_id=alice.id, subject="handled")
        coordination.ack_relay(db, relay_id=relay.id, session_id=bob.id)
        assert coordination.board(db)["open_relays"] == []

    def test_a_broadcast_read_by_several_peers_is_listed_once(self, db, alice, bob):
        """Several unacked receipts on one relay must not multiply it on the board."""
        third = coordination.register_session(
            db, actor_name="gemini", host="mbp16", repo="AxonRelay/core", clone_path="/tmp/c3"
        )
        coordination.send_relay(db, from_session_id=alice.id, subject="broadcast")
        coordination.read_inbox(db, bob.id)
        coordination.read_inbox(db, third.id)

        assert [r["subject"] for r in coordination.board(db)["open_relays"]] == ["broadcast"]

    def test_a_broadcast_stays_open_until_every_reader_acks(self, db, alice, bob):
        third = coordination.register_session(
            db, actor_name="gemini", host="mbp16", repo="AxonRelay/core", clone_path="/tmp/c3"
        )
        relay = coordination.send_relay(db, from_session_id=alice.id, subject="broadcast")
        coordination.read_inbox(db, bob.id)
        coordination.read_inbox(db, third.id)

        coordination.ack_relay(db, relay_id=relay.id, session_id=bob.id)
        assert len(coordination.board(db)["open_relays"]) == 1

        coordination.ack_relay(db, relay_id=relay.id, session_id=third.id)
        assert coordination.board(db)["open_relays"] == []

    def test_an_empty_board_is_still_well_formed(self, db):
        board = coordination.board(db)
        assert board["sessions"] == []
        assert board["claims"] == []
        assert board["open_relays"] == []
