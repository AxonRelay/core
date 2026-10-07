"""Evidence Clips, local half: the text store and the Context Pack (ADR-014).

The shared ledger knows a clip by its digest (app/evidence.py). The excerpt,
the page title, the full URL and any model annotations live here, in a
content-addressed directory on the machine that captured them:

    $AXONRELAY_EVIDENCE_DIR/<sha256>.json      (default ~/.axonrelay/evidence)

A record is accepted only if its text hashes to its file name, and is checked
again on every read, so a Context Pack never shows text that does not match
the commitment the ledger holds.

The Context Pack is assembled here from the ledger's manifest. Ranking is
deterministic (normalized trigram overlap, no model call) and runs over text
that never leaves this machine. Over a remote transport there is no local
store to read, and the pack carries digests and provenance only.

CLI:

    python -m app.evidence_local put --title T --url U < excerpt.txt
    python -m app.evidence_local import clips.jsonl     # the Chrome adapter's export
    python -m app.evidence_local show <sha256>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

from app import ledger

STORE_ENV = "AXONRELAY_EVIDENCE_DIR"
DEFAULT_STORE = Path.home() / ".axonrelay" / "evidence"

MAX_CONTENT_CHARS = 8_000
MAX_TITLE_CHARS = 500
MAX_ANNOTATIONS_BYTES = 16_000
ANNOTATION_FIELDS = ("summary", "tags", "source_claims", "interpretations")

MAX_PACK_ITEMS = 20
MAX_PACK_CHARS = 20_000
#: The fixed part of a pack (contract, digests, URL) is ~1k characters; below
#: this floor not even one truncated item would fit.
MIN_PACK_CHARS = 2_000
MAX_QUERY_CHARS = 500
#: A single shared trigram is common in prose and too weak to surface a clip.
#: An exact normalized substring scores +2, so this only gates fuzzy matches.
MIN_SCORE = 0.2

EPISTEMIC_CONTRACT = {
    "content": "verbatim operator-selected source excerpt; the source may be wrong",
    "content_excerpt": "budget-truncated prefix; never cite as the complete excerpt",
    "source_claims": "claims attributed to the source; not verified facts",
    "interpretations": "derived implications or hypotheses; never original evidence",
    "summary_and_tags": "derived retrieval aids; never original evidence",
    "verified_fact": "requires a separate explicit verification; a model cannot create one",
    "similarity": "retrieval relevance only; never truth or approval",
    "unresolved": "the ledger holds a commitment whose text is not on this machine",
}


class LocalEvidenceError(ValueError):
    """A record the local store refuses. Messages never repeat the content."""


def store_dir(path: str | os.PathLike | None = None) -> Path:
    if path is not None:
        return Path(path)
    configured = os.environ.get(STORE_ENV, "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_STORE


def _validate_annotations(annotations: dict | None) -> None:
    if annotations is None:
        return
    if not isinstance(annotations, dict):
        raise LocalEvidenceError("annotations must be an object")
    unknown = set(annotations) - set(ANNOTATION_FIELDS)
    if unknown:
        raise LocalEvidenceError("annotations may only hold summary, tags, source_claims and interpretations")
    if not isinstance(annotations.get("summary", ""), str):
        raise LocalEvidenceError("annotations.summary must be a string")
    for field in ("tags", "source_claims", "interpretations"):
        values = annotations.get(field, [])
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise LocalEvidenceError(f"annotations.{field} must be a list of strings")
    if len(json.dumps(annotations, ensure_ascii=False).encode("utf-8")) > MAX_ANNOTATIONS_BYTES:
        raise LocalEvidenceError(f"annotations must be at most {MAX_ANNOTATIONS_BYTES} UTF-8 bytes")


def put(
    content: str,
    *,
    title: str | None = None,
    url: str | None = None,
    annotations: dict | None = None,
    store: str | os.PathLike | None = None,
) -> str:
    """Store an excerpt locally and return its digest - the value the ledger records.

    The text is hashed exactly as given: a client that trims whitespace must
    trim before it hashes, or the two halves will not meet.
    """
    if not isinstance(content, str) or not content.strip():
        raise LocalEvidenceError("content must not be empty")
    if len(content) > MAX_CONTENT_CHARS:
        raise LocalEvidenceError(f"content must be at most {MAX_CONTENT_CHARS} characters")
    if title is not None and len(title) > MAX_TITLE_CHARS:
        raise LocalEvidenceError(f"title must be at most {MAX_TITLE_CHARS} characters")
    _validate_annotations(annotations)

    digest = ledger.compute_artifact_commitment(content)
    directory = store_dir(store)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = {
        "content_sha256": digest,
        "content_algorithm": ledger.COMMITMENT_ALGORITHM,
        "content": content,
        "title": title,
        "url": url,
        "annotations": annotations,
    }
    # Write-then-rename, so a reader never sees half a record.
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, directory / f"{digest}.json")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return digest


def get(content_sha256: str, *, store: str | os.PathLike | None = None) -> dict | None:
    """The local record for a digest, or None when this machine does not hold it.

    A record whose text no longer hashes to its name is refused, not returned:
    showing it would present different words under the ledger's commitment.
    """
    if not isinstance(content_sha256, str) or len(content_sha256) != 64:
        return None
    if any(ch not in "0123456789abcdef" for ch in content_sha256):
        return None
    path = store_dir(store) / f"{content_sha256}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise LocalEvidenceError(f"local record {content_sha256} is unreadable") from None
    content = record.get("content") if isinstance(record, dict) else None
    if not isinstance(content, str) or ledger.compute_artifact_commitment(content) != content_sha256:
        raise LocalEvidenceError(f"local record {content_sha256} does not match its digest")
    return record


def import_jsonl(path: str | os.PathLike, *, store: str | os.PathLike | None = None) -> list[str]:
    """Load the Chrome adapter's export. Each line: content, title, url, annotations, content_sha256.

    When a line states ``content_sha256`` it must equal the digest of its
    content; a line that disagrees is refused rather than stored under a
    digest the ledger may not hold.
    """
    digests = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                raise LocalEvidenceError(f"line {number} is not JSON") from None
            if not isinstance(entry, dict):
                raise LocalEvidenceError(f"line {number} is not an object")
            digest = put(
                entry.get("content", ""),
                title=entry.get("title"),
                url=entry.get("url"),
                annotations=entry.get("annotations"),
                store=store,
            )
            stated = entry.get("content_sha256")
            if stated is not None and stated != digest:
                (store_dir(store) / f"{digest}.json").unlink(missing_ok=True)
                raise LocalEvidenceError(f"line {number}: content_sha256 does not match its content")
            digests.append(digest)
    return digests


# ------------------------------------------------------------------ Context Pack


def _normalized(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _trigrams(value: str) -> set[str]:
    normalized = _normalized(value)
    if len(normalized) < 3:
        return {normalized} if normalized else set()
    return {normalized[index : index + 3] for index in range(len(normalized) - 2)}


def _search_planes(record: dict) -> dict[str, str]:
    annotations = record.get("annotations") or {}
    return {
        "original": " ".join([record.get("title") or "", record["content"]]),
        "source_claim": " ".join(annotations.get("source_claims", [])),
        "interpretation": " ".join(
            [
                str(annotations.get("summary", "")),
                " ".join(annotations.get("tags", [])),
                " ".join(annotations.get("interpretations", [])),
            ]
        ),
    }


def _score(record: dict, query_normalized: str, query_grams: set[str]) -> tuple[float, str]:
    weights = {"original": 1.0, "source_claim": 0.9, "interpretation": 0.7}
    candidates = []
    for plane, text in _search_planes(record).items():
        exact_bonus = 2.0 if query_normalized in _normalized(text) else 0.0
        overlap = len(query_grams & _trigrams(text)) / len(query_grams) if query_grams else 0.0
        candidates.append(((exact_bonus + overlap) * weights[plane], plane))
    return max(candidates)


def _json_characters(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _item(entry: dict, record: dict, score: float, matched_plane: str) -> dict:
    """The deliberately small projection an agent prompt receives."""
    return {
        "evidence_ref": entry["evidence_ref"],
        "content_sha256": entry["content_sha256"],
        "source_url": entry["source_url"],
        "source_title": record.get("title") or "",
        "content": record["content"],
        "annotations": record.get("annotations"),
        "captured_at": entry["captured_at"],
        "score": round(score, 4),
        "matched_plane": matched_plane,
    }


def _fit_first_item(payload: dict, budget: int) -> dict:
    """Bound even an unusually large first result to the caller's budget.

    Annotations go first, then the title, then the excerpt is cut to a prefix
    marked as such. The ref, digest and URL stay: without them the item cannot
    be cited or checked.
    """
    fitted = dict(payload)
    if _json_characters(fitted) <= budget:
        return fitted
    fitted["annotations"] = None
    fitted["annotations_omitted"] = True
    if _json_characters(fitted) > budget:
        fitted["source_title"] = ""
    if _json_characters(fitted) > budget:
        excerpt = str(fitted.pop("content"))
        fitted["content_excerpt"] = excerpt
        fitted["verbatim_complete"] = False
        # JSON escaping can make one character cost two, so cut until it fits.
        while excerpt and (overflow := _json_characters(fitted) - budget) > 0:
            excerpt = excerpt[: max(0, len(excerpt) - overflow)]
            fitted["content_excerpt"] = excerpt
    for field in ("matched_plane", "captured_at", "score"):
        if _json_characters(fitted) <= budget:
            break
        fitted.pop(field, None)
    return fitted


def build_context_pack(
    task_id: int,
    manifest: list[dict],
    *,
    query: str = "",
    limit: int = 5,
    char_budget: int = 8_000,
    store: str | os.PathLike | None = None,
    local: bool = True,
) -> dict:
    """Assemble a Context Pack from the ledger's manifest (``evidence.clip_to_dict`` rows).

    ``local=False`` is the remote-transport case: nothing is read from disk,
    every clip is listed as unresolved, and no query can be ranked.
    ``unresolved`` names the refs whose text this machine does not hold, so an
    agent can tell "no match" from "not here".
    """
    started = time.perf_counter()
    if len(query) > MAX_QUERY_CHARS:
        raise LocalEvidenceError(f"query must be at most {MAX_QUERY_CHARS} characters")
    limit = max(1, min(limit, MAX_PACK_ITEMS))
    char_budget = max(MIN_PACK_CHARS, min(char_budget, MAX_PACK_CHARS))

    resolved: list[tuple[dict, dict]] = []
    unresolved: list[str] = []
    for entry in manifest:
        record = None
        if local:
            try:
                record = get(entry["content_sha256"], store=store)
            except LocalEvidenceError:
                record = None
        if record is None:
            unresolved.append(entry["evidence_ref"])
        else:
            resolved.append((entry, record))

    query_normalized = _normalized(query)
    if query_normalized:
        query_grams = _trigrams(query_normalized)
        scored = [(entry, record, *_score(record, query_normalized, query_grams)) for entry, record in resolved]
        scored = [result for result in scored if result[2] >= MIN_SCORE]
        scored.sort(key=lambda r: (r[2], r[0]["captured_at"], r[0]["id"]), reverse=True)
    else:
        # The manifest is newest first already.
        scored = [(entry, record, 1.0, "recency") for entry, record in resolved]

    def pack(items: list[dict], elapsed_ms: float) -> dict:
        return {
            "task_id": task_id,
            "query": query,
            "untrusted_content": True,
            "epistemic_contract": EPISTEMIC_CONTRACT,
            "items": items,
            "unresolved": unresolved,
            "char_budget": char_budget,
            "elapsed_ms": elapsed_ms,
        }

    # Sized with a placeholder elapsed value at least as long as the real one.
    items: list[dict] = []
    for entry, record, score, plane in scored[:limit]:
        payload = _item(entry, record, score, plane)
        if _json_characters(pack([*items, payload], 999999.999)) <= char_budget:
            items.append(payload)
            continue
        if not items:
            available = char_budget - _json_characters(pack([], 999999.999))
            payload = _fit_first_item(payload, max(0, available - 1))
            if _json_characters(pack([payload], 999999.999)) <= char_budget:
                items.append(payload)
        break

    result = pack(items, round((time.perf_counter() - started) * 1_000, 3))
    if _json_characters(result) > char_budget:
        # A manifest so long that its unresolved refs alone overflow the
        # budget: report the count rather than exceed what the caller asked for.
        result["unresolved"] = []
        result["unresolved_count"] = len(unresolved)
    return result


# ------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evidence_local", description=__doc__.split("\n\n")[0])
    parser.add_argument("--store", help=f"store directory (default ${STORE_ENV} or {DEFAULT_STORE})")
    sub = parser.add_subparsers(dest="command", required=True)
    put_cmd = sub.add_parser("put", help="store stdin as one excerpt and print its sha256")
    put_cmd.add_argument("--title")
    put_cmd.add_argument("--url", help="full source URL; kept locally, the ledger gets it without query/fragment")
    put_cmd.add_argument("--annotations", help="path to a JSON file of annotations")
    import_cmd = sub.add_parser("import", help="load a JSONL export and print each sha256")
    import_cmd.add_argument("path")
    show_cmd = sub.add_parser("show", help="print the local record for a sha256")
    show_cmd.add_argument("sha256")
    args = parser.parse_args(argv)

    try:
        if args.command == "put":
            annotations = None
            if args.annotations:
                annotations = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
            print(
                put(sys.stdin.read().strip(), title=args.title, url=args.url, annotations=annotations, store=args.store)
            )
        elif args.command == "import":
            for digest in import_jsonl(args.path, store=args.store):
                print(digest)
        else:
            record = get(args.sha256, store=args.store)
            if record is None:
                print("not in the local store", file=sys.stderr)
                return 1
            print(json.dumps(record, ensure_ascii=False, indent=2))
    except LocalEvidenceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
