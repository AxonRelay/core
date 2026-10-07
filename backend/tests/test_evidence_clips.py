"""Evidence Clips, shared half (ADR-014): the ledger holds digests and provenance, never text."""

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import Text, inspect

from app import evidence, ledger, models
from app.mcp import server

EXCERPT = "The quick brown fox jumps over the lazy dog."
DIGEST = ledger.compute_artifact_commitment(EXCERPT)
URL = "https://example.com/articles/foxes"
#: A string that must never reach the database, a response or a log.
CANARY = "canary-excerpt-7f3a9c"


@pytest.fixture
def task(db, self_actor):
    row = models.Task(thread_id="t-1", title="Research", status=models.TaskStatusEnum.DRAFT)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def other_task(db, self_actor):
    row = models.Task(thread_id="t-2", title="Other", status=models.TaskStatusEnum.DRAFT)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _body(**overrides):
    return {"source_url": URL, "source_type": "public", "content_sha256": DIGEST, **overrides}


def _call_tool(name: str, arguments: dict):
    tool = server.mcp._tool_manager.get_tool(name)
    return asyncio.run(tool.run(arguments, context=None))


def _everything_stored(db) -> str:
    rows = []
    for table in ("evidence_clips", "evidence_feedback"):
        rows.extend(str(tuple(r)) for r in db.execute(models.Base.metadata.tables[table].select()))
    return "\n".join(rows)


# ------------------------------------------------------------------ the schema


def test_the_clip_tables_have_no_place_for_text():
    """The guarantee is structural: no column a quote, title or annotation could go in."""
    clip_columns = {c.name for c in models.EvidenceClip.__table__.columns}
    assert clip_columns == {
        "id",
        "task_id",
        "captured_by_actor_id",
        "source_url",
        "source_type",
        "content_sha256",
        "content_algorithm",
        "captured_at",
    }
    feedback_columns = {c.name for c in models.EvidenceFeedback.__table__.columns}
    assert feedback_columns == {"id", "evidence_clip_id", "actor_id", "verdict", "created_at"}
    for table in (models.EvidenceClip.__table__, models.EvidenceFeedback.__table__):
        assert not [c.name for c in table.columns if isinstance(c.type, Text)]


def test_the_clip_dict_is_exactly_the_shared_record(db, task, self_actor):
    clip, _ = evidence.create_clip(
        db,
        task_id=task.id,
        captured_by_actor_id=self_actor.id,
        source_url=URL,
        source_type="public",
        content_sha256=DIGEST,
    )
    assert set(evidence.clips_to_dicts(db, [clip])[0]) == {
        "id",
        "evidence_ref",
        "task_id",
        "captured_by_actor_id",
        "source_url",
        "source_type",
        "content_sha256",
        "content_algorithm",
        "captured_at",
        "feedback",
    }


# ------------------------------------------------------------------ REST capture


def test_capture_records_the_commitment_and_the_caller(client, db, task, self_actor):
    response = client.post(f"/tasks/{task.id}/evidence-clips", json=_body())

    assert response.status_code == 201
    body = response.json()
    assert body["evidence_ref"] == f"E-{body['id']}"
    assert body["content_sha256"] == DIGEST
    assert body["content_algorithm"] == ledger.COMMITMENT_ALGORITHM
    assert body["captured_by_actor_id"] == self_actor.id
    assert body["feedback"] == {"relevant": 0, "irrelevant": 0, "misleading": 0}


def test_recapture_is_idempotent(client, db, task):
    first = client.post(f"/tasks/{task.id}/evidence-clips", json=_body())
    again = client.post(f"/tasks/{task.id}/evidence-clips", json=_body())

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"]
    assert db.query(models.EvidenceClip).count() == 1


def test_the_same_capture_under_another_label_is_a_conflict(client, db, task):
    client.post(f"/tasks/{task.id}/evidence-clips", json=_body())
    response = client.post(f"/tasks/{task.id}/evidence-clips", json=_body(source_type="personal"))

    assert response.status_code == 409
    assert db.query(models.EvidenceClip).one().source_type == models.EvidenceSourceTypeEnum.PUBLIC


def test_the_same_digest_on_another_task_is_another_clip(client, db, task, other_task):
    a = client.post(f"/tasks/{task.id}/evidence-clips", json=_body()).json()
    b = client.post(f"/tasks/{other_task.id}/evidence-clips", json=_body()).json()
    assert a["id"] != b["id"]


@pytest.mark.parametrize("field", ["quote", "content", "title", "source_title", "annotations"])
def test_text_fields_are_refused_not_dropped(client, db, task, field, caplog):
    """A client that still sends the excerpt must learn that it stays local."""
    response = client.post(f"/tasks/{task.id}/evidence-clips", json=_body(**{field: CANARY}))

    assert response.status_code == 422
    assert CANARY not in response.text
    assert CANARY not in caplog.text
    assert db.query(models.EvidenceClip).count() == 0


