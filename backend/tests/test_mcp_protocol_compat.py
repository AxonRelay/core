"""Interactive approval, driven over the real MCP wire on both interaction models (issue #30).

Everything else in the suite calls `review_pending_task` as a Python function.
That is exactly the gap this file exists to close: the 2026-07-28 revision
changed *how the question travels*, not what the tool computes, so a test that
never speaks the protocol cannot tell the two models apart and would stay green
while approval broke on a real client.

The two models:

* **up to 2025-11-25** — the server sends a standalone `elicitation/create`
  down the open connection and the `tools/call` stays parked until the human
  answers. Lose the connection, lose the decision.
* **from 2026-07-28** — the call *returns* an `InputRequiredResult` carrying
  the question and an opaque, sealed `requestState`; the client calls the same
  tool again with the answer and that handle. The decision is resumable, and
  because the whole call is replayed, the tool body runs once per round.

Both are driven here by the SDK's own `ClientSession`, over both transports the
server actually offers: in-memory streams (the stdio wire shape, no subprocess)
and Streamable HTTP against a real uvicorn on a free port. Each transport is
exercised independently rather than assumed equivalent, because they differ in
exactly the places that matter - HTTP carries a request object where stdio does
not, which is what the authorization middleware branches on, and the older
interaction model needs a back-channel that only a streaming transport has.

What is pinned:

* the input-required round trip, and accept / decline / cancel;
* the decision binds to the draft that was *shown*, across rounds;
* a draft that changes between rounds is asked again and records nothing;
* a replayed answer round records one decision, not two;
* a fresh connection resumes a handle, an expired one does not, and neither
  does a handle pointed at another task;
* a client that cannot answer a question is told which tools to use instead;
* the published matrix matches the SDK it is written against.
"""

import asyncio
import contextlib
import json
import threading
import time

import mcp_types as types
import pytest
import uvicorn
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.request_state import RequestStateBoundary
from mcp.shared.exceptions import MCPError
from mcp_types.version import SUPPORTED_PROTOCOL_VERSIONS

from app import crud, langgraph_client, models
from app.mcp import compat
from app.mcp import server as mcp_server

HTTP_PATH = "/mcp"

DRAFT = "the draft under review"


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def waiting_task(mcp_db, self_actor):
    """A task parked on WAITING_APPROVAL with one draft to decide on."""
    task = crud.create_task(mcp_db, thread_id="thread-review", title="Review me")
    task.status = models.TaskStatusEnum.WAITING_APPROVAL
    mcp_db.commit()
    mcp_db.refresh(task)
    crud.add_draft(mcp_db, task_id=task.id, content=DRAFT)
    return task


@pytest.fixture
def no_platform(monkeypatch):
    """A Platform stub that actually processes the decision.

    An approval finishes the run; a rejection comes back parked on a revised
    draft. Returning a bare `{}` would be simpler and wrong: the projection
    would leave every task in WAITING_APPROVAL, which is the exact state the
    retry-repair path keys on, so the replay tests would silently be asserting
    the repair instead of the ordinary path.
    """
    resumes = []

    async def _resume(thread_id, payload):
        resumes.append((thread_id, payload))
        if payload["decision"] == "approved":
            drafts = [DRAFT]
            if payload.get("modified_draft"):
                drafts.append(payload["modified_draft"])
            return {"values": {"drafts": drafts, "final_output": "shipped"}}
        return {"next": ["human_approval"], "values": {"drafts": [DRAFT, "revised after rejection"]}}

    monkeypatch.setattr(langgraph_client, "resume_thread", _resume)
    return resumes


@pytest.fixture(params=["stdio", "http"])
def transport(request):
    """Both transports the server offers, exercised independently."""
    return request.param


# ------------------------------------------------------------------ harness


@pytest.fixture(scope="session")
def http_url():
    """The Streamable HTTP endpoint, served by a real uvicorn on a free port.

    A real listener rather than an in-process ASGI shim, because the older
    interaction model needs the server-to-client back-channel: an elicitation
    sent mid-`tools/call` travels down the SSE stream the client is holding
    open, and a transport that buffers a response instead of streaming it
    deadlocks on exactly the flow these tests exist to check. It is also the
    shape `python -m app.mcp.server --http` actually runs.
    """
    app = mcp_server.mcp.streamable_http_app(streamable_http_path=HTTP_PATH, host="127.0.0.1")
    # port=0 lets uvicorn bind whatever is free and report it. Picking a port
    # with a probe socket first would leave a window in which something else
    # can take it, and the failure would look like a bug in the server.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "the Streamable HTTP server did not come up"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}{HTTP_PATH}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        # A daemon thread that outlives the fixture keeps serving this
        # process's shared server object into later tests.
        assert not thread.is_alive(), "the Streamable HTTP server did not shut down"


@pytest.fixture
def connect(transport, http_url):
    """Open a connected `ClientSession` on the transport under test.

    `modern=True` reaches 2026-07-28 through `server/discover`; `modern=False`
    takes the legacy `initialize` handshake, which tops out at 2025-11-25. A
    session built without an `elicitation_callback` declares no elicitation
    capability, which is how the fallback case is expressed on the wire.
    """

    @contextlib.asynccontextmanager
    async def _connect(*, elicitation_callback=None, modern: bool = True):
        if transport == "stdio":
            opener = InMemoryTransport(mcp_server.mcp, raise_exceptions=True)
        else:
            opener = streamable_http_client(http_url)
        async with (
            opener as streams,
            ClientSession(*streams[:2], elicitation_callback=elicitation_callback) as session,
        ):
            if modern:
                await session.discover()
            else:
                await session.initialize()
            yield session

    return _connect


