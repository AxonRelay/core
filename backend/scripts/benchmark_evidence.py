"""Repeatable local retrieval benchmark for the Evidence Clip MVP."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import evidence, models
from app.database import Base


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clips", type=int, default=10_000)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--max-p95-ms", type=float, default=300.0)
    args = parser.parse_args()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    task = models.Task(thread_id="benchmark", title="Evidence retrieval benchmark")
    db.add(task)
    db.commit()
    db.refresh(task)

    now = datetime.utcnow()
    rows = []
    for index in range(args.clips):
        quote = (
            "Chrome Prompt API local inference privacy evidence"
            if index == args.clips - 1
            else f"Unrelated governance note number {index}"
        )
        rows.append(
            models.EvidenceClip(
                task_id=task.id,
                source_url=f"https://example.test/{index}",
                source_title=f"Source {index}",
                source_type=models.EvidenceSourceTypeEnum.PUBLIC,
                quote=quote,
                quote_sha256=evidence.hash_quote(quote),
                annotations={"tags": ["benchmark"]},
                status=models.EvidenceStatusEnum.CANDIDATE,
                captured_at=now,
            )
        )
    db.bulk_save_objects(rows)
    db.commit()

    timings = []
    for _ in range(args.runs):
        started = time.perf_counter()
        pack = evidence.get_context_pack(db, task.id, "Chrome Prompt API privacy")
        timings.append((time.perf_counter() - started) * 1_000)
    timings.sort()
    p95 = timings[max(0, int(len(timings) * 0.95) - 1)]
    passed = p95 <= args.max_p95_ms
    print(
        f"clips={args.clips} runs={args.runs} p95_ms={p95:.3f} "
        f"threshold_ms={args.max_p95_ms:.3f} results={len(pack['items'])} pass={str(passed).lower()}"
    )
    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