@pytest.mark.parametrize(
    "url",
    [
        # The #53 review finding: presigned credentials ride in the query.
        f"https://bucket.s3.amazonaws.com/doc.pdf?X-Amz-Signature={CANARY}&X-Amz-Credential=AKIA",
        f"https://acct.blob.core.windows.net/c/doc?sig={CANARY}",
        f"https://example.com/page#{CANARY}",
        f"https://user:{CANARY}@example.com/page",
        f"ftp://example.com/{CANARY}",
        f"javascript:{CANARY}",
    ],
)
def test_an_unsafe_source_url_is_refused_without_echo(client, db, task, url):
    response = client.post(f"/tasks/{task.id}/evidence-clips", json=_body(source_url=url))

    assert response.status_code == 400
    assert CANARY not in response.text
    assert CANARY not in _everything_stored(db)


@pytest.mark.parametrize("digest", ["0" * 63, "G" * 64, DIGEST.upper()])
def test_a_malformed_digest_is_refused(client, task, digest):
    assert client.post(f"/tasks/{task.id}/evidence-clips", json=_body(content_sha256=digest)).status_code == 422


def test_an_unknown_algorithm_is_refused(client, task):
    response = client.post(f"/tasks/{task.id}/evidence-clips", json=_body(content_algorithm="md5"))
    assert response.status_code == 400


def test_capture_on_a_missing_task_is_404(client, self_actor):
    assert client.post("/tasks/999/evidence-clips", json=_body()).status_code == 404


def test_list_is_newest_first_and_task_scoped(client, task, other_task):
    first = client.post(f"/tasks/{task.id}/evidence-clips", json=_body()).json()
    second = client.post(
        f"/tasks/{task.id}/evidence-clips", json=_body(content_sha256=ledger.compute_artifact_commitment("x"))
    ).json()
    client.post(f"/tasks/{other_task.id}/evidence-clips", json=_body())

    listed = client.get(f"/tasks/{task.id}/evidence-clips").json()
    assert [c["id"] for c in listed] == [second["id"], first["id"]]
    assert client.get("/tasks/999/evidence-clips").status_code == 404


# ------------------------------------------------------------------ feedback


def test_feedback_appends_and_is_counted(client, task, self_actor):
    clip = client.post(f"/tasks/{task.id}/evidence-clips", json=_body()).json()

    for verdict in ("relevant", "relevant", "misleading"):
        response = client.post(f"/evidence-clips/{clip['id']}/feedback", json={"verdict": verdict})
        assert response.status_code == 201
        assert response.json()["actor_id"] == self_actor.id

    listed = client.get(f"/tasks/{task.id}/evidence-clips").json()
    assert listed[0]["feedback"] == {"relevant": 2, "irrelevant": 0, "misleading": 1}


def test_feedback_takes_a_verdict_and_nothing_else(client, db, task):
    clip = client.post(f"/tasks/{task.id}/evidence-clips", json=_body()).json()

    assert client.post(f"/evidence-clips/{clip['id']}/feedback", json={"verdict": "great"}).status_code == 422
    response = client.post(f"/evidence-clips/{clip['id']}/feedback", json={"verdict": "relevant", "comment": CANARY})
    assert response.status_code == 422
    assert client.post("/evidence-clips/999/feedback", json={"verdict": "relevant"}).status_code == 404
    assert db.query(models.EvidenceFeedback).count() == 0


# ------------------------------------------------------------------ citations


def test_draft_references_resolve_only_to_this_task(db, task, other_task):
    mine, _ = evidence.create_clip(
        db, task_id=task.id, captured_by_actor_id=None, source_url=URL, source_type="public", content_sha256=DIGEST
    )
    theirs, _ = evidence.create_clip(
        db,
        task_id=other_task.id,
        captured_by_actor_id=None,
        source_url=URL,
        source_type="public",
        content_sha256=DIGEST,
    )
    draft = f"Foxes jump [{mine.evidence_ref}], see also [{theirs.evidence_ref}] and [E-999] and [{mine.evidence_ref}]."

    result = evidence.validate_draft_references(db, task.id, draft)

    assert result["referenced"] == [mine.evidence_ref, theirs.evidence_ref, "E-999", mine.evidence_ref]
    assert result["valid"] == [mine.evidence_ref, mine.evidence_ref]
    assert result["missing"] == [theirs.evidence_ref, "E-999"]
    assert result["manifest"] == [{"evidence_ref": mine.evidence_ref, "content_sha256": DIGEST, "source_url": URL}]


def test_a_draft_without_citations_is_valid_and_empty(db, task):
    assert evidence.validate_draft_references(db, task.id, "no refs") == {
        "referenced": [],
        "valid": [],
        "missing": [],
        "manifest": [],
    }