def _payload(result):
    """The tool's dict result, as the client receives it."""
    assert result.content, "tool returned no content"
    return json.loads(result.content[0].text)


def _accept(**fields):
    return types.ElicitResult(action="accept", content=fields)


async def _first_round(session, task_id: int):
    """Round one of an interactive review: the question, unanswered."""
    return await session.call_tool("review_pending_task", {"task_id": task_id}, allow_input_required=True)


async def _answer(session, task_id: int, asked, answer, *, request_state=None):
    """Round two: the same call again, carrying the answer and the handle."""
    key = next(iter(asked.input_requests))
    return await session.call_tool(
        "review_pending_task",
        {"task_id": task_id},
        input_responses={key: answer},
        request_state=request_state if request_state is not None else asked.request_state,
        allow_input_required=True,
    )


@contextlib.asynccontextmanager
async def _stdio(*, elicitation_callback=None, modern: bool = True):
    """A stdio-shaped session, for the checks that are about the handle rather than the wire."""
    async with (
        InMemoryTransport(mcp_server.mcp, raise_exceptions=True) as streams,
        ClientSession(*streams[:2], elicitation_callback=elicitation_callback) as session,
    ):
        if modern:
            await session.discover()
        else:
            await session.initialize()
        yield session


def _boundary() -> RequestStateBoundary:
    """The middleware that seals and verifies the resumable handle."""
    found = [m for m in mcp_server.mcp._lowlevel_server.middleware if isinstance(m, RequestStateBoundary)]
    assert len(found) == 1, "the server should install exactly one request-state boundary"
    return found[0]


# ------------------------------------------------------ the published matrix


def test_the_matrix_names_every_revision_the_sdk_will_negotiate():
    """A revision the SDK gains or drops has to be classified here, not discovered in the field."""
    assert set(compat.SPEC_REVISIONS) == set(SUPPORTED_PROTOCOL_VERSIONS)
    assert compat.INPUT_REQUIRED_SINCE in compat.SPEC_REVISIONS


def test_the_matrix_agrees_with_itself_about_where_the_model_changes():
    older = [v for v in SUPPORTED_PROTOCOL_VERSIONS if not compat.uses_input_required(v)]
    newer = [v for v in SUPPORTED_PROTOCOL_VERSIONS if compat.uses_input_required(v)]
    assert newer == [compat.INPUT_REQUIRED_SINCE]
    assert compat.INPUT_REQUIRED_SINCE not in older
    assert compat.uses_input_required(None) is False


def test_the_declared_sdk_range_is_the_one_that_is_installed():
    """The matrix quotes requirements.txt; a bump there has to reach the claim."""
    import pathlib

    requirements = pathlib.Path(__file__).resolve().parents[1] / "requirements.txt"
    pinned = [line for line in requirements.read_text().splitlines() if line.startswith("mcp>")]
    assert pinned == [f"mcp{compat.SDK_RANGE}"]


def test_the_fallback_tools_exist():
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert set(compat.FALLBACK_TOOLS) <= names


def test_the_resolved_parameters_stay_off_the_model_facing_schema():
    """The pinned draft and the decision are resolved, not arguments.

    If either leaked into the input schema, the model could hand the tool an
    artifact the reviewer never saw - which is the one thing the binding
    exists to prevent.
    """
    tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
    schema = tools["review_pending_task"].input_schema
    assert set(schema["properties"]) == {"task_id"}
    assert schema["required"] == ["task_id"]


def test_the_compat_resource_serves_the_matrix(mcp_db):
    async def go():
        async with _stdio() as session:
            result = await session.read_resource("axonrelay://compat")
            return json.loads(result.contents[0].text)

    assert asyncio.run(go()) == json.loads(json.dumps(compat.matrix()))


# ------------------------------------------------- the input-required round


def test_an_approval_is_an_input_required_round_then_a_bound_decision(
    connect, transport, waiting_task, no_platform, mcp_db
):
    """The 2026-07-28 shape end to end: question, handle, answer, ledger entry."""

    async def go():
        async with connect(elicitation_callback=_unused_callback) as session:
            assert session.protocol_version == compat.INPUT_REQUIRED_SINCE
            asked = await _first_round(session, waiting_task.id)
            assert isinstance(asked, types.InputRequiredResult)
            assert asked.request_state, "the round must hand back a resumable handle"
            question = next(iter(asked.input_requests.values()))
            assert DRAFT in question.params.message
            answered = await _answer(session, waiting_task.id, asked, _accept(approve=True, comment="ok"))
            return _payload(answered)

    payload = asyncio.run(go())
    assert payload["approval"]["action"] == "approved"
    assert payload["approval"]["artifact_version"] == 1
    assert payload["approval"]["artifact_commitment"] == crud.get_drafts(mcp_db, waiting_task.id)[-1].commitment
    assert len(no_platform) == 1


def test_the_question_is_not_asked_twice_within_one_round(connect, waiting_task, no_platform):
    """Round one must not also send a standalone elicitation - one question, one channel."""
    standalone = []

    async def spy(ctx, params):
        standalone.append(params.message)
        return _accept(approve=True)

    async def go():
        async with connect(elicitation_callback=spy) as session:
            asked = await _first_round(session, waiting_task.id)
            assert isinstance(asked, types.InputRequiredResult)
            return standalone

    assert asyncio.run(go()) == []


