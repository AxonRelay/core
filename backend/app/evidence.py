"""Evidence Clip capture, deterministic retrieval, and evaluation.

This is deliberately not a general memory or RAG layer. Clips are explicit,
task-scoped excerpts selected by the operator; model output is optional
annotation data and never controls provenance, status, or task association.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app import models

MAX_QUOTE_CHARS = 8_000
MAX_ANNOTATIONS_BYTES = 16_000
MAX_LOCATOR_BYTES = 4_000
MAX_PACK_ITEMS = 20
MAX_PACK_CHARS = 20_000
MAX_QUERY_CHARS = 500
MAX_SEARCH_CANDIDATES = 10_000
_EVIDENCE_REF = re.compile(r"\[E-(\d+)\]")


def hash_quote(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


_SENSITIVE_QUERY_KEYS = {"access_token", "auth", "code", "id_token", "key", "password", "secret", "session", "token"}


def sanitize_source_url(source_url: str) -> str:
    """Remove credentials, fragments, and common secret-bearing query values."""
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source_url must be an absolute http(s) URL")
    if len(source_url) > 2000:
        raise ValueError("source_url must be at most 2000 characters")
    host = parsed.netloc.rsplit("@", 1)[-1]
    query = urlencode(
        [
            (key, "[REDACTED]" if key.casefold() in _SENSITIVE_QUERY_KEYS else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunparse((parsed.scheme, host, parsed.path, parsed.params, query, ""))


def _validate_annotations(annotations: dict | None) -> None:
    if annotations is None:
        return
    encoded = json.dumps(annotations, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_ANNOTATIONS_BYTES:
        raise ValueError(f"annotations must be at most {MAX_ANNOTATIONS_BYTES} UTF-8 bytes")
    allowed = {"summary", "tags", "source_claims", "interpretations"}
    unknown = set(annotations) - allowed
    if unknown:
        raise ValueError(f"unsupported annotation fields: {', '.join(sorted(unknown))}")
    if not isinstance(annotations.get("summary", ""), str):
        raise ValueError("annotations.summary must be a string")
    for field in ("tags", "source_claims", "interpretations"):
        values = annotations.get(field, [])
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"annotations.{field} must be a list of strings")


def _validate_json_size(value: dict | None, *, label: str, maximum: int) -> None:
    if value is not None and len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > maximum:
        raise ValueError(f"{label} must be at most {maximum} UTF-8 bytes")


def create_clip(
    db: Session,
    *,
    task_id: int,
    captured_by_actor_id: int | None,
    source_url: str,
    source_title: str,
    source_type: str,
    quote: str,
    locator: dict | None = None,
    annotations: dict | None = None,
    extractor: str | None = None,
    extractor_version: str | None = None,
    prompt_version: str | None = None,
    inference_location: str = "none",
    extraction_ms: float | None = None,
) -> models.EvidenceClip:
    """Store a user-selected excerpt; duplicate capture is idempotent.

    URL, title, task, and quote must come from the browser/operator, not from
    the model annotation payload. The server does not fetch ``source_url``.
    """
    if not db.get(models.Task, task_id):
        raise ValueError(f"Task {task_id} not found")
    if captured_by_actor_id is not None and not db.get(models.Actor, captured_by_actor_id):
        raise ValueError(f"Actor {captured_by_actor_id} not found")
    source_url = sanitize_source_url(source_url)
    quote = quote.strip()
    if not quote or len(quote) > MAX_QUOTE_CHARS:
        raise ValueError(f"quote must contain 1..{MAX_QUOTE_CHARS} characters")
    if not source_title.strip() or len(source_title) > 500:
        raise ValueError("source_title must contain 1..500 characters")
    try:
        resolved_source_type = models.EvidenceSourceTypeEnum(source_type)
    except ValueError as exc:
        raise ValueError("source_type must be public or personal") from exc
    _validate_annotations(annotations)
    _validate_json_size(locator, label="locator", maximum=MAX_LOCATOR_BYTES)
    for label, value in (
        ("extractor", extractor),
        ("extractor_version", extractor_version),
        ("prompt_version", prompt_version),
    ):
        if value is not None and len(value) > 100:
            raise ValueError(f"{label} must be at most 100 characters")
    if extraction_ms is not None and extraction_ms < 0:
        raise ValueError("extraction_ms must not be negative")
    if inference_location not in {"none", "device", "lan"}:
        raise ValueError("inference_location must be none, device, or lan")

    digest = hash_quote(quote)
    existing = (
        db.query(models.EvidenceClip)
        .filter(
            models.EvidenceClip.task_id == task_id,
            models.EvidenceClip.source_url == source_url,
            models.EvidenceClip.quote_sha256 == digest,
        )
        .first()
    )
    if existing:
        return existing

    clip = models.EvidenceClip(
        task_id=task_id,
        captured_by_actor_id=captured_by_actor_id,
        source_url=source_url,
        source_title=source_title.strip(),
        source_type=resolved_source_type,
        quote=quote,
        quote_sha256=digest,
        locator=locator,
        annotations=annotations,
        extractor=extractor,
        extractor_version=extractor_version,
        prompt_version=prompt_version,
        inference_location=inference_location,
        extraction_ms=extraction_ms,
    )
    db.add(clip)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # A concurrent duplicate won the unique constraint race. Return that
        # immutable row so double-click/retry remains idempotent.
        existing = (
            db.query(models.EvidenceClip)
            .filter(
                models.EvidenceClip.task_id == task_id,
                models.EvidenceClip.source_url == source_url,
                models.EvidenceClip.quote_sha256 == digest,
            )
            .first()
        )
        if existing:
            return existing
        raise
    db.refresh(clip)
    return clip


def get_clip(db: Session, clip_id: int) -> models.EvidenceClip | None:
    return (
        db.query(models.EvidenceClip)
        .options(joinedload(models.EvidenceClip.feedback_events))
        .filter(models.EvidenceClip.id == clip_id)
        .first()
    )


def list_clips(
    db: Session,
    task_id: int,
    *,
    include_rejected: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> list[models.EvidenceClip]:
    query = db.query(models.EvidenceClip).filter(models.EvidenceClip.task_id == task_id)
    if not include_rejected:
        query = query.filter(models.EvidenceClip.status != models.EvidenceStatusEnum.REJECTED)
    return (
        query.order_by(models.EvidenceClip.captured_at.desc(), models.EvidenceClip.id.desc())
        .offset(max(0, skip))
        .limit(max(1, min(limit, MAX_SEARCH_CANDIDATES)))
        .all()
    )


def record_feedback(
    db: Session,
    clip_id: int,
    actor_id: int | None,
    verdict: str,
    comment: str | None = None,
) -> models.EvidenceFeedback:
    clip = db.get(models.EvidenceClip, clip_id)
    if not clip:
        raise ValueError(f"Evidence clip {clip_id} not found")
    if actor_id is not None and not db.get(models.Actor, actor_id):
        raise ValueError(f"Actor {actor_id} not found")
    try:
        resolved = models.EvidenceFeedbackVerdictEnum(verdict)
    except ValueError as exc:
        raise ValueError("verdict must be relevant, irrelevant, or misleading") from exc
    event = models.EvidenceFeedback(
        evidence_clip_id=clip.id,
        actor_id=actor_id,
        verdict=resolved,
        comment=comment,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _normalized(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _trigrams(value: str) -> set[str]:
    normalized = _normalized(value)
    if len(normalized) < 3:
        return {normalized} if normalized else set()
    return {normalized[index : index + 3] for index in range(len(normalized) - 2)}


def _search_planes(clip: models.EvidenceClip) -> dict[str, str]:
    annotations = clip.annotations or {}
    return {
        "original": " ".join([clip.source_title, clip.quote]),
        "source_claim": " ".join(annotations.get("source_claims", [])),
        "interpretation": " ".join(
            [
                str(annotations.get("summary", "")),
                " ".join(str(tag) for tag in annotations.get("tags", []) if isinstance(tag, str)),
                " ".join(annotations.get("interpretations", [])),
            ]
        ),
    }


def _score(clip: models.EvidenceClip, query_normalized: str, query_grams: set[str]) -> tuple[float, str]:
    if not query_normalized:
        return 1.0, "recency"
    weights = {"original": 1.0, "source_claim": 0.9, "interpretation": 0.7}
    candidates = []
    for plane, text in _search_planes(clip).items():
        text_normalized = _normalized(text)
        exact_bonus = 2.0 if query_normalized in text_normalized else 0.0
        overlap = len(query_grams & _trigrams(text)) / len(query_grams) if query_grams else 0.0
        candidates.append(((exact_bonus + overlap) * weights[plane], plane))
    return max(candidates)


def clip_to_dict(clip: models.EvidenceClip, *, score: float | None = None) -> dict:
    payload = {
        "id": clip.id,
        "evidence_ref": clip.evidence_ref,
        "task_id": clip.task_id,
        "source_url": clip.source_url,
        "source_title": clip.source_title,
        "source_type": str(clip.source_type),
        "quote": clip.quote,
        "quote_sha256": clip.quote_sha256,
        "locator": clip.locator,
        "annotations": clip.annotations,
        "extractor": clip.extractor,
        "extractor_version": clip.extractor_version,
        "prompt_version": clip.prompt_version,
        "inference_location": clip.inference_location,
        "extraction_ms": clip.extraction_ms,
        "status": str(clip.status),
        "captured_at": clip.captured_at.isoformat(),
    }
    if score is not None:
        payload["score"] = round(score, 4)
        payload["match_reason"] = "task-scoped normalized trigram match"
    return payload


def _context_item(clip: models.EvidenceClip, score: float, matched_plane: str) -> dict:
    """The intentionally small projection exposed to an agent prompt."""
    return {
        "evidence_ref": clip.evidence_ref,
        "source_url": clip.source_url,
        "source_title": clip.source_title,
        "quote": clip.quote,
        "quote_sha256": clip.quote_sha256,
        "annotations": clip.annotations,
        "status": str(clip.status),
        "captured_at": clip.captured_at.isoformat(),
        "score": round(score, 4),
        "matched_plane": matched_plane,
        "match_reason": "task-scoped plane-weighted normalized trigram match",
    }


def _json_characters(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _fit_first_item(payload: dict, budget: int) -> dict:
    """Bound even an unusually large first result to the caller's item budget."""
    fitted = dict(payload)
    if _json_characters(fitted) <= budget:
        return fitted
    fitted["annotations"] = None
    fitted["annotations_omitted"] = True
    # Preserve the exact source excerpt as long as possible; URL/title are
    # useful provenance but less important than the text a decision cites.
    for field in ("source_title", "source_url"):
        overflow = _json_characters(fitted) - budget
        if overflow <= 0:
            break
        value = str(fitted[field])
        keep = max(0, len(value) - overflow - 3)
        fitted[field] = f"{value[:keep]}..." if keep else ""
        fitted[f"{field}_truncated"] = True
    if _json_characters(fitted) > budget and "quote" in fitted:
        quote = str(fitted.pop("quote"))
        fitted["quote_excerpt"] = quote
        fitted["verbatim_complete"] = False
        overflow = _json_characters(fitted) - budget
        if overflow > 0:
            keep = max(0, len(quote) - overflow)
            fitted["quote_excerpt"] = quote[:keep]
    # Very small caller budgets may not fit optional ranking metadata.
    for field in ("match_reason", "captured_at", "score", "status"):
        if _json_characters(fitted) <= budget:
            break
        fitted.pop(field, None)
    if _json_characters(fitted) > budget and "quote_excerpt" in fitted:
        excerpt = str(fitted["quote_excerpt"])
        overflow = _json_characters(fitted) - budget
        fitted["quote_excerpt"] = excerpt[: max(0, len(excerpt) - overflow)]
    for field in ("source_title_truncated", "source_url_truncated"):
        if _json_characters(fitted) <= budget:
            break
        fitted.pop(field, None)
    return fitted


