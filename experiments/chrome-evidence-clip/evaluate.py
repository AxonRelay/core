"""Summarize paired trials, retrieval quality, and Evidence Clip citations."""

import argparse
import csv
import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

FIELDS = {
    "batch",
    "task_id",
    "condition",
    "task_type",
    "decision_seconds",
    "blind_quality_1_to_5",
    "baseline_pair_task_id",
}
EVIDENCE_ID = re.compile(r"(E-[0-9]+|\[E-[0-9]+\])")


@dataclass(frozen=True)
class Trial:
    condition: str
    kind: str
    baseline: str
    seconds: float
    quality: int
    line: int
    retrieval: tuple[set[str], set[str]] | None
    citations: tuple[set[str], set[str]] | None


def read_ids(value, field, line):
    """A dash records an observed empty set. A blank cell is unmeasured."""
    value = value.strip()
    if not value:
        return None
    if value == "-":
        return set()
    ids = set()
    for token in re.split(r"[\s,]+", value):
        if not EVIDENCE_ID.fullmatch(token):
            raise ValueError(f"row {line}: invalid Evidence ID in {field}")
        ref = token.strip("[]")
        if ref in ids:
            raise ValueError(f"row {line}: duplicate Evidence ID in {field}")
        ids.add(ref)
    return ids


def read_measurement(row, fields, line):
    first, second = (read_ids(row.get(field, ""), field, line) for field in fields)
    if first is None and second is None:
        return None
    if first is None or second is None:
        raise ValueError(f"row {line}: fill both {fields[0]} and {fields[1]} or leave both blank")
    return first, second


def summarize_evidence(trials):
    retrieval = [trial.retrieval for trial in trials if trial.retrieval is not None]
    citations = [trial.citations for trial in trials if trial.citations is not None]
    expected_count = sum(len(expected) for expected, _ in retrieval)
    returned_count = sum(len(returned) for _, returned in retrieval)
    hit_count = sum(len(expected & returned) for expected, returned in retrieval)
    cited_count = sum(len(cited) for cited, _ in citations)
    invalid_count = sum(len(invalid) for _, invalid in citations)
    return {
        "retrieval": {
            "measured_trials": len(retrieval),
            "expected_ids": expected_count,
            "returned_ids": returned_count,
            "relevant_returned_ids": hit_count,
            "recall_at_5_pct": 100 * hit_count / expected_count if expected_count else None,
            "relevant_at_5_pct": 100 * hit_count / returned_count if returned_count else None,
        },
        "citations": {
            "measured_trials": len(citations),
            "trials_without_citations": sum(not cited for cited, _ in citations),
            "cited_ids": cited_count,
            "missing_or_invalid_ids": invalid_count,
            "integrity_pct": 100 * (cited_count - invalid_count) / cited_count if cited_count else None,
        },
    }


def evaluate(stream):
    reader = csv.DictReader(stream, strict=True)
    headers = reader.fieldnames or []
    if len(set(headers)) != len(headers) or not FIELDS.issubset(headers):
        raise ValueError("expected unique columns from evaluation-template.csv")

    trials = {}
    for line, row in enumerate(reader, start=2):
        if None in row or None in row.values():
            raise ValueError(f"row {line}: column count differs from header")
        batch, task, condition, kind, baseline = (
            row[key].strip() for key in ("batch", "task_id", "condition", "task_type", "baseline_pair_task_id")
        )
        if not batch or not task or condition not in {"A", "B", "C"} or not kind:
            raise ValueError(f"row {line}: missing trial identity or invalid condition")
        if (batch, task) in trials:
            raise ValueError(f"row {line}: task ID repeated within batch")
        if (condition == "A") == bool(baseline):
            raise ValueError(f"row {line}: only B and C require a baseline task ID")
        try:
            seconds = float(row["decision_seconds"])
            quality = int(row["blind_quality_1_to_5"])
        except ValueError:
            raise ValueError(f"row {line}: expected numeric time and quality") from None
        if not math.isfinite(seconds) or seconds <= 0 or not 1 <= quality <= 5:
            raise ValueError(f"row {line}: time must be positive and quality must be 1 to 5")
        retrieval = read_measurement(row, ("expected_relevant_ids", "returned_top5_ids"), line)
        if retrieval is not None and (not retrieval[0] or len(retrieval[1]) > 5):
            raise ValueError(f"row {line}: retrieval needs expected IDs and at most five returned IDs")
        citations = read_measurement(row, ("cited_ids", "missing_or_invalid_ids"), line)
        if citations is not None and not citations[1].issubset(citations[0]):
            raise ValueError(f"row {line}: missing or invalid IDs must be a subset of cited IDs")
        trials[batch, task] = Trial(condition, kind, baseline, seconds, quality, line, retrieval, citations)

    comparisons = {}
    for arm in ("B", "C"):
        reductions, deltas = [], []
        used = set()
        for (batch, _), trial in trials.items():
            if trial.condition != arm:
                continue
            key = (batch, trial.baseline)
            reference = trials.get(key)
            if reference is None or reference.condition != "A" or reference.kind != trial.kind:
                raise ValueError(f"row {trial.line}: expected a same-batch, same-type A baseline")
            if key in used:
                raise ValueError(f"row {trial.line}: baseline already paired with this condition")
            used.add(key)
            reductions.append(100 * (1 - trial.seconds / reference.seconds))
            deltas.append(trial.quality - reference.quality)
        comparisons[arm] = {
            "pairs": len(used),
            "median_decision_time_reduction_pct": statistics.median(reductions) if used else None,
            "mean_blind_quality_delta": statistics.mean(deltas) if used else None,
            **summarize_evidence([trial for trial in trials.values() if trial.condition == arm]),
        }
    return comparisons


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    args = parser.parse_args()
    try:
        with args.csv.open(encoding="utf-8-sig", newline="") as stream:
            print(json.dumps(evaluate(stream), indent=2, allow_nan=False))
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f"evaluation failed: {exc}\n")
