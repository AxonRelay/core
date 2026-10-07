"""Evidence Clips, local half (ADR-014): the text store and the Context Pack."""

import asyncio
import json
import stat

import pytest

from app import evidence, evidence_local, ledger, models
from app.mcp import server

URL = "https://example.com/articles/foxes"


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "evidence"
    monkeypatch.setenv(evidence_local.STORE_ENV, str(path))
    return path


def _entry(clip_id: int, content: str, captured_at: str = "2026-10-07T00:00:00") -> dict:
    return {
        "id": clip_id,
        "evidence_ref": f"E-{clip_id}",
        "source_url": URL,
        "content_sha256": ledger.compute_artifact_commitment(content),
        "captured_at": captured_at,
    }


def _size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


# ------------------------------------------------------------------ the store


def test_put_returns_the_ledger_commitment_and_get_round_trips(store):
    digest = evidence_local.put("An excerpt.", title="T", url=URL + "?q=1#p", annotations={"summary": "s"})

    assert digest == ledger.compute_artifact_commitment("An excerpt.")
    record = evidence_local.get(digest)
    assert record["content"] == "An excerpt."
    # The full URL stays local.
    assert record["captures"] == [{"title": "T", "url": URL + "?q=1#p", "annotations": {"summary": "s"}}]


def test_the_same_words_from_two_pages_keep_both_captures(store):
    """Round-1 finding: a second source must not overwrite the first page's metadata."""
    other = "https://other.example.org/copy"
    digest = evidence_local.put("Boilerplate.", title="First", url=URL, annotations={"summary": "first"})
    evidence_local.put("Boilerplate.", title="Second", url=other, annotations={"summary": "second"})
    evidence_local.put("Boilerplate.", title="First, renamed", url=URL, annotations={"summary": "first"})

    titles = {c["url"]: c["title"] for c in evidence_local.get(digest)["captures"]}
    assert titles == {URL: "First, renamed", other: "Second"}

    pack = evidence_local.build_context_pack(
        1, [_entry(2, "Boilerplate.") | {"source_url": other}, _entry(1, "Boilerplate.")]
    )
    assert [(i["evidence_ref"], i["source_title"]) for i in pack["items"]] == [
        ("E-2", "Second"),
        ("E-1", "First, renamed"),
    ]


def test_a_capture_from_another_source_is_not_shown_under_this_clip(store):
    evidence_local.put(
        "Shared words.", title="Elsewhere", url="https://elsewhere.example/", annotations={"summary": "x"}
    )
    item = evidence_local.build_context_pack(1, [_entry(1, "Shared words.")])["items"][0]
    assert item["source_title"] == "" and item["annotations"] is None


def test_the_ledger_form_of_a_url_matches_its_capture(store):
    evidence_local.put("Text.", title="T", url="https://User:pw" + "@" + "Example.COM/articles/foxes?utm=1#frag")
    item = evidence_local.build_context_pack(
        1, [_entry(1, "Text.") | {"source_url": "https://example.com/articles/foxes"}]
    )["items"][0]
    assert item["source_title"] == "T"


def test_records_are_private_to_the_owner(store):
    digest = evidence_local.put("private")
    mode = (store / f"{digest}.json").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600


def test_a_missing_digest_is_none_and_a_malformed_one_is_never_a_path(store):
    assert evidence_local.get("0" * 64) is None
    assert evidence_local.get("../../etc/passwd") is None


def test_a_tampered_record_is_refused(store):
    digest = evidence_local.put("original words")
    path = store / f"{digest}.json"
    record = json.loads(path.read_text())
    record["content"] = "different words"
    path.write_text(json.dumps(record))

    with pytest.raises(evidence_local.LocalEvidenceError) as exc:
        evidence_local.get(digest)
    assert "different words" not in str(exc.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": ""},
        {"content": "   "},
        {"content": "x" * (evidence_local.MAX_CONTENT_CHARS + 1)},
        {"content": "ok", "annotations": {"verified": True}},
        {"content": "ok", "annotations": {"tags": "not-a-list"}},
        {"content": "ok", "title": "t" * (evidence_local.MAX_TITLE_CHARS + 1)},
    ],
)
def test_put_refuses_bad_records(store, kwargs):
    content = kwargs.pop("content")
    with pytest.raises(evidence_local.LocalEvidenceError):
        evidence_local.put(content, **kwargs)