@pytest.mark.parametrize("action", ["decline", "cancel"])
def test_declining_or_cancelling_records_nothing(action, connect, waiting_task, no_platform, mcp_db):
    async def go():
        async with connect(elicitation_callback=_unused_callback) as session:
            asked = await _first_round(session, waiting_task.id)
            answered = await _answer(session, waiting_task.id, asked, types.ElicitResult(action=action))
            return _payload(answered)

    payload = asyncio.run(go())
    assert payload == {
        "status": "no_decision",
        "elicitation_action": action,
        "task_id": waiting_task.id,
    }
    assert crud.get_approvals(mcp_db, waiting_task.id) == []
    assert no_platform == []


# ------------------------------------------------------------ staleness


def test_a_draft_that_changes_between_rounds_is_asked_again_and_records_nothing(
    connect, waiting_task, no_platform, mcp_db
):
    """The reviewer answered a question about v1; v2 is a different question.

    This is the whole point of binding the elicitation to the shown artifact:
    the answer is not applied to a draft the human never saw. The framework
    compares the rendered question, which is rendered from the pinned draft,
    so the round comes back as a fresh question rather than a decision.
    """

    async def go():
        async with connect(elicitation_callback=_unused_callback) as session:
            asked = await _first_round(session, waiting_task.id)
            crud.add_draft(mcp_db, task_id=waiting_task.id, content="a different draft")
            again = await _answer(session, waiting_task.id, asked, _accept(approve=True))
            return again

    again = asyncio.run(go())
    assert isinstance(again, types.InputRequiredResult), "a changed draft must be re-asked, not decided"
    question = next(iter(again.input_requests.values()))
    assert "a different draft" in question.params.message
    assert crud.get_approvals(mcp_db, waiting_task.id) == []
    assert no_platform == []


def test_a_task_that_leaves_waiting_between_rounds_is_refused(connect, waiting_task, no_platform, mcp_db):
    async def go():
        async with connect(elicitation_callback=_unused_callback) as session:
            asked = await _first_round(session, waiting_task.id)
            waiting_task.status = models.TaskStatusEnum.COMPLETED
            mcp_db.commit()
            answered = await _answer(session, waiting_task.id, asked, _accept(approve=True))
            return answered

    answered = asyncio.run(go())
    # The pinned draft is unchanged, so the answer is accepted and the tool
    # body runs; the status gate is what refuses it.
    payload = _payload(answered)
    assert payload["status"] == "stale_decision"
    assert crud.get_approvals(mcp_db, waiting_task.id) == []
    assert no_platform == []


# -------------------------------------------------------------- retry


def test_replaying_an_answered_round_records_one_decision(connect, waiting_task, no_platform, mcp_db):
    """A retried round is the same decision, not a second one.

    The 2026-07-28 answer round replays the whole `tools/call`, so a client
    that retries after a dropped response runs the tool body again. Once the
    first round has carried the task out of WAITING_APPROVAL the pinning
    resolver refuses before the body runs at all; the answer says so without
    claiming that nothing was ever recorded.
    """

    async def go():
        async with connect(elicitation_callback=_unused_callback) as session:
            asked = await _first_round(session, waiting_task.id)
            first = await _answer(session, waiting_task.id, asked, _accept(approve=True, comment="ok"))
            replay = await _answer(session, waiting_task.id, asked, _accept(approve=True, comment="ok"))
            return _payload(first), _payload(replay)

    first, replay = asyncio.run(go())
    assert first["approval"]["action"] == "approved"
    assert replay["status"] == "stale_decision"
    reason = replay["reason"]
    assert "recorded nothing" in reason, "this call really did record nothing"
    assert "verify_task_ledger" in reason, "and must point at where the standing decision can be read"
    assert "decisions that stand on it" in reason, "without claiming this caller's own answer is one of them"

    assert len(crud.get_approvals(mcp_db, waiting_task.id)) == 1
    assert [d.version for d in crud.get_drafts(mcp_db, waiting_task.id)] == [1]
    assert len(no_platform) == 1, "the graph must not be resumed twice for one decision"


