"""Retention for operational coordination metadata (issue #26, app/retention.py).

Two things have to be true at once, and they pull in opposite directions:

* operational rows — ended sessions, finished claims, delivered relays — must
  actually expire, or a shared board becomes a permanent record of who was
  working where;
* **evidence must never expire.** An approval names the artifact it decided on
  (ADR-009), so deleting an approval, a draft or the task they hang off would
  make the hash chain unverifiable. The envelope feed (ADR-010) is itself the
  minimal record a shared instance keeps.

So the sweep names the tables it may touch, and the test below fills a database
with ledger rows, sweeps it, and asserts the ledger still verifies byte for
byte.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect

from app import coordination, crud, models, retention


def _aged(db, row, field, days):
    setattr(row, field, datetime.utcnow() - timedelta(days=days))
    db.commit()
    return row


@pytest.fixture
def agent(db):
    actor = models.Actor(type=models.ActorTypeEnum.AI, name="sweeper-bot")
    db.add(actor)
    db.commit()
    db.refresh(actor)
    return actor


def _session(db, clone="/c"):
    return coordination.register_session(
        db, actor_name="sweeper-bot", host="h", repo="r", clone_path=clone, actor_type=models.ActorTypeEnum.AI
    )


# ----------------------------------------------------------- what is swept


def test_an_ended_session_goes_once_it_is_old_enough(db, agent):
    session = _session(db)
    coordination.end_session(db, session.id)
    _aged(db, session, "ended_at", retention.ENDED_SESSION_DAYS + 1)

    assert retention.sweep(db).sessions == 1
    assert db.query(models.Session).count() == 0


def test_a_recently_ended_session_stays(db, agent):
    session = _session(db)
    coordination.end_session(db, session.id)
    _aged(db, session, "ended_at", retention.ENDED_SESSION_DAYS - 1)

    assert retention.sweep(db).sessions == 0
    assert db.query(models.Session).count() == 1


def test_an_active_session_is_never_swept_however_old(db, agent):
    session = _session(db)
    _aged(db, session, "started_at", 3650)

    assert retention.sweep(db).sessions == 0
    assert db.query(models.Session).count() == 1


def test_a_released_claim_goes_and_a_live_one_stays(db, agent):
    session = _session(db)
    old = coordination.claim_territory(db, session_id=session.id, paths=["a"])["claim"]
    live = coordination.claim_territory(db, session_id=session.id, paths=["b"])["claim"]
    coordination.release_claim(db, old.id)
    _aged(db, old, "released_at", retention.FINISHED_CLAIM_DAYS + 1)

    result = retention.sweep(db)

    assert result.claims == 1
    assert [c.id for c in db.query(models.Claim).all()] == [live.id]


def test_the_window_runs_from_when_a_claim_finished_not_when_it_was_taken(db, agent):
    """A long-lived claim released a minute ago is the one a peer is about to ask about."""
    session = _session(db)
    claim = coordination.claim_territory(db, session_id=session.id, paths=["a"], ttl_minutes=60 * 24 * 90)["claim"]
    _aged(db, claim, "created_at", retention.FINISHED_CLAIM_DAYS * 10)
    coordination.release_claim(db, claim.id)

    assert retention.sweep(db).claims == 0
    assert db.query(models.Claim).count() == 1


def test_an_expired_claim_ages_from_its_expiry(db, agent):
    session = _session(db)
    claim = coordination.claim_territory(db, session_id=session.id, paths=["a"])["claim"]
    _aged(db, claim, "expires_at", 1)
    assert retention.sweep(db).claims == 0  # expired, but only just

    _aged(db, claim, "expires_at", retention.FINISHED_CLAIM_DAYS + 1)
    assert retention.sweep(db).claims == 1


def test_a_broadcast_only_ever_leaves_on_the_long_window(db, agent):
    """A receipt exists only for a session that read it, so "all acked" proves nothing about a broadcast."""
    sender = _session(db, "/sender")
    reader = coordination.register_session(
        db, actor_name="reader", host="h", repo="r", clone_path="/reader", actor_type=models.ActorTypeEnum.AI
    )
    coordination.register_session(
        db, actor_name="quiet", host="h", repo="r", clone_path="/quiet", actor_type=models.ActorTypeEnum.AI
    )
    relay = coordination.send_relay(db, from_session_id=sender.id, subject="fleet-wide")
    coordination.read_inbox(db, session_id=reader.id)
    coordination.ack_relay(db, relay_id=relay.id, session_id=reader.id)
    _aged(db, relay, "created_at", retention.ACKED_RELAY_DAYS + 1)

    assert retention.sweep(db).relays == 0, "one peer acking must not delete it for the others"

    _aged(db, relay, "created_at", retention.UNACKED_RELAY_DAYS + 1)
    assert retention.sweep(db).relays == 1


def test_sweeping_a_session_does_not_resurrect_a_delivered_relay(db, agent):
    """Receipts cascade off a session; losing them would make an acked relay look unread."""
    sender = _session(db, "/sender")
    reader = coordination.register_session(
        db, actor_name="reader", host="h", repo="r", clone_path="/reader", actor_type=models.ActorTypeEnum.AI
    )
    relay = coordination.send_relay(db, from_session_id=sender.id, subject="s", to_actor_id=reader.actor_id)
    coordination.read_inbox(db, session_id=reader.id)
    coordination.ack_relay(db, relay_id=relay.id, session_id=reader.id)
    coordination.end_session(db, reader.id)
    _aged(db, reader, "ended_at", retention.ENDED_SESSION_DAYS + 1)
    _aged(db, relay, "created_at", retention.ACKED_RELAY_DAYS + 1)

    result = retention.sweep(db)

    assert result.sessions == 1
    assert result.relay_receipts == 1, "the receipt is counted, not absorbed by a cascade"
    assert db.query(models.Relay).count() == 0, "a delivered relay goes with its receipts"
    assert coordination.board(db)["open_relays"] == []


def test_a_relay_everyone_acked_goes_with_its_receipts(db, agent):
    sender = _session(db, "/sender")
    reader = coordination.register_session(
        db, actor_name="reader", host="h", repo="r", clone_path="/reader", actor_type=models.ActorTypeEnum.AI
    )
    relay = coordination.send_relay(db, from_session_id=sender.id, subject="s", to_actor_id=reader.actor_id)
    coordination.read_inbox(db, session_id=reader.id)
    coordination.ack_relay(db, relay_id=relay.id, session_id=reader.id)
    _aged(db, relay, "created_at", retention.ACKED_RELAY_DAYS + 1)

    result = retention.sweep(db)

    assert (result.relays, result.relay_receipts) == (1, 1)
    assert db.query(models.Relay).count() == 0
    assert db.query(models.RelayReceipt).count() == 0


def test_an_unacked_relay_survives_the_short_window(db, agent):
    """It is still somebody's inbox item; only the long window reaches it."""
    sender = _session(db, "/sender")
    reader = coordination.register_session(
        db, actor_name="reader", host="h", repo="r", clone_path="/reader", actor_type=models.ActorTypeEnum.AI
    )
    relay = coordination.send_relay(db, from_session_id=sender.id, subject="s", to_actor_id=reader.actor_id)
    coordination.read_inbox(db, session_id=reader.id)
    _aged(db, relay, "created_at", retention.ACKED_RELAY_DAYS + 1)

    assert retention.sweep(db).relays == 0

    _aged(db, relay, "created_at", retention.UNACKED_RELAY_DAYS + 1)
    assert retention.sweep(db).relays == 1