def test_import_checks_the_stated_digest(store, tmp_path):
    good = {"content": "one", "title": "T", "url": URL, "content_sha256": ledger.compute_artifact_commitment("one")}
    export = tmp_path / "clips.jsonl"
    export.write_text(json.dumps(good) + "\n\n" + json.dumps({"content": "two"}) + "\n")
    assert evidence_local.import_jsonl(export) == [
        ledger.compute_artifact_commitment("one"),
        ledger.compute_artifact_commitment("two"),
    ]

    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"content": "three", "content_sha256": "0" * 64}) + "\n")
    with pytest.raises(evidence_local.LocalEvidenceError):
        evidence_local.import_jsonl(bad)
    assert evidence_local.get(ledger.compute_artifact_commitment("three")) is None


def test_a_bad_import_line_leaves_the_store_untouched(store, tmp_path):
    """Round-1 finding: a wrong stated digest used to overwrite, then delete, a valid record."""
    digest = evidence_local.put("kept", title="Original", url=URL)
    before = (store / f"{digest}.json").read_text()
    export = tmp_path / "mixed.jsonl"
    export.write_text(
        json.dumps({"content": "new and fine"})
        + "\n"
        + json.dumps({"content": "kept", "title": "Overwrite", "content_sha256": "0" * 64})
        + "\n"
    )

    with pytest.raises(evidence_local.LocalEvidenceError):
        evidence_local.import_jsonl(export)

    assert (store / f"{digest}.json").read_text() == before
    assert evidence_local.get(ledger.compute_artifact_commitment("new and fine")) is None


def test_cli_put_prints_the_digest(store, capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("  from stdin \n"))
    assert evidence_local.main(["put", "--title", "T"]) == 0
    assert capsys.readouterr().out.strip() == ledger.compute_artifact_commitment("from stdin")


# ------------------------------------------------------------------ the Context Pack


def test_without_a_query_the_pack_is_the_manifest_order(store):
    for text in ("newest", "older"):
        evidence_local.put(text)
    pack = evidence_local.build_context_pack(1, [_entry(2, "newest"), _entry(1, "older")])

    assert [item["evidence_ref"] for item in pack["items"]] == ["E-2", "E-1"]
    assert pack["items"][0]["content"] == "newest"
    assert pack["untrusted_content"] is True
    assert pack["unresolved"] == []


def test_a_query_ranks_exact_matches_first_and_drops_weak_ones(store):
    texts = {
        1: "Brown foxes are quick and clever.",
        2: "The quick brown fox jumps over the lazy dog.",
        3: "Completely unrelated sentence about tea.",
    }
    for text in texts.values():
        evidence_local.put(text)
    manifest = [_entry(i, t) for i, t in sorted(texts.items(), reverse=True)]

    pack = evidence_local.build_context_pack(1, manifest, query="quick brown fox")

    refs = [item["evidence_ref"] for item in pack["items"]]
    assert refs[0] == "E-2"
    assert "E-3" not in refs


def test_annotations_are_searched_on_their_own_plane(store):
    evidence_local.put("Plain text.", url=URL, annotations={"source_claims": ["Rates rose in March"]})
    pack = evidence_local.build_context_pack(1, [_entry(1, "Plain text.")], query="rates rose")
    assert pack["items"][0]["matched_plane"] == "source_claim"


def test_text_not_on_this_machine_is_reported_unresolved(store):
    evidence_local.put("here")
    pack = evidence_local.build_context_pack(1, [_entry(2, "elsewhere"), _entry(1, "here")])
    assert [item["evidence_ref"] for item in pack["items"]] == ["E-1"]
    assert pack["unresolved"] == [
        {"evidence_ref": "E-2", "content_sha256": ledger.compute_artifact_commitment("elsewhere"), "source_url": URL}
    ]
    assert pack["unresolved_count"] == 1


def test_a_tampered_record_is_unresolved_not_shown(store):
    digest = evidence_local.put("original")
    path = store / f"{digest}.json"
    record = json.loads(path.read_text())
    record["content"] = "forged"
    path.write_text(json.dumps(record))

    pack = evidence_local.build_context_pack(1, [_entry(1, "original")])
    assert pack["items"] == [] and [u["evidence_ref"] for u in pack["unresolved"]] == ["E-1"]
    assert "forged" not in json.dumps(pack)


def test_a_remote_pack_reads_nothing(store):
    evidence_local.put("local text")
    pack = evidence_local.build_context_pack(1, [_entry(1, "local text")], local=False)
    assert pack["items"] == [] and [u["evidence_ref"] for u in pack["unresolved"]] == ["E-1"]


