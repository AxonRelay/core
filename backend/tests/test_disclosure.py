"""What a shared boundary may say about the coordination plane (issue #26).

The board records a host name, an absolute clone path, a git directory and
four free-text fields. On a shared instance that is a description of somebody's
machine and, in the prose, sometimes of the work. `app/disclosure.py` decides
what a response may contain; these tests pin the decision:

* an explicit field allowlist per view, asserted as an equality so a field
  added to a serializer without a decision fails here rather than leaking;
* the safe-mode views carry opaque references, enums and counts — and none of
  the raw identity, checked by searching the whole rendered payload for the
  values, not just the keys;
* an agent's config values never leave a serializer, in either mode, because a
  config blob is where an operator puts credentials;
* a URL with a credential, a query, a fragment or an odd scheme is refused at
  the model boundary, and the refusal does not repeat the URL.
"""

import json

import pytest

from app import disclosure, models
from app.mcp import serializers

HOST = "gazer.local"
CLONE = "/Users/someone/workspace/AxonRelay/core"
GIT_DIR = "/Users/someone/workspace/AxonRelay/core/.git"
FOCUS = "rewriting the payroll exporter for ACME"
REASON = "refactoring the ACME billing module"
SUBJECT = "please review the ACME migration"
BODY = "the numbers in tmp/acme-q3.csv look wrong"
LABEL = "someone's laptop"
BRANCH = "feature/acme-payroll"

RAW_VALUES = (HOST, CLONE, GIT_DIR, FOCUS, REASON, SUBJECT, BODY, LABEL, BRANCH, "writer-bot")


@pytest.fixture
def full_text(monkeypatch):
    from app import safe_envelope

    monkeypatch.delenv(safe_envelope.SAFE_MODE_ENV, raising=False)
    monkeypatch.delenv(safe_envelope.PUBLIC_IDENTIFIERS_ENV, raising=False)


@pytest.fixture
def safe(monkeypatch):
    from app import safe_envelope

    monkeypatch.setenv(safe_envelope.SAFE_MODE_ENV, "1")
    monkeypatch.delenv(safe_envelope.PUBLIC_IDENTIFIERS_ENV, raising=False)


@pytest.fixture
def board(db, self_actor):
    """One of everything, with values a shared instance must not repeat."""
    from app import coordination

    actor = models.Actor(type=models.ActorTypeEnum.AI, name="writer-bot", opaque_id=disclosure.new_opaque_id())
    db.add(actor)
    db.commit()

    session = coordination.register_session(
        db,
        actor_name="writer-bot",
        host=HOST,
        repo="AxonRelay/core",
        clone_path=CLONE,
        branch=BRANCH,
        focus=FOCUS,
        focus_code=models.FocusCodeEnum.IMPLEMENTING,
        label=LABEL,
        git_dir=GIT_DIR,
    )
    workspace = session.workspace
    workspace.opaque_id = workspace.opaque_id or disclosure.new_opaque_id()
    session.actor.opaque_id = session.actor.opaque_id or disclosure.new_opaque_id()
    db.commit()

    claim = coordination.claim_territory(
        db,
        session_id=session.id,
        paths=["backend/app/payroll.py", "backend/app/acme.py"],
        reason=REASON,
        reason_code=models.ClaimReasonCodeEnum.REFACTORING,
    )["claim"]
    relay = coordination.send_relay(
        db,
        from_session_id=session.id,
        subject=SUBJECT,
        body=BODY,
        code=models.RelayCodeEnum.NEEDS_REVIEW,
    )
    return {"session": session, "workspace": workspace, "claim": claim, "relay": relay, "actor": session.actor}


def _rendered(payload) -> str:
    return json.dumps(payload, default=str)


# ------------------------------------------------------------- allowlists