def get_context_pack(
    db: Session,
    task_id: int,
    query: str = "",
    limit: int = 5,
    char_budget: int = 8_000,
) -> dict:
    """Return a deterministic, source-grounded pack without LLM generation."""
    started = time.perf_counter()
    if not db.get(models.Task, task_id):
        raise ValueError(f"Task {task_id} not found")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"query must be at most {MAX_QUERY_CHARS} characters")
    limit = max(1, min(limit, MAX_PACK_ITEMS))
    char_budget = max(1_000, min(char_budget, MAX_PACK_CHARS))
    query_normalized = _normalized(query)
    if not query_normalized:
        candidates = (
            db.query(models.EvidenceClip)
            .filter(
                models.EvidenceClip.task_id == task_id,
                models.EvidenceClip.status != models.EvidenceStatusEnum.REJECTED,
            )
            .order_by(models.EvidenceClip.captured_at.desc(), models.EvidenceClip.id.desc())
            .limit(limit)
            .all()
        )
        scored = [(clip, 1.0, "recency") for clip in candidates]
    else:
        query_grams = _trigrams(query_normalized)
        scored = [
            (clip, *_score(clip, query_normalized, query_grams))
            for clip in list_clips(db, task_id, limit=MAX_SEARCH_CANDIDATES)
        ]
        # A single accidental trigram (common in natural-language prose) is too
        # weak to surface a clip. Exact normalized substring matches already
        # receive a +2 bonus; fuzzy matches need at least 20% query coverage.
        scored = [result for result in scored if result[1] >= 0.2]
    scored.sort(key=lambda result: (result[1], result[0].captured_at, result[0].id), reverse=True)

    contract = {
        "quote": "verbatim operator-selected source record; the source may be wrong",
        "quote_excerpt": "budget-truncated prefix; never cite as the complete verbatim source record",
        "source_claims": "attributed claims extracted from the source; not verified facts",
        "interpretations": "derived implications or hypotheses; never original evidence",
        "summary_and_tags": "derived retrieval aids; never original evidence",
        "verified_fact": "requires a separate explicit verification operation; local AI cannot create one",
        "similarity": "retrieval relevance only; never truth or approval",
    }

    def pack_size(candidate_items: list[dict], item_characters: int) -> int:
        return _json_characters(
            {
                "task_id": task_id,
                "query": query,
                "untrusted_content": True,
                "epistemic_contract": contract,
                "items": candidate_items,
                "characters": item_characters,
                "char_budget": char_budget,
                "elapsed_ms": 999999.999,
            }
        )

    items: list[dict] = []
    characters = 0
    for clip, score, matched_plane in scored[:limit]:
        payload = _context_item(clip, score, matched_plane)
        item_chars = _json_characters(payload)
        if pack_size([*items, payload], characters + item_chars) <= char_budget:
            items.append(payload)
            characters += item_chars
            continue
        if items:
            break
        available = max(100, char_budget - pack_size([], 0))
        payload = _fit_first_item(payload, available)
        item_chars = _json_characters(payload)
        overflow = pack_size([payload], item_chars) - char_budget
        if overflow > 0:
            payload = _fit_first_item(payload, max(0, available - overflow))
            item_chars = _json_characters(payload)
        if pack_size([payload], item_chars) <= char_budget:
            items.append(payload)
            characters += item_chars
        break

    pack = {
        "task_id": task_id,
        "query": query,
        "untrusted_content": True,
        "epistemic_contract": contract,
        "items": items,
        "characters": characters,
        "char_budget": char_budget,
        "elapsed_ms": round((time.perf_counter() - started) * 1_000, 3),
    }
    if _json_characters(pack) > char_budget:
        raise RuntimeError("Context Pack exceeded its serialized character budget")
    return pack


def validate_draft_references(db: Session, task_id: int, content: str) -> dict:
    referenced = [f"E-{value}" for value in _EVIDENCE_REF.findall(content)]
    clips = db.query(models.EvidenceClip).filter(models.EvidenceClip.task_id == task_id).all()
    by_ref = {clip.evidence_ref: clip for clip in clips}
    tampered = [
        ref for ref in referenced if ref in by_ref and hash_quote(by_ref[ref].quote) != by_ref[ref].quote_sha256
    ]
    rejected = [ref for ref in referenced if ref in by_ref and by_ref[ref].status == models.EvidenceStatusEnum.REJECTED]
    valid = [ref for ref in referenced if ref in by_ref and ref not in tampered and ref not in rejected]
    # Preserve first-citation order while storing each immutable source once.
    manifest = [
        {
            "evidence_ref": ref,
            "quote_sha256": by_ref[ref].quote_sha256,
            "source_url": by_ref[ref].source_url,
        }
        for ref in dict.fromkeys(valid)
    ]
    return {
        "referenced": referenced,
        "valid": valid,
        "tampered": tampered,
        "rejected": rejected,
        "missing": [ref for ref in referenced if ref not in by_ref],
        "manifest": manifest,
    }
