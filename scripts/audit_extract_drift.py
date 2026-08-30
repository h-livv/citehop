#!/usr/bin/env python3
"""Read-only: corpus fetch/text vs latest extraction run.

Does not start, resume, or requeue. Prints drift only.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from citehop.claims.engine import load_paper_text  # noqa: E402
from citehop.ids import file_id  # noqa: E402


def _open_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SystemExit(f"No sqlite file at {path}")
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _text_mtime(corpus: Path, fid: str) -> datetime | None:
    path = corpus / "text" / f"{fid}.txt"
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args(argv)

    corpus = args.corpus.expanduser()
    project = args.project.expanduser()
    man = _open_ro(corpus / "manifest.db")
    try:
        papers = list(man.execute("SELECT canonical_id, file_id, abstract, status FROM papers"))
        fetch_status = {
            r["status"]: r["n"]
            for r in man.execute("SELECT status, COUNT(*) AS n FROM papers GROUP BY status")
        }
    finally:
        man.close()

    papers_by_id = {p["canonical_id"]: p for p in papers}
    ext = _open_ro(project / "extraction.db")
    try:
        run = ext.execute(
            "SELECT run_id, status FROM runs ORDER BY started_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
        if not run:
            raise SystemExit(f"No runs in {project / 'extraction.db'}")
        run_id = run["run_id"]
        run_papers = list(
            ext.execute(
                "SELECT paper_canonical_id, file_id, status, updated_at FROM run_papers WHERE run_id=?",
                (run_id,),
            )
        )
    finally:
        ext.close()

    in_run = {r["paper_canonical_id"] for r in run_papers}
    missing_from_run = [
        p["canonical_id"] for p in papers if p["canonical_id"] not in in_run
    ]

    skipped_now_have_text: list[str] = []
    done_text_newer: list[str] = []
    run_status_counts: dict[str, int] = {}
    for row in run_papers:
        st = row["status"]
        run_status_counts[st] = run_status_counts.get(st, 0) + 1
        cid = row["paper_canonical_id"]
        src = papers_by_id.get(cid)
        paper = {
            "canonical_id": cid,
            "file_id": (src["file_id"] if src else None) or row["file_id"] or file_id(cid),
            "abstract": src["abstract"] if src else None,
        }
        fid = paper["file_id"]
        if st == "skipped_no_text" and load_paper_text(corpus, paper):
            skipped_now_have_text.append(cid)
        if st == "done":
            mtime = _text_mtime(corpus, fid)
            done_at = _parse_iso(row["updated_at"])
            if mtime is not None and done_at is not None and mtime > done_at:
                done_text_newer.append(cid)

    fetch_open = {
        k: fetch_status.get(k, 0) for k in ("pending", "failed_retry") if fetch_status.get(k)
    }

    print(json.dumps(
        {
            "run_id": run_id,
            "run_status": run["status"],
            "corpus_papers": len(papers),
            "run_papers": len(run_papers),
            "run_paper_status": run_status_counts,
            "fetch_status": fetch_status,
            "corpus_not_in_run": {"n": len(missing_from_run), "ids": missing_from_run[:50]},
            "skipped_no_text_now_has_text": {
                "n": len(skipped_now_have_text),
                "ids": skipped_now_have_text[:50],
                "note": "citehop extract resume already requeues these",
            },
            "done_text_mtime_newer_than_run_paper": {
                "n": len(done_text_newer),
                "ids": done_text_newer[:50],
                "note": "possible abstract-then-PDF; resume does not re-extract done rows",
            },
            "fetch_still_open": fetch_open,
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