def test_each_view_emits_exactly_its_allowlist(db, board, full_text):
    assert set(disclosure.actor_view(board["actor"])) == set(disclosure.ACTOR_FIELDS)
    assert set(disclosure.workspace_view(board["workspace"])) == set(disclosure.WORKSPACE_FIELDS)
    assert set(disclosure.session_view(board["session"])) == set(disclosure.SESSION_FIELDS)
    assert set(disclosure.claim_view(board["claim"])) == set(disclosure.CLAIM_FIELDS)
    assert set(disclosure.relay_view(board["relay"])) == set(disclosure.RELAY_FIELDS)


def test_each_safe_view_emits_exactly_its_allowlist(db, board, safe):
    assert set(disclosure.actor_view(board["actor"])) == set(disclosure.ACTOR_SAFE_FIELDS)
    assert set(disclosure.workspace_view(board["workspace"])) == set(disclosure.WORKSPACE_SAFE_FIELDS)
    assert set(disclosure.session_view(board["session"])) == set(disclosure.SESSION_SAFE_FIELDS)
    assert set(disclosure.claim_view(board["claim"])) == set(disclosure.CLAIM_SAFE_FIELDS)
    assert set(disclosure.relay_view(board["relay"])) == set(disclosure.RELAY_SAFE_FIELDS)


def test_the_safe_allowlists_are_a_subset_of_what_exists():
    """A safe field must be a real field or a named derivation, not an invention."""
    derived = {"actor_ref", "workspace_ref", "path_count", "forced"}
    for full, restricted in (
        (disclosure.ACTOR_FIELDS, disclosure.ACTOR_SAFE_FIELDS),
        (disclosure.WORKSPACE_FIELDS, disclosure.WORKSPACE_SAFE_FIELDS),
        (disclosure.SESSION_FIELDS, disclosure.SESSION_SAFE_FIELDS),
        (disclosure.CLAIM_FIELDS, disclosure.CLAIM_SAFE_FIELDS),
        (disclosure.RELAY_FIELDS, disclosure.RELAY_SAFE_FIELDS),
    ):
        assert set(restricted) - set(full) <= derived


# ---------------------------------------------------------- what safe mode hides


@pytest.mark.parametrize("view", ["actor", "workspace", "session", "claim", "relay"])
def test_no_raw_identity_survives_into_a_safe_response(db, board, safe, view):
    rendered = _rendered(getattr(disclosure, f"{view}_view")(board[view if view != "session" else "session"]))
    for value in RAW_VALUES:
        assert value not in rendered, f"{view} view leaked {value!r}"


def test_a_safe_session_says_what_it_is_doing_without_saying_it(db, board, safe):
    payload = disclosure.session_view(board["session"])

    assert payload["focus_code"] == "implementing"
    assert payload["actor"]["actor_ref"] == board["actor"].opaque_id
    assert payload["workspace"]["workspace_ref"] == board["workspace"].opaque_id
    assert "focus" not in payload and "branch" not in payload


def test_a_safe_claim_sizes_the_overlap_without_naming_the_files(db, board, safe):
    payload = disclosure.claim_view(board["claim"])

    assert payload["path_count"] == 2
    assert payload["reason_code"] == "refactoring"
    assert payload["forced"] is False
    assert "paths" not in payload and "reason" not in payload and "forced_over" not in payload


def test_a_safe_relay_carries_its_intent_and_not_its_words(db, board, safe):
    payload = disclosure.relay_view(board["relay"])

    assert payload["code"] == "needs_review"
    assert payload["kind"]
    assert "subject" not in payload and "body" not in payload


def test_a_repository_slug_is_a_public_identifier(db, board, safe, monkeypatch):
    from app import safe_envelope

    assert disclosure.workspace_view(board["workspace"])["repo"] is None
    assert disclosure.claim_view(board["claim"])["repo"] is None

    monkeypatch.setenv(safe_envelope.PUBLIC_IDENTIFIERS_ENV, "1")
    assert disclosure.workspace_view(board["workspace"])["repo"] == "AxonRelay/core"
    assert disclosure.claim_view(board["claim"])["repo"] == "AxonRelay/core"


