#!/usr/bin/env python3
"""Lightweight guard against unsourced regulatory / compliance claims in docs.

Scans the tracked Markdown files and fails when a line matches one of the
PATTERNS below. It judges wording, not accuracy: the phrases listed here are
the ones that reintroduced overstated claims before (see
docs/regulatory-positioning.md), so a match means "stop and source this", not
"this is wrong".

Escape hatches, for quotations and historical notes:

    <!-- claim-check:allow -->   on the line itself
    <!-- claim-check:off --> ... <!-- claim-check:on -->   around a block

Usage:
    python3 scripts/check_doc_claims.py            # scan the repository
    python3 scripts/check_doc_claims.py --selftest # check the patterns
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Each entry: (name, compiled regex). Keep patterns narrow; a false positive
# costs a marker, a broad pattern costs the check its credibility.
REGULATIONS = r"(?:EU AI Act|AI Act|GDPR|SOC ?2|ISO ?27001|HIPAA|21 CFR(?: Part 11)?|GxP|eIDAS)"
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "full-enforcement claim",
        re.compile(r"full enforcement|fully enforced|全面施行|完全施行", re.I),
    ),
    (
        "compliance/certification claim next to a regulation name",
        # A regulation name followed, within the same clause, by a compliance
        # or certification word. Clause boundaries stop the match so that
        # "... 21 CFR Part 11 is out of scope. AxonRelay is not compliant ..."
        # does not trip it.
        re.compile(
            REGULATIONS + r"[^.。|\n]{0,40}?(?:compliant|compliance|certified|certification|準拠|適合|認証取得|認定)",
            re.I,
        ),
    ),
    (
        "asserted regulatory conformity",
        re.compile(
            r"(?:compliant with|complies with|satisf(?:y|ies)|meets|fulfil?ls) (?:the )?(?:requirements of )?"
            + REGULATIONS,
            re.I,
        ),
    ),
    (
        "compliance-ready marketing phrase",
        re.compile(
            r"compliance[- ]ready|audit[- ]ready|regulatory[- ]grade (?:compliance|ready)|コンプライアンス対応済み|規制対応済み|監査対応済み",
            re.I,
        ),
    ),
]

ALLOW_LINE = "claim-check:allow"
BLOCK_OFF = "claim-check:off"
BLOCK_ON = "claim-check:on"

EXCLUDED_PREFIXES = ("tmp/", "node_modules/")
# The policy document necessarily spells out the phrases it forbids; it is
# reviewed by hand against its own checklist instead.
EXCLUDED_PATHS = ("docs/regulatory-positioning.md",)


def tracked_markdown() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md", "**/*.md"],
        cwd=REPO,
        check=True,
        capture_output=True,
    ).stdout
    files = sorted({p for p in out.decode().split("\0") if p})
    return [REPO / p for p in files if not p.startswith(EXCLUDED_PREFIXES) and p not in EXCLUDED_PATHS]


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """Return (line_number, pattern_name, excerpt) for every hit.

    Markdown prose is hard-wrapped, so lines are joined into paragraphs
    (runs of non-blank lines) before matching; the reported line number is
    the paragraph's first line. Table rows and list items are paragraphs of
    their own only if separated by blank lines, which is fine for a guard.
    """
    hits: list[tuple[int, str, str]] = []
    enabled = True
    paragraph: list[str] = []
    start = 0

    def flush() -> None:
        if not paragraph:
            return
        joined = " ".join(part.strip() for part in paragraph)
        if ALLOW_LINE not in joined:
            for name, pattern in PATTERNS:
                match = pattern.search(joined)
                if match:
                    lo = max(0, match.start() - 60)
                    hits.append((start, name, joined[lo : match.end() + 60]))
        paragraph.clear()

    for lineno, line in enumerate(text.splitlines(), start=1):
        if BLOCK_OFF in line:
            flush()
            enabled = False
            continue
        if BLOCK_ON in line:
            enabled = True
            continue
        if not enabled:
            continue
        if not line.strip():
            flush()
            continue
        if not paragraph:
            start = lineno
        paragraph.append(line)
    flush()
    return hits


def scan_repo() -> int:
    failures = 0
    for path in tracked_markdown():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, name, line in scan_text(text):
            failures += 1
            rel = path.relative_to(REPO)
            print(f"{rel}:{lineno}: {name}\n    ...{line.strip()[:160]}...")
    if failures:
        print(
            f"\n{failures} line(s) need a dated primary source or a rewrite; "
            "see docs/regulatory-positioning.md (Review checklist)."
        )
        return 1
    print("check_doc_claims: no unsourced regulatory claims found")
    return 0


def selftest() -> int:
    must_hit = [
        "high-risk obligations reach full enforcement on 2026-08-02",
        "高リスク義務は 2026-08-02 に全面施行",
        "AxonRelay is EU AI Act compliant",
        "AxonRelay satisfies the requirements of GDPR",
        "an audit-ready ledger for regulated teams",
        "AI Act に準拠した台帳",
        "the EU AI Act's high-risk obligations reach full\nenforcement on 2026-08-02, and their core asks",
    ]
    must_pass = [
        "規制グレード対応（電子署名 / タイムスタンプ局 / 21 CFR Part 11 / GxP）は対象外",
        "AxonRelay is not a compliance product.",
        "this transport has no caller authentication (呼び出し元認証がない)",
        "the general application date of the AI Act was 2026-08-02",
        "見出し: EU AI Act full enforcement <!-- claim-check:allow -->",
        "<!-- claim-check:off -->\nfull enforcement\n<!-- claim-check:on -->",
        "<!-- claim-check:off -->\n### heading full enforcement\nbody full enforcement\n<!-- claim-check:on -->\nsafe text",
        "It certifies nothing and makes nothing conform to the EU AI Act or any other regulation.",
        "何も認証せず、EU AI Act やその他の規制への対応を何ら提供しない。",
    ]
    ok = True
    for sample in must_hit:
        if not scan_text(sample):
            ok = False
            print(f"selftest: expected a hit, got none: {sample!r}")
    for sample in must_pass:
        if scan_text(sample):
            ok = False
            print(f"selftest: unexpected hit: {sample!r}")
    print("selftest:", "ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(scan_repo())
