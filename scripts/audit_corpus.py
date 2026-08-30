#!/usr/bin/env python3
"""Read-only 1-hop completeness / duplicate-id check on a corpus manifest.

Does not write. Does not ingest, merge, or fetch.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _open_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SystemExit(f"No sqlite file at {path}")
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _meta(conn: sqlite3.Connection) -> dict[str, str]:
    out = {}
    for row in conn.execute("SELECT key, value FROM run_meta"):
        val = row["value"]
        if val in ("", "null", "None", None):
            continue
        out[row["key"]] = val
    return out


def _dupes(conn: sqlite3.Connection, column: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT {column} AS id, GROUP_CONCAT(canonical_id) AS cids, COUNT(*) AS n "
        f"FROM papers WHERE {column} IS NOT NULL AND TRIM({column}) != '' "
        f"GROUP BY {column} HAVING n > 1"
    ).fetchall()
    return [{"id": r["id"], "n": r["n"], "canonical_ids": (r["cids"] or "").split(",")} for r in rows]


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        raise SystemExit("usage: audit_corpus.py <corpus-dir>")
    corpus = Path(args[0]).expanduser()
    db = corpus / "manifest.db"
    conn = _open_ro(db)
    try:
        rel = {
            r["relation_to_seed"]: r["n"]
            for r in conn.execute(
                "SELECT relation_to_seed, COUNT(*) AS n FROM papers GROUP BY relation_to_seed"
            )
        }
        status = {
            r["status"]: r["n"]
            for r in conn.execute("SELECT status, COUNT(*) AS n FROM papers GROUP BY status")
        }
        meta = _meta(conn)
        reported = {
            "s2_reference_count_reported": meta.get("s2_reference_count_reported"),
            "s2_citation_count_reported": meta.get("s2_citation_count_reported"),
            "openalex_referenced_works_count": meta.get("openalex_referenced_works_count"),
            "openalex_cited_by_count": meta.get("openalex_cited_by_count"),
        }
        duplicates = {
            col: _dupes(conn, col)
            for col in ("doi", "arxiv_id", "semantic_scholar_id", "openalex_id")
        }
    finally:
        conn.close()

    conflicts = corpus / "merge_conflicts.jsonl"
    conflict_lines = 0
    if conflicts.is_file():
        conflict_lines = sum(1 for line in conflicts.read_text(encoding="utf-8").splitlines() if line.strip())

    print(json.dumps(
        {
            "corpus": str(corpus),
            "paper_count": sum(rel.values()),
            "relation_counts": rel,
            "status_counts": status,
            "provider_counts_at_seed_resolve": reported,
            "ingested_vs_reported": {
                "backward_reference": rel.get("backward_reference"),
                "s2_reference_count_reported": reported["s2_reference_count_reported"],
                "forward_citation": rel.get("forward_citation"),
                "s2_citation_count_reported": reported["s2_citation_count_reported"],
                "openalex_referenced_works_count": reported["openalex_referenced_works_count"],
                "openalex_cited_by_count": reported["openalex_cited_by_count"],
            },
            "duplicate_identifiers": {k: v for k, v in duplicates.items() if v},
            "merge_conflicts_jsonl_lines": conflict_lines,
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