@pytest.mark.parametrize("budget", [500, 2_000, 2_500, 5_000, 8_000, 20_000])
def test_the_pack_never_exceeds_its_budget(store, budget):
    texts = [
        f"clip {i} " + ('lorem ipsum "quoted" \n' * 400)[: evidence_local.MAX_CONTENT_CHARS - 10] for i in range(6)
    ]
    for text in texts:
        evidence_local.put(text, title="T" * 400, annotations={"summary": "s" * 2000})
    manifest = [_entry(i, t) for i, t in enumerate(texts)]

    pack = evidence_local.build_context_pack(1, manifest, limit=20, char_budget=budget)

    assert pack["char_budget"] == max(budget, evidence_local.MIN_PACK_CHARS)
    assert _size(pack) <= pack["char_budget"]
    assert pack["items"], "the first result must fit, truncated if need be"


def test_a_truncated_excerpt_says_so(store):
    text = "word " * 1500
    evidence_local.put(text)
    pack = evidence_local.build_context_pack(1, [_entry(1, text)], char_budget=2_000)

    item = pack["items"][0]
    assert "content" not in item
    assert item["verbatim_complete"] is False
    assert text.startswith(item["content_excerpt"])
    assert item["content_sha256"] == ledger.compute_artifact_commitment(text)


def test_an_overlong_query_is_refused(store):
    with pytest.raises(evidence_local.LocalEvidenceError):
        evidence_local.build_context_pack(1, [], query="q" * (evidence_local.MAX_QUERY_CHARS + 1))


# ------------------------------------------------------------------ over MCP


def test_the_mcp_pack_resolves_from_the_local_store_on_stdio(mcp_db, store, self_actor):
    task = models.Task(thread_id="t", title="T", status=models.TaskStatusEnum.DRAFT)
    mcp_db.add(task)
    mcp_db.commit()
    digest = evidence_local.put("The quick brown fox.", title="Foxes")
    clip, _ = evidence.create_clip(
        mcp_db, task_id=task.id, captured_by_actor_id=None, source_url=URL, source_type="public", content_sha256=digest
    )

    tool = server.mcp._tool_manager.get_tool("get_context_pack")
    pack = asyncio.run(tool.run({"task_id": task.id, "query": "brown fox"}, context=None))

    assert [item["evidence_ref"] for item in pack["items"]] == [clip.evidence_ref]
    assert pack["items"][0]["content"] == "The quick brown fox."
    # The ledger never saw the text.
    assert "brown fox" not in str([tuple(r) for r in mcp_db.execute(models.EvidenceClip.__table__.select())])


def test_a_remote_transport_is_not_local():
    class _RequestContext:
        request = object()

    class _Ctx:
        request_context = _RequestContext()

    assert server._transport_is_local(_Ctx()) is False
    assert server._transport_is_local(None) is True


def test_an_in_memory_session_is_local_and_resolves(mcp_db, store, self_actor):
    """Through the real SDK request path: no HTTP request attached, as on stdio."""
    from mcp.client._memory import InMemoryTransport
    from mcp.client.session import ClientSession

    task = models.Task(thread_id="t-mem", title="T", status=models.TaskStatusEnum.DRAFT)
    mcp_db.add(task)
    mcp_db.commit()
    digest = evidence_local.put("Resolved over the SDK.")
    evidence.create_clip(
        mcp_db, task_id=task.id, captured_by_actor_id=None, source_url=URL, source_type="public", content_sha256=digest
    )

    async def _call():
        async with (
            InMemoryTransport(server.mcp, raise_exceptions=True) as streams,
            ClientSession(*streams[:2]) as session,
        ):
            await session.initialize()
            return await session.call_tool("get_context_pack", {"task_id": task.id})

    result = asyncio.run(_call())
    assert not result.is_error
    pack = json.loads(result.content[0].text)
    assert pack["items"][0]["content"] == "Resolved over the SDK."


def test_an_undecidable_transport_does_not_read_the_disk():
    class _Ctx:
        @property
        def request_context(self):
            raise ValueError("no request in flight")

    assert server._transport_is_local(_Ctx()) is False


def test_a_long_unresolved_list_never_starves_the_items(store):
    """Items are fitted first; the unresolved list takes the room left and the count stays exact."""
    evidence_local.put("present", url=URL)
    manifest = [_entry(1000 + i, f"absent {i}") for i in range(300)] + [_entry(1, "present")]

    pack = evidence_local.build_context_pack(1, manifest, char_budget=4_000)

    assert [i["evidence_ref"] for i in pack["items"]] == ["E-1"]
    assert pack["unresolved_count"] == 300
    assert 0 < len(pack["unresolved"]) < 300
    assert _size(pack) <= 4_000