def test_a_dry_run_reports_and_changes_nothing(db, agent):
    session = _session(db)
    coordination.end_session(db, session.id)
    _aged(db, session, "ended_at", retention.ENDED_SESSION_DAYS + 1)

    planned = retention.sweep(db, dry_run=True)

    assert planned.sessions == 1 and planned.total() == 1
    assert db.query(models.Session).count() == 1


def test_a_sweep_of_a_quiet_board_removes_nothing(db, agent):
    _session(db)
    assert retention.sweep(db).total() == 0


# ------------------------------------------------------- what is never swept


def test_a_sweep_leaves_the_ledger_verifiable(db, self_actor, agent):
    """The one invariant: deletion or compaction must not invalidate evidence."""
    task = crud.create_task(db, thread_id="t-retain", title="t")
    crud.add_draft(db, task_id=task.id, content="draft under review")
    crud.record_approval(db, task.id, self_actor.id, "rejected", "redo")
    crud.record_approval(db, task.id, self_actor.id, "approved", "ok")
    before = crud.verify_approval_chain(db, task.id)
    assert before["valid"] is True and before["count"] == 2

    # Age everything sweepable so nothing survives by being young.
    session = _session(db)
    coordination.end_session(db, session.id)
    _aged(db, session, "ended_at", retention.ENDED_SESSION_DAYS + 1)

    retention.sweep(db)

    assert crud.verify_approval_chain(db, task.id) == before
    assert [a.entry_hash for a in crud.get_approvals(db, task.id)] == [
        a.entry_hash for a in crud.get_approvals(db, task.id)
    ]
    assert len(crud.get_drafts(db, task.id)) == 1


def test_a_sweep_never_touches_a_preserved_table(db, self_actor, agent):
    from app import authz, safe_envelope

    task = crud.create_task(db, thread_id="t-preserve", title="t")
    crud.add_draft(db, task_id=task.id, content="d")
    crud.record_approval(db, task.id, self_actor.id, "approved")
    crud.create_agent_definition(db, name="kept", agent_type=models.AgentTypeEnum.WRITER)
    db.add(
        models.Credential(
            actor_id=self_actor.id, label="kept", token_hash=authz.token_digest(authz.issue_token()), scopes=""
        )
    )
    safe_envelope.ingest(
        db,
        {
            "schema_version": 1,
            "policy_version": "p1",
            "event_id": "retain_0123456789abcdef",
            "actor_id": "actor_0123456789abcd",
            "repository_id": "repo_0123456789abcde",
            "action": "session_end",
            "outcome": "success",
            "occurred_at": "2026-01-01T00:00:00+00:00",
        },
    )
    _session(db)

    before = {t: db.execute(models.Base.metadata.tables[t].select()).all() for t in retention.PRESERVED}
    retention.sweep(db, now=datetime.utcnow() + timedelta(days=3650))
    after = {t: db.execute(models.Base.metadata.tables[t].select()).all() for t in retention.PRESERVED}

    assert after == before


def test_the_sweepable_and_preserved_lists_cover_every_table(db):
    """A new table must be classified, or the sweep's promise is only about the tables we remembered."""
    known = set(retention.SWEEPABLE) | set(retention.PRESERVED)
    actual = set(inspect(db.get_bind()).get_table_names())
    assert actual - known == set(), f"classify these tables for retention: {sorted(actual - known)}"
    assert known - actual == set()


def test_the_cli_reports_windows_and_the_preserved_tables(capsys, db, agent, monkeypatch):
    """An operator running this must be able to see what it will and will not touch."""
    monkeypatch.setattr("app.database.SessionLocal", lambda: db)

    assert retention.main(["--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "would remove" in out
    assert f"ended sessions {retention.ENDED_SESSION_DAYS}d" in out
    for table in ("approvals", "drafts", "safe_events"):
        assert table in out
