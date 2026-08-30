#!/usr/bin/env python3
"""Markdown evidence table from confirmed claims.

Reads a citehop.claims.v1 JSON export, or a project extraction.db (read-only).
Does not extract or review. Default filter: human_confirmed.

The desktop Review tab exports the same table without this script.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from citehop.claims.evidence import to_markdown  # noqa: E402


def rows_from_payload(payload: dict, verification: str | None) -> list[dict]:
    claims = payload.get("claims") or []
    out = []
    for rec in claims:
        if verification and rec.get("verification_status") != verification:
            continue
        out.append(rec)
    return out


def rows_from_db(db_path: Path, verification: str | None) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute(
            "SELECT run_id FROM runs ORDER BY started_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
        if not run:
            return []
        sql = "SELECT * FROM claims WHERE run_id=?"
        args: list[object] = [run["run_id"]]
        if verification:
            sql += " AND verification_status=?"
            args.append(verification)
        sql += " ORDER BY paper_canonical_id, source_start, claim_id"
        rows = []
        for row in conn.execute(sql, args):
            fields = row["structured_fields_json"]
            try:
                fields = json.loads(fields) if fields else {}
            except json.JSONDecodeError:
                fields = {}
            rows.append(
                {
                    "paper_canonical_id": row["paper_canonical_id"],
                    "claim_type": row["claim_type"],
                    "structured_fields": fields,
                    "quoted_source_span": row["quoted_source_span"],
                    "verification_status": row["verification_status"],
                    "claim_text": row["claim_text"],
                }
            )
        return rows
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="citehop.claims.v1 export file")
    parser.add_argument("--project", type=Path, help="Project directory (extraction.db)")
    parser.add_argument(
        "--verification",
        default="human_confirmed",
        help="Filter; empty string means every claim. Default: human_confirmed",
    )
    parser.add_argument("--out", type=Path, help="Write markdown here (default: stdout)")
    args = parser.parse_args(argv)
    verification = args.verification if args.verification else None
    if args.json:
        payload = json.loads(args.json.expanduser().read_text(encoding="utf-8"))
        claims = rows_from_payload(payload, verification)
    elif args.project:
        db = args.project.expanduser() / "extraction.db"
        claims = rows_from_db(db, verification)
    else:
        raise SystemExit("Pass --json <export.json> or --project <project-dir>")
    md = to_markdown(claims)
    if args.out:
        args.out.expanduser().write_text(md, encoding="utf-8")
        print(args.out)
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