def test_full_text_mode_is_unchanged(db, board, full_text):
    session = disclosure.session_view(board["session"])
    assert session["focus"] == FOCUS
    assert session["branch"] == BRANCH
    assert session["workspace"]["host"] == HOST
    assert session["workspace"]["clone_path"] == CLONE
    assert disclosure.claim_view(board["claim"])["paths"] == ["backend/app/payroll.py", "backend/app/acme.py"]
    assert disclosure.relay_view(board["relay"])["subject"] == SUBJECT


def test_the_serializers_are_the_policy(db, board, safe):
    """MCP and REST share these, so the boundary is one decision, not two."""
    assert serializers.session_to_dict(board["session"]) == disclosure.session_view(board["session"])
    assert serializers.claim_to_dict(board["claim"]) == disclosure.claim_view(board["claim"])
    assert serializers.relay_to_dict(board["relay"]) == disclosure.relay_view(board["relay"])
    assert serializers.actor_to_dict(board["actor"]) == disclosure.actor_view(board["actor"])


def test_the_whole_board_is_clean_in_safe_mode(db, board, safe):
    from app import coordination

    rendered = _rendered(coordination.board(db))
    for value in RAW_VALUES:
        assert value not in rendered, f"the board leaked {value!r}"


def test_the_board_views_emit_exactly_their_allowlists(db, board, full_text):
    from app import coordination

    snapshot = coordination.board(db)
    assert set(snapshot["sessions"][0]) == set(disclosure.BOARD_SESSION_FIELDS)
    assert set(snapshot["claims"][0]["holder"]) == set(disclosure.HOLDER_FIELDS)
    entry = coordination.read_inbox(db, session_id=board["session"].id)
    assert entry == [] or set(entry[0]) == set(disclosure.INBOX_FIELDS)


def test_the_safe_board_views_emit_exactly_their_allowlists(db, board, safe):
    from app import coordination

    snapshot = coordination.board(db)
    assert set(snapshot["sessions"][0]) == set(disclosure.BOARD_SESSION_SAFE_FIELDS)
    assert set(snapshot["claims"][0]["holder"]) == set(disclosure.HOLDER_SAFE_FIELDS)


def test_an_inbox_is_clean_in_safe_mode(db, board, safe):
    """The relay a peer actually reads is the one most likely to quote the work."""
    from app import coordination

    other = coordination.register_session(
        db, actor_name="reader-bot", host="other-host", repo="AxonRelay/core", clone_path="/other/clone"
    )
    coordination.send_relay(
        db,
        from_session_id=board["session"].id,
        subject=SUBJECT,
        body=BODY,
        code=models.RelayCodeEnum.NEEDS_REVIEW,
        to_actor_id=other.actor_id,
    )
    entries = coordination.read_inbox(db, session_id=other.id)

    assert entries, "the relay should be addressed to this session"
    assert set(entries[0]) == set(disclosure.INBOX_SAFE_FIELDS)
    assert set(entries[0]["from"]) == set(disclosure.INBOX_FROM_SAFE_FIELDS)
    rendered = _rendered(entries)
    for value in RAW_VALUES:
        assert value not in rendered, f"the inbox leaked {value!r}"
    assert entries[0]["code"] == "needs_review"


def test_a_conflict_refusal_names_the_holder_without_locating_them(db, board, safe):
    """Being refused must still tell you whom to talk to - a ref is enough for a relay."""
    from app import coordination

    other = coordination.register_session(
        db, actor_name="reader-bot", host="other-host", repo="AxonRelay/core", clone_path="/other/clone"
    )
    result = coordination.claim_territory(db, session_id=other.id, paths=["backend/app/payroll.py"])

    assert result["granted"] is False
    rendered = _rendered(result["conflicts"])
    for value in RAW_VALUES:
        assert value not in rendered, f"the conflict leaked {value!r}"
    conflict = result["conflicts"][0]
    assert set(conflict) == set(disclosure.CONFLICT_SAFE_FIELDS)
    assert conflict["overlap_count"] == 1
    assert conflict["holder"]["actor_ref"] == board["actor"].opaque_id