# ------------------------------------------------------------------ retention and deletion


def test_clips_go_with_their_task(db, task):
    evidence.create_clip(
        db, task_id=task.id, captured_by_actor_id=None, source_url=URL, source_type="public", content_sha256=DIGEST
    )
    db.delete(task)
    db.commit()
    assert db.query(models.EvidenceClip).count() == 0


def test_the_clip_tables_exist(db):
    assert {"evidence_clips", "evidence_feedback"} <= set(inspect(db.get_bind()).get_table_names())


# ------------------------------------------------------------------ MCP


def test_mcp_capture_list_and_evaluate(mcp_db, task, self_actor):
    created = _call_tool(
        "capture_evidence_clip",
        {"task_id": task.id, "source_url": URL, "source_type": "personal", "content_sha256": DIGEST},
    )
    assert created["created"] is True
    again = _call_tool(
        "capture_evidence_clip",
        {"task_id": task.id, "source_url": URL, "source_type": "personal", "content_sha256": DIGEST},
    )
    assert again["created"] is False and again["id"] == created["id"]

    event = _call_tool("evaluate_evidence_clip", {"clip_id": created["id"], "verdict": "irrelevant"})
    assert event["verdict"] == "irrelevant"

    listed = _call_tool("list_evidence_clips", {"task_id": task.id})
    assert [c["id"] for c in listed] == [created["id"]]
    assert listed[0]["feedback"]["irrelevant"] == 1


def test_mcp_refusals_do_not_echo(mcp_db, task):
    with pytest.raises(ToolError) as exc:
        _call_tool(
            "capture_evidence_clip",
            {
                "task_id": task.id,
                "source_url": f"https://example.com/?token={CANARY}",
                "source_type": "public",
                "content_sha256": DIGEST,
            },
        )
    assert CANARY not in str(exc.value)
    with pytest.raises(ToolError):
        _call_tool("evaluate_evidence_clip", {"clip_id": 999, "verdict": "relevant"})


def test_mcp_validate_references(mcp_db, task):
    clip = _call_tool(
        "capture_evidence_clip",
        {"task_id": task.id, "source_url": URL, "source_type": "public", "content_sha256": DIGEST},
    )
    result = _call_tool("validate_evidence_references", {"task_id": task.id, "draft": f"[{clip['evidence_ref']}]"})
    assert result["valid"] == [clip["evidence_ref"]]


def test_a_task_holds_a_bounded_number_of_clips(db, task, monkeypatch):
    """Round-5 finding: the Context Pack reads the whole manifest, so the manifest is bounded at capture."""
    monkeypatch.setattr(evidence, "MAX_CLIPS_PER_TASK", 3)
    for i in range(3):
        evidence.create_clip(
            db,
            task_id=task.id,
            captured_by_actor_id=None,
            source_url=f"{URL}/{i}",
            source_type="public",
            content_sha256=DIGEST,
        )
    with pytest.raises(evidence.EvidenceError):
        evidence.create_clip(
            db,
            task_id=task.id,
            captured_by_actor_id=None,
            source_url=f"{URL}/9",
            source_type="public",
            content_sha256=DIGEST,
        )
    # Re-sending an existing capture is still idempotent at the cap.
    _, created = evidence.create_clip(
        db,
        task_id=task.id,
        captured_by_actor_id=None,
        source_url=f"{URL}/0",
        source_type="public",
        content_sha256=DIGEST,
    )
    assert created is False


def test_an_enormous_citation_number_is_missing_not_an_error(db, task):
    """Round-5 finding: int() on an unbounded digit run raised before any lookup."""
    draft = "[E-" + "9" * 5000 + "] and [E-123456789012345678]"
    result = evidence.validate_draft_references(db, task.id, draft)
    assert result["missing"] == ["E-123456789012345678"]
    assert result["valid"] == []


def test_feedback_counts_come_from_one_aggregate_query(db, task):
    """Round-6 finding: counting loaded every feedback row of every clip."""
    from sqlalchemy import event

    clips = [
        evidence.create_clip(
            db,
            task_id=task.id,
            captured_by_actor_id=None,
            source_url=f"{URL}/{i}",
            source_type="public",
            content_sha256=DIGEST,
        )[0]
        for i in range(5)
    ]
    for clip in clips:
        for verdict in ("relevant", "misleading", "relevant"):
            evidence.record_feedback(db, clip.id, None, verdict)
    db.expire_all()

    statements = []
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(db.get_bind(), "before_cursor_execute", listener)
    try:
        rows = evidence.manifest(db, task.id)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", listener)

    assert all(r["feedback"] == {"relevant": 2, "irrelevant": 0, "misleading": 1} for r in rows)
    assert len([s for s in statements if "evidence_feedback" in s]) == 1