def test_the_mcp_pack_sees_clips_beyond_any_page(mcp_db, store, self_actor):
    """Round-1 finding: the pack used to read only the newest 500 clips."""
    task = models.Task(thread_id="t-many", title="T", status=models.TaskStatusEnum.DRAFT)
    mcp_db.add(task)
    mcp_db.commit()
    oldest_digest = evidence_local.put("The oldest cited clip.", url=URL)
    oldest, _ = evidence.create_clip(
        mcp_db,
        task_id=task.id,
        captured_by_actor_id=None,
        source_url=URL,
        source_type="public",
        content_sha256=oldest_digest,
    )
    for i in range(evidence.MAX_LIST + 5):
        mcp_db.add(
            models.EvidenceClip(
                task_id=task.id,
                source_url=f"https://example.com/{i}",
                source_type=models.EvidenceSourceTypeEnum.PUBLIC,
                content_sha256=ledger.compute_artifact_commitment(f"filler {i}"),
                content_algorithm=ledger.COMMITMENT_ALGORITHM,
            )
        )
    mcp_db.commit()

    tool = server.mcp._tool_manager.get_tool("get_context_pack")
    pack = asyncio.run(tool.run({"task_id": task.id, "query": "oldest cited clip"}, context=None))

    assert [i["evidence_ref"] for i in pack["items"]] == [oldest.evidence_ref]
    assert pack["unresolved_count"] == evidence.MAX_LIST + 5


@pytest.mark.parametrize(
    "bad",
    [
        {"content": "x", "title": "t" * (evidence_local.MAX_TITLE_CHARS + 1)},
        {"content": "x", "annotations": {"verified": True}},
        {"content": "   "},
    ],
)
def test_an_import_is_all_or_nothing_for_every_kind_of_bad_line(store, tmp_path, bad):
    """Round-2 finding: title and annotation checks ran only after earlier lines were written."""
    export = tmp_path / "mixed.jsonl"
    export.write_text(json.dumps({"content": "fine"}) + "\n" + json.dumps(bad) + "\n")

    with pytest.raises(evidence_local.LocalEvidenceError):
        evidence_local.import_jsonl(export)
    assert evidence_local.get(ledger.compute_artifact_commitment("fine")) is None


def test_a_match_the_budget_cannot_hold_is_counted_not_lost(store):
    """Round-2 finding: a near-2,000-character URL made a resolved clip vanish."""
    long_url = "https://example.com/" + "p" * 1_980
    evidence_local.put("Long-URL clip.", url=long_url)
    pack = evidence_local.build_context_pack(
        1, [_entry(1, "Long-URL clip.") | {"source_url": long_url}], char_budget=2_000
    )
    assert pack["items"] == []
    assert pack["omitted_count"] == 1
    assert _size(pack) <= 2_000


def test_nothing_is_omitted_when_everything_fits(store):
    evidence_local.put("fits", url=URL)
    assert evidence_local.build_context_pack(1, [_entry(1, "fits")])["omitted_count"] == 0


def test_concurrent_puts_of_the_same_words_keep_every_capture(store):
    """Round-3 finding: an unlocked read-modify-replace dropped a concurrent capture."""
    import threading

    pages = [f"https://example.com/page-{i}" for i in range(16)]
    barrier = threading.Barrier(len(pages))

    def _put(url):
        barrier.wait()
        evidence_local.put("Same words everywhere.", title=url, url=url)

    threads = [threading.Thread(target=_put, args=(url,)) for url in pages]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    record = evidence_local.get(ledger.compute_artifact_commitment("Same words everywhere."))
    assert sorted(c["url"] for c in record["captures"]) == sorted(pages)


@pytest.mark.parametrize(
    "captures",
    [
        [{"title": 7, "url": URL, "annotations": None}],
        [{"title": "T", "url": URL, "annotations": "not an object"}],
        ["not a capture"],
        "not a list",
    ],
)
def test_malformed_capture_metadata_cannot_break_a_pack(store, captures):
    """Round-3 finding: a valid digest with corrupted metadata raised inside ranking."""
    digest = evidence_local.put("Intact text.", url=URL)
    path = store / f"{digest}.json"
    record = json.loads(path.read_text())
    record["captures"] = captures
    path.write_text(json.dumps(record))

    pack = evidence_local.build_context_pack(1, [_entry(1, "Intact text.")], query="intact text")

    assert [i["evidence_ref"] for i in pack["items"]] == ["E-1"]
    assert pack["items"][0]["source_title"] == "" and pack["items"][0]["annotations"] is None