@contextlib.contextmanager
def _lost_before_delivery(monkeypatch):
    """Simulate dying between the ledger commit and the delivery claim.

    That window is the only one recovery may act in: the claim is taken before
    the Platform call, so any entry that was *attempted* carries a stamp
    whether or not the call landed, and an unstamped entry provably never
    reached the graph.
    """
    from app import crud as crud_module

    real = crud_module.claim_delivery
    calls = {"n": 0}

    def _crash_then_claim(db, approval_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("process died before the delivery attempt was claimed")
        return real(db, approval_id)

    monkeypatch.setattr(crud_module, "claim_delivery", _crash_then_claim)
    yield calls
    monkeypatch.setattr(crud_module, "claim_delivery", real)


def test_a_decision_lost_before_delivery_is_recovered_without_a_second_entry(
    connect, waiting_task, mcp_db, no_platform, monkeypatch
):
    """Recorded, never sent, recovered - and the ledger gains nothing.

    Without this the task is parked forever with a decision standing against
    it. With a naive retry instead, the ledger gains a duplicate.
    """
    with _lost_before_delivery(monkeypatch):

        async def go():
            async with connect(elicitation_callback=_unused_callback) as session:
                asked = await _first_round(session, waiting_task.id)
                answer = _accept(approve=True, comment="ok")
                assert (await _answer(session, waiting_task.id, asked, answer)).is_error
                # Nothing was sent, so the entry is unstamped and recoverable.
                assert crud.latest_approval(mcp_db, waiting_task.id).resumed_at is None
                assert no_platform == []
                return _payload(await _answer(session, waiting_task.id, asked, answer))

        repaired = asyncio.run(go())

    assert repaired["replayed"] is True
    assert len(no_platform) == 1, "the recovery is the first and only delivery"
    assert len(crud.get_approvals(mcp_db, waiting_task.id)) == 1
    assert crud.latest_approval(mcp_db, waiting_task.id).resumed_at is not None


def test_recovering_an_edited_decision_re_sends_the_edit(connect, waiting_task, mcp_db, no_platform, monkeypatch):
    """The edit case is the one a re-ask gets wrong.

    An approval carrying an edit commits that edit as v2 before the entry binds
    to it. If recovery were a re-ask, the reviewer would be asked about their
    own text and answering would append a second entry for one decision.
    """
    with _lost_before_delivery(monkeypatch):

        async def go():
            async with connect(elicitation_callback=_unused_callback) as session:
                asked = await _first_round(session, waiting_task.id)
                answer = _accept(approve=True, modified_draft="edited by the reviewer")
                assert (await _answer(session, waiting_task.id, asked, answer)).is_error
                return await _answer(session, waiting_task.id, asked, answer)

        repaired = asyncio.run(go())

    assert not isinstance(repaired, types.InputRequiredResult), "the reviewer must not be re-asked about their own edit"
    payload = _payload(repaired)
    assert payload["replayed"] is True
    assert payload["approval"]["artifact_version"] == 2
    assert no_platform[0][1]["modified_draft"] == "edited by the reviewer"
    assert [d.version for d in crud.get_drafts(mcp_db, waiting_task.id)] == [1, 2]
    assert len(crud.get_approvals(mcp_db, waiting_task.id)) == 1


def test_recovering_a_decision_that_carried_no_edit_sends_no_edit(
    connect, waiting_task, mcp_db, no_platform, monkeypatch
):
    """`edited_artifact`, not the draft's producer, says whether there was an edit.

    A reviewer who authored the latest draft earlier by other means is
    indistinguishable, by producer alone, from one who edited it as part of
    this approval - and guessing wrong re-sends a whole draft to the graph as
    if the human had rewritten it.
    """
    self_actor_id = crud.get_self_actor(mcp_db).id
    crud.add_draft(mcp_db, task_id=waiting_task.id, content="authored earlier", producer_actor_id=self_actor_id)

    with _lost_before_delivery(monkeypatch):

        async def go():
            async with connect(elicitation_callback=_unused_callback) as session:
                asked = await _first_round(session, waiting_task.id)
                answer = _accept(approve=True)  # no modified_draft
                assert (await _answer(session, waiting_task.id, asked, answer)).is_error
                return _payload(await _answer(session, waiting_task.id, asked, answer))

        repaired = asyncio.run(go())

    assert repaired["replayed"] is True
    assert no_platform[0][1]["modified_draft"] is None, "recovery must re-send what the original sent"
    assert crud.latest_approval(mcp_db, waiting_task.id).edited_artifact is False


def test_a_second_recoverer_never_delivers_the_same_decision_again(
    connect, waiting_task, mcp_db, no_platform, monkeypatch
):
    """Two deliveries is the outcome to avoid, not two lost ones.

    `resume_thread` is a plain LangGraph resume: a second delivery answers
    whichever interrupt the graph has reached by then, which can be a question
    no human has seen. The claim is a conditional UPDATE taken before the call,
    so exactly one recoverer proceeds.
    """
    with _lost_before_delivery(monkeypatch):

        async def go():
            async with connect(elicitation_callback=_unused_callback) as session:
                asked = await _first_round(session, waiting_task.id)
                answer = _accept(approve=True)
                assert (await _answer(session, waiting_task.id, asked, answer)).is_error
                approval_id = crud.latest_approval(mcp_db, waiting_task.id).id
                first = _payload(await _answer(session, waiting_task.id, asked, answer))
                second = await mcp_server._resume_recorded_decision(waiting_task.id, approval_id)
                return first, second

        first, second = asyncio.run(go())

    assert first["replayed"] is True and second["replayed"] is True
    assert len(no_platform) == 1, "the second recoverer must not reach Platform"
    assert len(crud.get_approvals(mcp_db, waiting_task.id)) == 1


def test_a_failed_delivery_is_not_retried_on_its_own(waiting_task, mcp_db, monkeypatch):
    """An unconfirmed delivery stays claimed, and says what to do about it.

    A timeout after Platform accepted the resume is indistinguishable from one
    it never received. Re-sending on that ambiguity is the dangerous half, so
    the attempt is spent and the operator is told in terms they can act on.
    """
    attempts = []

    async def _resume(thread_id, payload):
        attempts.append(payload)
        raise TimeoutError("Platform did not answer")

    monkeypatch.setattr(langgraph_client, "resume_thread", _resume)

    async def go():
        with pytest.raises(ToolError) as exc:
            await mcp_server._apply_decision(waiting_task.id, action="approved", comment="ok")
        return str(exc.value)

    message = asyncio.run(go())
    assert "recorded in the ledger" in message
    assert "will not be re-sent on its own" in message
    assert "reject_task" in message, "the operator needs the way through"

    approval = crud.latest_approval(mcp_db, waiting_task.id)
    assert approval.resumed_at is not None, "the one attempt is spent"
    assert len(attempts) == 1
    assert crud.undelivered_approvals(mcp_db, waiting_task.id) == []


def test_a_superseded_undelivered_decision_is_surfaced_not_re_sent(
    connect, waiting_task, mcp_db, no_platform, monkeypatch
):
    """A decision lost before delivery, then overtaken, must not be delivered late.

    Sending it now would answer whichever question the graph reached in the
    meantime - the same harm as a double delivery. It cannot be silent either:
    it is a ledger entry the graph never heard.
    """
    with _lost_before_delivery(monkeypatch):

        async def lost():
            async with connect(elicitation_callback=_unused_callback) as session:
                asked = await _first_round(session, waiting_task.id)
                assert (await _answer(session, waiting_task.id, asked, _accept(approve=False, comment="a"))).is_error

        asyncio.run(lost())

    stranded = crud.latest_approval(mcp_db, waiting_task.id)
    assert stranded.resumed_at is None
    # A different reviewer records and delivers the next decision.
    other = models.Actor(type=models.ActorTypeEnum.HUMAN, name="second reviewer")
    mcp_db.add(other)
    mcp_db.commit()
    crud.record_approval(mcp_db, waiting_task.id, other.id, "rejected", comment="b")
    crud.claim_delivery(mcp_db, crud.latest_approval(mcp_db, waiting_task.id).id)

    async def go():
        async with connect(elicitation_callback=_unused_callback) as session:
            asked = await _first_round(session, waiting_task.id)
            assert isinstance(asked, types.InputRequiredResult), "the reviewer is asked, not handed a late replay"
            # Even a decline is a moment to learn something is stranded.
            return _payload(await _answer(session, waiting_task.id, asked, types.ElicitResult(action="decline")))

    payload = asyncio.run(go())
    assert no_platform == [], "a superseded decision is never delivered late"
    assert payload["status"] == "no_decision"
    assert payload["undelivered"] == [stranded.id], "it has to reach the caller, not just the table"
    assert "not re-sent automatically" in payload["undelivered_note"]
    assert [a.id for a in crud.undelivered_approvals(mcp_db, waiting_task.id)] == [stranded.id]


def test_a_decision_whose_claim_was_taken_elsewhere_is_not_sent_twice(waiting_task, mcp_db, monkeypatch):
    """Even the call that just wrote the entry can lose the claim.

    A retry that arrives while this call sits between its ledger commit and its
    claim will find the unstamped row and take it. Sending anyway would be the
    double delivery the claim exists to stop - and a second delivery answers
    whichever interrupt the graph has reached by then.
    """
    from app import crud as crud_module

    sent = []

    async def _resume(thread_id, payload):
        sent.append(payload)
        return {"values": {"drafts": [DRAFT], "final_output": "shipped"}}

    monkeypatch.setattr(langgraph_client, "resume_thread", _resume)
    # Somebody else got there first.
    monkeypatch.setattr(crud_module, "claim_delivery", lambda db, approval_id: False)

    async def go():
        return await mcp_server._apply_decision(waiting_task.id, action="approved", comment="ok")

    payload = asyncio.run(go())
    assert payload["replayed"] is True
    assert sent == [], "the caller that lost the claim must not reach Platform"
    assert len(crud.get_approvals(mcp_db, waiting_task.id)) == 1


def test_a_replayed_edit_does_not_append_a_second_draft_version(connect, waiting_task, no_platform, mcp_db):
    """An approval carrying an edit is the case a naive retry corrupts worst.

    The edit becomes draft v2 before the approval binds to it, so a naive retry
    would append both a second approval and a third draft version. Neither
    happens, and the graph is resumed once.
    """

    async def go():
        async with connect(elicitation_callback=_unused_callback) as session:
            asked = await _first_round(session, waiting_task.id)
            answer = _accept(approve=True, modified_draft="edited by the reviewer")
            first = await _answer(session, waiting_task.id, asked, answer)
            replay = await _answer(session, waiting_task.id, asked, answer)
            return _payload(first), _payload(replay)

    first, replay = asyncio.run(go())
    assert first["approval"]["artifact_version"] == 2
    assert replay["status"] == "stale_decision"
    assert [d.version for d in crud.get_drafts(mcp_db, waiting_task.id)] == [1, 2]
    assert len(crud.get_approvals(mcp_db, waiting_task.id)) == 1
    assert len(no_platform) == 1


# ---------------------------------------------------- the resumable handle


def test_a_new_connection_resumes_the_handle(connect, waiting_task, no_platform, mcp_db):
    """The point of the 2026 model: the decision outlives the connection it started on."""

    async def go():
        async with connect(elicitation_callback=_unused_callback) as session:
            asked = await _first_round(session, waiting_task.id)
        # First connection is gone; a second one carries the handle back.
        async with connect(elicitation_callback=_unused_callback) as session:
            answered = await _answer(session, waiting_task.id, asked, _accept(approve=True))
            return _payload(answered)

    payload = asyncio.run(go())
    assert payload["approval"]["action"] == "approved"
    assert payload["approval"]["artifact_version"] == 1


def test_an_expired_handle_is_refused_and_the_question_is_asked_again(waiting_task, no_platform, mcp_db, monkeypatch):
    """A half-finished approval is not a standing offer.

    The expiry is stamped into the envelope when it is sealed, so the TTL is
    shortened before the question is asked rather than after.
    """
    boundary = _boundary()
    monkeypatch.setattr(boundary._security, "ttl", 0.05)

    async def go():
        import anyio

        async with _stdio(elicitation_callback=_unused_callback) as session:
            asked = await _first_round(session, waiting_task.id)
            await anyio.sleep(0.1)
            with pytest.raises(MCPError) as exc:
                await _answer(session, waiting_task.id, asked, _accept(approve=True))
            assert exc.value.error.data == {"reason": "invalid_request_state"}
            # The client's recovery is to start the review over, which works.
            fresh = await _first_round(session, waiting_task.id)
            assert isinstance(fresh, types.InputRequiredResult)

    asyncio.run(go())
    assert crud.get_approvals(mcp_db, waiting_task.id) == []


def test_a_handle_cannot_be_pointed_at_another_task(waiting_task, no_platform, mcp_db, self_actor):
    """The handle is bound to the call it was minted for, arguments included."""
    other = crud.create_task(mcp_db, thread_id="thread-other", title="Other")
    other.status = models.TaskStatusEnum.WAITING_APPROVAL
    mcp_db.commit()
    crud.add_draft(mcp_db, task_id=other.id, content="somebody else's draft")

    async def go():
        async with _stdio(elicitation_callback=_unused_callback) as session:
            asked = await _first_round(session, waiting_task.id)
            with pytest.raises(MCPError) as exc:
                await _answer(session, other.id, asked, _accept(approve=True))
            assert exc.value.error.data == {"reason": "invalid_request_state"}

    asyncio.run(go())
    assert crud.get_approvals(mcp_db, other.id) == []
    assert crud.get_approvals(mcp_db, waiting_task.id) == []


def test_a_tampered_handle_is_refused(waiting_task, no_platform, mcp_db):
    async def go():
        async with _stdio(elicitation_callback=_unused_callback) as session:
            asked = await _first_round(session, waiting_task.id)
            forged = asked.request_state[:-4] + ("AAAA" if not asked.request_state.endswith("AAAA") else "BBBB")
            with pytest.raises(MCPError) as exc:
                await _answer(session, waiting_task.id, asked, _accept(approve=True), request_state=forged)
            assert exc.value.error.data == {"reason": "invalid_request_state"}

    asyncio.run(go())
    assert crud.get_approvals(mcp_db, waiting_task.id) == []


def test_the_request_state_policy_follows_the_environment(monkeypatch):
    monkeypatch.delenv(mcp_server.REQUEST_STATE_KEY_ENV, raising=False)
    ephemeral = mcp_server._request_state_security()
    assert ephemeral.ttl == mcp_server.REQUEST_STATE_TTL_SECONDS

    monkeypatch.setenv(mcp_server.REQUEST_STATE_KEY_ENV, "0" * 64)
    shared = mcp_server._request_state_security()
    assert shared.ttl == mcp_server.REQUEST_STATE_TTL_SECONDS
    # Two policies built from the same configured key seal interchangeably;
    # two ephemeral ones do not, which is what makes the env var meaningful.
    again = mcp_server._request_state_security()
    assert again.codec.unseal(shared.codec.seal(b"payload")) == b"payload"

    # A key too weak to protect a resumable decision is refused by name, at
    # startup, rather than silently downgrading what the handle is worth.
    monkeypatch.setenv(mcp_server.REQUEST_STATE_KEY_ENV, "short")
    with pytest.raises(ValueError, match=mcp_server.REQUEST_STATE_KEY_ENV):
        mcp_server._request_state_security()


# -------------------------------------------------- the older interaction model


def test_the_legacy_revision_holds_the_question_open_on_the_connection(connect, waiting_task, no_platform, mcp_db):
    """One tool call, answered mid-flight - and the same ledger entry as the new model."""
    asked_messages = []

    async def answer_inline(ctx, params):
        asked_messages.append(params.message)
        return _accept(approve=True, comment="ok")

    async def go():
        async with connect(elicitation_callback=answer_inline, modern=False) as session:
            assert not compat.uses_input_required(session.protocol_version)
            result = await session.call_tool("review_pending_task", {"task_id": waiting_task.id})
            return _payload(result)

    payload = asyncio.run(go())
    assert len(asked_messages) == 1
    assert DRAFT in asked_messages[0]
    assert payload["approval"]["action"] == "approved"
    assert payload["approval"]["artifact_version"] == 1
    assert len(crud.get_approvals(mcp_db, waiting_task.id)) == 1


def test_the_legacy_revision_still_binds_to_the_draft_it_showed(connect, waiting_task, no_platform, mcp_db):
    """Swapping the draft while the human reads it must not move the decision onto v2."""

    async def swap_then_answer(ctx, params):
        crud.add_draft(mcp_db, task_id=waiting_task.id, content="swapped in behind the reviewer")
        return _accept(approve=True)

    async def go():
        async with connect(elicitation_callback=swap_then_answer, modern=False) as session:
            result = await session.call_tool("review_pending_task", {"task_id": waiting_task.id})
            return _payload(result)

    payload = asyncio.run(go())
    assert payload["status"] == "stale_decision"
    assert crud.get_approvals(mcp_db, waiting_task.id) == []
    assert no_platform == []


# --------------------------------------------------------------- fallback


def test_a_client_without_elicitation_is_sent_to_the_direct_tools(connect, waiting_task, no_platform, mcp_db):
    """No capability, no question: an answerable instruction instead of a protocol error."""

    async def go():
        async with connect() as session:  # no elicitation_callback -> no capability
            result = await session.call_tool(
                "review_pending_task", {"task_id": waiting_task.id}, allow_input_required=True
            )
            return result

    result = asyncio.run(go())
    assert not isinstance(result, types.InputRequiredResult), "nothing should be asked of a client that cannot answer"
    payload = _payload(result)
    assert payload["status"] == "elicitation_unsupported"
    assert payload["fallback_tools"] == list(compat.FALLBACK_TOOLS)
    assert payload["artifact_version"] == 1
    assert crud.get_approvals(mcp_db, waiting_task.id) == []


def test_the_fallback_tools_reproduce_the_same_bound_decision(connect, waiting_task, no_platform, mcp_db):
    """What the fallback offers has to be worth taking: the same binding, no interaction."""

    async def go():
        async with connect() as session:
            refused = _payload(
                await session.call_tool("review_pending_task", {"task_id": waiting_task.id}, allow_input_required=True)
            )
            approved = await session.call_tool(
                "approve_task",
                {
                    "task_id": waiting_task.id,
                    "artifact_version": refused["artifact_version"],
                    "expected_commitment": refused["expected_commitment"],
                },
            )
            return _payload(approved)

    payload = asyncio.run(go())
    assert payload["approval"]["action"] == "approved"
    assert payload["approval"]["artifact_version"] == 1


def test_a_task_with_no_draft_is_answered_without_asking_anything(connect, mcp_db, self_actor, no_platform):
    task = crud.create_task(mcp_db, thread_id="thread-empty", title="Nothing to show")
    task.status = models.TaskStatusEnum.WAITING_APPROVAL
    mcp_db.commit()
    asked = []

    async def spy(ctx, params):
        asked.append(params.message)
        return _accept(approve=True)

    async def go():
        async with connect(elicitation_callback=spy) as session:
            return await session.call_tool("review_pending_task", {"task_id": task.id}, allow_input_required=True)

    result = asyncio.run(go())
    assert not isinstance(result, types.InputRequiredResult)
    assert _payload(result)["status"] == "no_artifact"
    assert asked == []


def test_a_delivered_decision_that_parks_again_is_asked_afresh(connect, waiting_task, mcp_db, monkeypatch):
    """A graph that interrupts again on the same draft is a new question, not a lost resume.

    This is the state the old status-only discriminator could not read: the
    task is WAITING_APPROVAL, the newest entry is this reviewer's, and it binds
    the latest draft - identical to a resume that never arrived. Replaying the
    old rejection there would take the reviewer's new question away from them.
    `resumed_at` is what tells the two apart.
    """

    async def _resume(thread_id, payload):
        # Delivered, and the graph parks again without producing a new draft.
        return {"next": ["human_approval"], "values": {"drafts": [DRAFT]}}

    monkeypatch.setattr(langgraph_client, "resume_thread", _resume)
    asked = []

    async def spy(ctx, params):
        asked.append(params.message)
        return types.ElicitResult(action="decline")

    async def go():
        async with connect(elicitation_callback=spy) as session:
            first = await _first_round(session, waiting_task.id)
            await _answer(session, waiting_task.id, first, _accept(approve=False, comment="revise"))
            # A fresh review, not a retry: no request_state, no answers.
            return await _first_round(session, waiting_task.id)

    again = asyncio.run(go())
    assert isinstance(again, types.InputRequiredResult), "the reviewer must be asked, not handed a replay"
    assert DRAFT in next(iter(again.input_requests.values())).params.message
    assert len(crud.get_approvals(mcp_db, waiting_task.id)) == 1
    assert crud.latest_approval(mcp_db, waiting_task.id).resumed_at is not None


def test_a_duplicate_key_never_drives_the_graph_twice(waiting_task, mcp_db, monkeypatch):
    """A second round carrying one decision's key records nothing and resumes nothing.

    Sequential, like the rest of the ledger's concurrency coverage: SQLite has
    no row locks, so what is observable here is the contract - the second
    caller is refused by `record_approval` rather than handed the row, and a
    caller that cannot tell "I wrote this" from "somebody already had" is
    exactly the one that drives the graph twice. The lock itself is pinned in
    test_ledger_concurrency.py.
    """
    resumes = []

    async def _resume(thread_id, payload):
        resumes.append(payload)
        return {"next": ["human_approval"], "values": {"drafts": [DRAFT]}}

    monkeypatch.setattr(langgraph_client, "resume_thread", _resume)

    async def go():
        first = await mcp_server._apply_decision(
            waiting_task.id, action="rejected", comment="no", decision_key="one round"
        )
        second = await mcp_server._apply_decision(
            waiting_task.id, action="rejected", comment="no", decision_key="one round"
        )
        return first, second

    first, second = asyncio.run(go())
    assert "replayed" not in first
    assert second["replayed"] is True
    assert second["approval"]["id"] == first["approval"]["id"]
    assert len(resumes) == 1, "the loser of the race must not resume the graph"
    assert len(crud.get_approvals(mcp_db, waiting_task.id)) == 1


def test_a_repeated_identical_verdict_is_refused_with_a_way_through(connect, waiting_task, mcp_db, monkeypatch):
    """The one case the key cannot decide, resolved toward the ledger.

    A rejection is delivered, the graph parks again on the same draft, and the
    reviewer rejects the new question in the same words. That is two decisions,
    and it is byte-identical to a replay of the first - `requestState` does not
    separate them either, because the SDK hands the handler the unsealed state,
    which is a pure function of the answers and the rendered question.

    Refusing is the safe half of an undecidable choice (a silently duplicated
    entry in an approval ledger is worse than a refused write), so what is
    pinned here is that the refusal is visible and names the way through.
    """

    async def _resume(thread_id, payload):
        return {"next": ["human_approval"], "values": {"drafts": [DRAFT]}}

    monkeypatch.setattr(langgraph_client, "resume_thread", _resume)

    async def go():
        async with connect(elicitation_callback=_unused_callback) as session:
            answers = []
            for _ in range(2):
                asked = await _first_round(session, waiting_task.id)
                assert isinstance(asked, types.InputRequiredResult), "each parked question is asked"
                answers.append(_payload(await _answer(session, waiting_task.id, asked, _accept(approve=False))))
            return answers

    first, second = asyncio.run(go())
    assert "replayed" not in first
    assert second["replayed"] is True
    assert second["approval"]["id"] == first["approval"]["id"]
    note = second.get("note", "")
    assert "repeated question" in note and "reject_task" in note, "the refusal must name the way through"

    # The ledger holds one entry, and the escape hatch really does record the second.
    assert len(crud.get_approvals(mcp_db, waiting_task.id)) == 1

    async def escape():
        async with connect() as session:
            return _payload(await session.call_tool("reject_task", {"task_id": waiting_task.id}))

    asyncio.run(escape())
    approvals = crud.get_approvals(mcp_db, waiting_task.id)
    assert [a.action for a in approvals] == ["rejected", "rejected"]
    assert approvals[1].prev_hash == approvals[0].entry_hash
    assert crud.verify_approval_chain(mcp_db, waiting_task.id)["valid"] is True


# ------------------------------------------------- authorization and the handle


def test_a_caller_who_may_not_decide_is_never_shown_the_draft(connect, waiting_task, mcp_db, monkeypatch):
    """The refusal happens before the draft is rendered, not after the answer.

    Being told "you may not approve this" once the content has already been
    displayed as a question is not a refusal - the disclosure already
    happened, on whichever interaction model was in use.
    """
    from app import authz

    refused = []

    def _refuse(db, task_id):
        refused.append(task_id)
        raise authz.AuthzError("this credential may not decide on that task")

    monkeypatch.setattr(authz, "check_may_approve", _refuse)
    shown = []

    async def spy(ctx, params):
        shown.append(params.message)
        return _accept(approve=True)

    async def go():
        async with connect(elicitation_callback=spy) as session:
            return await session.call_tool(
                "review_pending_task", {"task_id": waiting_task.id}, allow_input_required=True
            )

    result = asyncio.run(go())
    assert result.is_error
    assert not isinstance(result, types.InputRequiredResult), "nothing may be asked of a caller who cannot decide"
    assert shown == [], "the draft must not reach the wire"
    assert DRAFT not in "".join(c.text for c in result.content)
    assert refused == [waiting_task.id]
    assert crud.get_approvals(mcp_db, waiting_task.id) == []


def test_the_handle_is_bound_to_the_caller_that_started_the_round():
    """A handle minted for one credential must not be usable by another.

    The SDK's default binding reads its own OAuth context, which this server
    never populates, so without an explicit `bind_principal` every handle would
    be transferable between authenticated callers.
    """

    class _Ctx:
        def __init__(self, header):
            self.request = None if header is None else _Req(header)

    class _Req:
        def __init__(self, header):
            self.headers = {"authorization": header}

    security = mcp_server._request_state_security()
    assert security.bind_principal is mcp_server._request_state_principal

    a = mcp_server._request_state_principal(_Ctx("Bearer aaa"))
    b = mcp_server._request_state_principal(_Ctx("Bearer bbb"))
    assert a is not None and b is not None and a != b
    assert mcp_server._request_state_principal(_Ctx("Bearer aaa")) == a, "stable across the rounds of one call"
    assert "aaa" not in a, "the binding value must not carry the credential"
    # stdio, and an HTTP call presenting nothing, are the loopback assumption.
    assert mcp_server._request_state_principal(_Ctx(None)) is None
    assert mcp_server._request_state_principal(_Ctx("")) is None


# ------------------------------------------------------------------ helpers


async def _unused_callback(ctx, params):  # pragma: no cover - a guard, not a path
    """Declares the elicitation capability without ever being the answer channel.

    On 2026-07-28 the answer arrives as `input_responses`; if this ever runs,
    the server fell back to the standalone request on a revision that should
    not use it.
    """
    raise AssertionError(f"standalone elicitation on a modern connection: {params.message!r}")


def test_delivery_is_recorded_even_if_the_projection_fails(waiting_task, mcp_db, monkeypatch):
    """The stamp is committed on its own, before the projection.

    If it rode the projection's commit, a failure there would leave the
    decision looking undelivered - and the retry would deliver it a second
    time, which is the double-apply the column exists to prevent.
    """

    async def _resume(thread_id, payload):
        return {"values": {"drafts": [DRAFT], "final_output": "shipped"}}

    monkeypatch.setattr(langgraph_client, "resume_thread", _resume)

    def _explode(db, task, result):
        raise RuntimeError("projection failed")

    monkeypatch.setattr(mcp_server, "_sync_state", _explode)

    async def go():
        with pytest.raises(RuntimeError):
            await mcp_server._apply_decision(waiting_task.id, action="approved", comment="ok")

    asyncio.run(go())
    approval = crud.latest_approval(mcp_db, waiting_task.id)
    assert approval is not None
    assert approval.resumed_at is not None, "the graph got the decision; a retry must not re-deliver it"