def test_a_conflict_names_the_files_in_full_text_mode(db, board, full_text):
    from app import coordination

    other = coordination.register_session(
        db, actor_name="reader-bot", host="other-host", repo="AxonRelay/core", clone_path="/other/clone"
    )
    result = coordination.claim_territory(db, session_id=other.id, paths=["backend/app/payroll.py"])

    conflict = result["conflicts"][0]
    assert set(conflict) == set(disclosure.CONFLICT_FIELDS)
    assert conflict["overlapping_paths"] == ["backend/app/payroll.py"]
    assert conflict["reason"] == REASON


def test_a_git_resource_conflict_follows_the_same_policy(db, board, safe):
    from app import coordination

    coordination.claim_resource(
        db, session_id=board["session"].id, resource=models.ClaimResourceEnum.STASH, reason=REASON
    )
    other = coordination.register_session(
        db, actor_name="reader-bot", host=HOST, repo="AxonRelay/core", clone_path=CLONE + "-wt", git_dir=GIT_DIR
    )
    result = coordination.claim_resource(db, session_id=other.id, resource=models.ClaimResourceEnum.STASH)

    assert result["granted"] is False
    assert set(result["conflicts"][0]) == set(disclosure.CONFLICT_SAFE_FIELDS)
    rendered = _rendered(result["conflicts"])
    for value in RAW_VALUES:
        assert value not in rendered, f"the resource conflict leaked {value!r}"


# ------------------------------------------------------- opaque identifiers


def test_an_opaque_id_is_stable_and_unguessable(db, board):
    first = board["workspace"].opaque_id
    db.expire_all()
    assert db.query(models.Workspace).first().opaque_id == first
    assert len(first) >= 12
    assert {disclosure.new_opaque_id() for _ in range(50)}.__len__() == 50


def test_a_row_without_one_gets_one_on_demand(db, self_actor):
    """Migration 012 backfills; this covers a row built from the models instead."""
    assert self_actor.opaque_id is None
    minted = disclosure.ensure_opaque_id(db, self_actor)
    assert minted and disclosure.ensure_opaque_id(db, self_actor) == minted


# ------------------------------------------------------------ config secrets


@pytest.mark.parametrize("mode", ["full_text", "safe"])
def test_agent_config_values_never_leave_a_serializer(db, request, mode):
    request.getfixturevalue(mode)
    from app import crud

    agent = crud.create_agent_definition(
        db,
        name="configured",
        agent_type=models.AgentTypeEnum.WRITER,
        config={"model": "claude-opus-5", "api_key": "sk-secret-value", "temperature": 0.2},
    )

    payload = serializers.agent_definition_to_dict(agent)

    assert payload["config_keys"] == ["api_key", "model", "temperature"]
    assert "config" not in payload
    assert "sk-secret-value" not in _rendered(payload)
    assert agent.config_keys == ["api_key", "model", "temperature"]


def test_config_keys_handles_the_shapes_a_json_column_can_hold():
    assert disclosure.config_keys(None) is None
    assert disclosure.config_keys({}) == []
    assert disclosure.config_keys([1, 2]) == []
    assert disclosure.config_keys({"b": 1, "a": 2}) == ["a", "b"]


# ------------------------------------------------------------------- URLs


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ssh://example.invalid/x/y",
        "https://u:t0ken@127.0.0.1/x",
        "https://example.invalid/x?access_token=t0ken",
        "https://example.invalid/x#section",
        "https://",
    ],
)
def test_an_unsafe_url_is_refused_at_the_model_boundary(db, url):
    from app import crud

    task = crud.create_task(db, thread_id="t-url", title="t")
    with pytest.raises(disclosure.UnsafeURL) as exc:
        models.ExternalLink(task_id=task.id, link_type="issue", url=url)
    assert "t0ken" not in str(exc.value)
    assert url not in str(exc.value)


def test_a_plain_https_url_is_kept(db):
    from app import crud

    task = crud.create_task(db, thread_id="t-url-ok", title="t")
    link = models.ExternalLink(task_id=task.id, link_type="issue", url="https://github.com/AxonRelay/core/issues/26")
    db.add(link)
    db.commit()
    assert link.url == "https://github.com/AxonRelay/core/issues/26"
