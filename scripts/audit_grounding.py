#!/usr/bin/env python3
"""Read-only: how stored claims sit in corpus text (exact vs old 80-char prefix).

Does not write. Does not call extract. Existing extraction.db rows are left as-is.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from citehop.claims.engine import load_paper_text  # noqa: E402
from citehop.claims.locate import locate_span  # noqa: E402
from citehop.ids import file_id  # noqa: E402

_WS = re.compile(r"\s+")


def _open_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SystemExit(f"No sqlite file at {path}")
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _old_prefix_hit(stored_text: str, quote: str) -> bool:
    stripped = (quote or "").strip()
    if len(stripped) <= 40 or not stored_text:
        return False
    head = stripped[:80]
    return stored_text.find(head) >= 0


def _kind(stored_text: str | None, quote: str) -> str:
    if not stored_text:
        return "no_text"
    if locate_span(stored_text, quote) is not None:
        return "current_locate"
    if _old_prefix_hit(stored_text, quote):
        return "prefix80_only"
    return "unmatched"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True, help="Corpus directory (manifest.db + text/)")
    parser.add_argument("--project", type=Path, required=True, help="Project directory (extraction.db)")
    args = parser.parse_args(argv)

    corpus = args.corpus.expanduser()
    project = args.project.expanduser()
    claims_db = project / "extraction.db"
    manifest_db = corpus / "manifest.db"

    claims_conn = _open_ro(claims_db)
    try:
        run = claims_conn.execute(
            "SELECT run_id, status FROM runs ORDER BY started_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
        if not run:
            raise SystemExit(f"No runs in {claims_db}")
        run_id = run["run_id"]
        rows = list(
            claims_conn.execute(
                "SELECT claim_id, paper_canonical_id, quoted_source_span, source_start, source_end "
                "FROM claims WHERE run_id=? ORDER BY paper_canonical_id, source_start",
                (run_id,),
            )
        )
    finally:
        claims_conn.close()

    papers: dict[str, dict] = {}
    if manifest_db.is_file():
        man = _open_ro(manifest_db)
        try:
            for p in man.execute("SELECT canonical_id, file_id, abstract FROM papers"):
                papers[p["canonical_id"]] = {
                    "canonical_id": p["canonical_id"],
                    "file_id": p["file_id"],
                    "abstract": p["abstract"],
                }
        finally:
            man.close()

    counts: Counter[str] = Counter()
    span80: list[str] = []
    flagged: list[tuple[str, str, str]] = []
    text_cache: dict[str, str | None] = {}

    for row in rows:
        cid = row["paper_canonical_id"]
        if cid not in text_cache:
            paper = papers.get(cid) or {"canonical_id": cid, "file_id": file_id(cid)}
            text_cache[cid] = load_paper_text(corpus, paper)
        quote = row["quoted_source_span"] or ""
        kind = _kind(text_cache[cid], quote)
        counts[kind] += 1
        if kind in ("prefix80_only", "unmatched", "no_text"):
            flagged.append((kind, row["claim_id"], cid))
        if kind == "current_locate" and len(quote) == 80:
            span80.append(row["claim_id"])
            counts["span_exactly_80"] += 1

    print(json.dumps(
        {
            "run_id": run_id,
            "run_status": run["status"],
            "claims": len(rows),
            "counts": dict(counts),
            "note": (
                "prefix80_only: current locate_span misses, old head[:80] would hit. "
                "span_exactly_80: quote length 80 under current locate "
                "(historical prefix rewrite fingerprint; not proof)."
            ),
        },
        indent=2,
    ))
    if flagged:
        print("flagged:")
        for kind, claim_id, paper in flagged:
            print(f"  {kind}\t{claim_id[:12]}\t{paper}")
    if span80:
        print("span_exactly_80:")
        for claim_id in span80:
            print(f"  {claim_id[:12]}")


if __name__ == "__main__":
    main()
