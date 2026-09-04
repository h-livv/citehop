"""Per-project SQLite store for extraction runs and claim records."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from citehop.sqliteutil import configure_connection
from citehop.store import utcnow

RUN_STATUSES = ("idle", "running", "paused", "completed", "failed")
PAPER_STATUSES = ("pending", "extracting", "done", "skipped_no_text", "error")
VERIFICATION = (
    "unverified_by_human",
    "human_confirmed",
    "human_rejected",
    "human_edited",
)
AGREEMENT = ("match", "partial_match", "disagreement", "single_pass_only")
CONFIDENCE = ("high", "medium", "low")
REVIEW_PRIORITY = {"disagreement": 0, "single_pass_only": 1, "partial_match": 2, "match": 3}

# Extracting rows older than this are treated as abandoned (crash / killed process).
DEFAULT_EXTRACT_LEASE_SECONDS = 600.0


def extract_lease_seconds() -> float:
    raw = (os.environ.get("CITEHOP_EXTRACT_LEASE_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_EXTRACT_LEASE_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_EXTRACT_LEASE_SECONDS


class ClaimStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit: multi-statement work uses explicit BEGIN IMMEDIATE.
        self.conn = sqlite3.connect(str(self.db_path), timeout=30.0, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        configure_connection(self.conn, self.db_path)
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL,
                token_budget INTEGER NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                time_budget_seconds INTEGER,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT,
                papers_total INTEGER NOT NULL DEFAULT 0,
                papers_done INTEGER NOT NULL DEFAULT 0,
                papers_skipped INTEGER NOT NULL DEFAULT 0,
                pause_requested INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS run_papers (
                run_id TEXT NOT NULL,
                paper_canonical_id TEXT NOT NULL,
                file_id TEXT,
                status TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, paper_canonical_id)
            );

            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                paper_canonical_id TEXT NOT NULL,
                claim_type TEXT NOT NULL,
                claim_text TEXT NOT NULL,
                structured_fields_json TEXT NOT NULL,
                quoted_source_span TEXT NOT NULL,
                source_start INTEGER NOT NULL,
                source_end INTEGER NOT NULL,
                confidence_self_reported TEXT NOT NULL,
                present_in_pass_a INTEGER NOT NULL,
                present_in_pass_b INTEGER NOT NULL,
                agreement TEXT NOT NULL,
                disagreement_notes TEXT,
                verification_status TEXT NOT NULL,
                human_edit_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                paper_title TEXT,
                doi TEXT,
                arxiv_id TEXT,
                year INTEGER,
                venue TEXT,
                full_text_used TEXT,
                prompt_start INTEGER,
                prompt_end INTEGER,
                schema_id TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_claims_project ON claims(project_id, run_id);
            CREATE INDEX IF NOT EXISTS idx_claims_paper ON claims(paper_canonical_id);
            CREATE INDEX IF NOT EXISTS idx_claims_type ON claims(claim_type);
            CREATE INDEX IF NOT EXISTS idx_claims_agree ON claims(agreement);
            CREATE INDEX IF NOT EXISTS idx_claims_verify ON claims(verification_status);
            CREATE INDEX IF NOT EXISTS idx_run_papers_status ON run_papers(run_id, status);
            """
        )
        self._migrate_run_identity()
        self._migrate_claims_provenance()

    def _migrate_run_identity(self) -> None:
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(runs)")}
        for name in ("llm_backend", "llm_model", "schema_id"):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE runs ADD COLUMN {name} TEXT")

    def _migrate_claims_provenance(self) -> None:
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(claims)")}
        for name, decl in (
            ("paper_title", "TEXT"),
            ("doi", "TEXT"),
            ("arxiv_id", "TEXT"),
            ("year", "INTEGER"),
            ("venue", "TEXT"),
            ("full_text_used", "TEXT"),
            ("prompt_start", "INTEGER"),
            ("prompt_end", "INTEGER"),
            ("schema_id", "TEXT"),
        ):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE claims ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self.conn.close()

    def _begin(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self.conn.execute("COMMIT")

    def _rollback(self) -> None:
        try:
            self.conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def latest_run(self, project_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM runs WHERE project_id=? ORDER BY started_at DESC, run_id DESC LIMIT 1",
            (project_id,),
        ).fetchone()

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()

    def create_run(
        self,
        project_id: str,
        papers: list[dict[str, str]],
        token_budget: int,
        time_budget_seconds: int | None = None,
        *,
        llm_backend: str | None = None,
        llm_model: str | None = None,
        schema_id: str | None = None,
    ) -> str:
        run_id = uuid.uuid4().hex
        now = utcnow()
        self._begin()
        try:
            self.conn.execute(
                "INSERT INTO runs(run_id, project_id, status, token_budget, tokens_used, "
                "time_budget_seconds, started_at, updated_at, papers_total, pause_requested, "
                "llm_backend, llm_model, schema_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?)",
                (
                    run_id,
                    project_id,
                    "running",
                    int(token_budget),
                    0,
                    time_budget_seconds,
                    now,
                    now,
                    len(papers),
                    llm_backend,
                    llm_model,
                    schema_id,
                ),
            )
            for paper in papers:
                self.conn.execute(
                    "INSERT INTO run_papers(run_id, paper_canonical_id, file_id, status, updated_at) "
                    "VALUES(?,?,?,?,?)",
                    (
                        run_id,
                        paper["canonical_id"],
                        paper.get("file_id"),
                        "pending",
                        now,
                    ),
                )
            self._commit()
        except Exception:
            self._rollback()
            raise
        return run_id

    def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None:
        now = utcnow()
        finished = now if status in ("completed", "failed") else None
        self.conn.execute(
            "UPDATE runs SET status=?, error=?, updated_at=?, finished_at=COALESCE(?, finished_at), "
            "pause_requested=CASE WHEN ? IN ('running','completed','failed') THEN 0 ELSE pause_requested END "
            "WHERE run_id=?",
            (status, error, now, finished, status, run_id),
        )

    def request_pause(self, run_id: str) -> None:
        self.conn.execute(
            "UPDATE runs SET pause_requested=1, updated_at=? WHERE run_id=?",
            (utcnow(), run_id),
        )

    def pause_requested(self, run_id: str) -> bool:
        row = self.get_run(run_id)
        return bool(row and row["pause_requested"])

    def add_tokens(self, run_id: str, n: int) -> int:
        self.conn.execute(
            "UPDATE runs SET tokens_used=tokens_used+?, updated_at=? WHERE run_id=?",
            (int(n), utcnow(), run_id),
        )
        row = self.get_run(run_id)
        return int(row["tokens_used"]) if row else 0

    def next_pending_paper(self, run_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM run_papers WHERE run_id=? AND status='pending' "
            "ORDER BY paper_canonical_id LIMIT 1",
            (run_id,),
        ).fetchone()

    def count_papers(self, run_id: str, status: str) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM run_papers WHERE run_id=? AND status=?",
                (run_id, status),
            ).fetchone()["n"]
        )

    def paper_status(self, run_id: str, paper_canonical_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM run_papers WHERE run_id=? AND paper_canonical_id=?",
            (run_id, paper_canonical_id),
        ).fetchone()
        return row["status"] if row else None

    def claim_next_paper(self, run_id: str) -> sqlite3.Row | None:
        """Atomically take one pending paper (pending → extracting)."""
        while True:
            self._begin()
            try:
                row = self.conn.execute(
                    "SELECT * FROM run_papers WHERE run_id=? AND status='pending' "
                    "ORDER BY paper_canonical_id LIMIT 1",
                    (run_id,),
                ).fetchone()
                if row is None:
                    self._commit()
                    return None
                cid = row["paper_canonical_id"]
                snapshot = dict(row)
                cur = self.conn.execute(
                    "UPDATE run_papers SET status='extracting', updated_at=? "
                    "WHERE run_id=? AND paper_canonical_id=? AND status='pending'",
                    (utcnow(), run_id, cid),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
            if cur.rowcount != 1:
                continue
            return snapshot  # type: ignore[return-value]

    def requeue_extracting(self, run_id: str) -> int:
        """Force every extracting paper back to pending (resume after crash / pause)."""
        now = utcnow()
        cur = self.conn.execute(
            "UPDATE run_papers SET status='pending', updated_at=? "
            "WHERE run_id=? AND status='extracting'",
            (now, run_id),
        )
        return int(cur.rowcount or 0)

    def requeue_stale_extracting(self, run_id: str, lease_seconds: float | None = None) -> int:
        """Requeue extracting papers whose lease has expired (abandoned worker)."""
        lease = DEFAULT_EXTRACT_LEASE_SECONDS if lease_seconds is None else lease_seconds
        rows = self.conn.execute(
            "SELECT paper_canonical_id, updated_at FROM run_papers "
            "WHERE run_id=? AND status='extracting'",
            (run_id,),
        ).fetchall()
        n = 0
        now_ts = datetime.now(timezone.utc).timestamp()
        cutoff = now_ts - float(lease)
        now = utcnow()
        for row in rows:
            try:
                t = datetime.fromisoformat(row["updated_at"])
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                t = datetime.fromtimestamp(0, tz=timezone.utc)
            if t.timestamp() <= cutoff:
                cur = self.conn.execute(
                    "UPDATE run_papers SET status='pending', updated_at=? "
                    "WHERE run_id=? AND paper_canonical_id=? AND status='extracting'",
                    (now, run_id, row["paper_canonical_id"]),
                )
                n += int(cur.rowcount or 0)
        return n

    def requeue_retryable_errors(self, run_id: str) -> int:
        """Pending-again papers that failed because the engine was not ready."""
        from citehop.claims.llm import retryable_backend_message

        rows = self.conn.execute(
            "SELECT paper_canonical_id, error FROM run_papers "
            "WHERE run_id=? AND status='error'",
            (run_id,),
        ).fetchall()
        n = 0
        now = utcnow()
        for row in rows:
            if not retryable_backend_message(row["error"] or ""):
                continue
            cur = self.conn.execute(
                "UPDATE run_papers SET status='pending', error=NULL, updated_at=? "
                "WHERE run_id=? AND paper_canonical_id=? AND status='error'",
                (now, run_id, row["paper_canonical_id"]),
            )
            n += int(cur.rowcount or 0)
        if n:
            self._refresh_run_paper_counts(run_id)
        return n

    def requeue_skipped(self, run_id: str, canonical_ids: list[str]) -> int:
        """Retry skips after full text showed up on disk."""
        n = 0
        now = utcnow()
        for cid in canonical_ids:
            cur = self.conn.execute(
                "UPDATE run_papers SET status='pending', error=NULL, updated_at=? "
                "WHERE run_id=? AND paper_canonical_id=? AND status='skipped_no_text'",
                (now, run_id, cid),
            )
            n += int(cur.rowcount or 0)
        if n:
            self._refresh_run_paper_counts(run_id)
        return n

    def done_paper_rows(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT paper_canonical_id, file_id, updated_at FROM run_papers "
                "WHERE run_id=? AND status='done'",
                (run_id,),
            )
        )

    def requeue_done_stale_text(self, run_id: str, canonical_ids: list[str]) -> int:
        """Return done papers to pending and drop this run's claims for them.

        Used when stored text is newer than the extract (abstract then PDF).
        """
        if not canonical_ids:
            return 0
        now = utcnow()
        claim_ids: list[str] = []
        n = 0
        self._begin()
        try:
            for cid in canonical_ids:
                rows = self.conn.execute(
                    "SELECT claim_id FROM claims WHERE run_id=? AND paper_canonical_id=?",
                    (run_id, cid),
                ).fetchall()
                claim_ids.extend(r["claim_id"] for r in rows)
                self.conn.execute(
                    "DELETE FROM claims WHERE run_id=? AND paper_canonical_id=?",
                    (run_id, cid),
                )
                cur = self.conn.execute(
                    "UPDATE run_papers SET status='pending', error=NULL, tokens_used=0, "
                    "updated_at=? WHERE run_id=? AND paper_canonical_id=? AND status='done'",
                    (now, run_id, cid),
                )
                n += int(cur.rowcount or 0)
            if n:
                self._refresh_run_paper_counts(run_id)
            self._commit()
        except Exception:
            self._rollback()
            raise
        if claim_ids:
            from .files import claim_file_path, write_claims_index

            folder = self.db_path.parent
            for cid in claim_ids:
                path = claim_file_path(folder, cid)
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            write_claims_index(folder)
        return n

    def papers_with_status(self, run_id: str, status: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT paper_canonical_id FROM run_papers WHERE run_id=? AND status=?",
            (run_id, status),
        ).fetchall()
        return [r["paper_canonical_id"] for r in rows]

    def release_paper(self, run_id: str, paper_canonical_id: str) -> None:
        """Return an in-flight paper to pending (backend died; do not record an error)."""
        self.conn.execute(
            "UPDATE run_papers SET status='pending', error=NULL, updated_at=? "
            "WHERE run_id=? AND paper_canonical_id=? AND status='extracting'",
            (utcnow(), run_id, paper_canonical_id),
        )

    def mark_paper(
        self,
        run_id: str,
        paper_canonical_id: str,
        status: str,
        tokens_used: int = 0,
        error: str | None = None,
    ) -> None:
        self._begin()
        try:
            self._mark_paper_uncommitted(
                run_id, paper_canonical_id, status, tokens_used=tokens_used, error=error
            )
            self._commit()
        except Exception:
            self._rollback()
            raise

    def _mark_paper_uncommitted(
        self,
        run_id: str,
        paper_canonical_id: str,
        status: str,
        tokens_used: int = 0,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE run_papers SET status=?, tokens_used=?, error=?, updated_at=? "
            "WHERE run_id=? AND paper_canonical_id=?",
            (status, tokens_used, error, utcnow(), run_id, paper_canonical_id),
        )
        self._refresh_run_paper_counts(run_id)

    def _refresh_run_paper_counts(self, run_id: str) -> None:
        done = self.conn.execute(
            "SELECT COUNT(*) AS n FROM run_papers WHERE run_id=? AND status='done'",
            (run_id,),
        ).fetchone()["n"]
        skipped = self.conn.execute(
            "SELECT COUNT(*) AS n FROM run_papers WHERE run_id=? AND status IN "
            "('skipped_no_text','error')",
            (run_id,),
        ).fetchone()["n"]
        self.conn.execute(
            "UPDATE runs SET papers_done=?, papers_skipped=?, updated_at=? WHERE run_id=?",
            (done, skipped, utcnow(), run_id),
        )

    def insert_claims(self, records: list[dict[str, Any]]) -> None:
        self._begin()
        try:
            self._insert_claims_uncommitted(records)
            self._commit()
        except Exception:
            self._rollback()
            raise
        self._persist_claim_files(records)

    def complete_paper(
        self,
        run_id: str,
        paper_canonical_id: str,
        *,
        claims: list[dict[str, Any]] | None = None,
        tokens_used: int = 0,
        status: str = "done",
        error: str | None = None,
        add_tokens: int = 0,
    ) -> None:
        """Write claims (if any) and the paper status in one transaction.

        A crash here rolls back both sides: no 'done' paper with missing claims,
        and no claims attached to a paper that is still pending.
        """
        self._begin()
        try:
            if add_tokens:
                self.conn.execute(
                    "UPDATE runs SET tokens_used=tokens_used+?, updated_at=? WHERE run_id=?",
                    (int(add_tokens), utcnow(), run_id),
                )
            if claims:
                self._insert_claims_uncommitted(claims)
            self._mark_paper_uncommitted(
                run_id, paper_canonical_id, status, tokens_used=tokens_used, error=error
            )
            self._commit()
        except Exception:
            self._rollback()
            raise
        if claims:
            self._persist_claim_files(claims)

    def _persist_claim_files(self, records: list[dict[str, Any]]) -> None:
        from .files import write_claim_files

        write_claim_files(self.db_path.parent, records)

    def _insert_claims_uncommitted(self, records: list[dict[str, Any]]) -> None:
        now = utcnow()
        for rec in records:
            offset = rec["source_char_offset"]
            prompt = rec.get("prompt_char_range")
            prompt_start = prompt_end = None
            if isinstance(prompt, (list, tuple)) and len(prompt) >= 2:
                if prompt[0] is not None and prompt[1] is not None:
                    prompt_start, prompt_end = int(prompt[0]), int(prompt[1])
            year = rec.get("year")
            if year is not None:
                year = int(year)
            self.conn.execute(
                "INSERT INTO claims("
                "claim_id, project_id, run_id, paper_canonical_id, claim_type, claim_text, "
                "structured_fields_json, quoted_source_span, source_start, source_end, "
                "confidence_self_reported, present_in_pass_a, present_in_pass_b, agreement, "
                "disagreement_notes, verification_status, human_edit_json, created_at, updated_at, "
                "paper_title, doi, arxiv_id, year, venue, full_text_used, prompt_start, prompt_end, "
                "schema_id"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec["claim_id"],
                    rec["project_id"],
                    rec["run_id"],
                    rec["paper_canonical_id"],
                    rec["claim_type"],
                    rec["claim_text"],
                    json.dumps(rec.get("structured_fields") or {}, ensure_ascii=False),
                    rec["quoted_source_span"],
                    int(offset[0]),
                    int(offset[1]),
                    rec["confidence_self_reported"],
                    1 if rec.get("present_in_pass_a") else 0,
                    1 if rec.get("present_in_pass_b") else 0,
                    rec["agreement"],
                    rec.get("disagreement_notes"),
                    rec.get("verification_status") or "unverified_by_human",
                    json.dumps(rec["human_edit"], ensure_ascii=False)
                    if rec.get("human_edit")
                    else None,
                    now,
                    now,
                    rec.get("paper_title"),
                    rec.get("doi"),
                    rec.get("arxiv_id"),
                    year,
                    rec.get("venue"),
                    rec.get("full_text_used"),
                    prompt_start,
                    prompt_end,
                    rec.get("schema_id"),
                ),
            )

    def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        return row_to_claim(row) if row else None

    def claim_ids_in_run(self, run_id: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT claim_id FROM claims WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return {r["claim_id"] for r in rows}

    def existing_claim_ids(self, claim_ids: list[str]) -> set[str]:
        ids = [cid for cid in claim_ids if cid]
        if not ids:
            return set()
        found: set[str] = set()
        for i in range(0, len(ids), 400):
            chunk = ids[i : i + 400]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT claim_id FROM claims WHERE claim_id IN ({placeholders})",
                chunk,
            ).fetchall()
            found.update(r["claim_id"] for r in rows)
        return found

    def referenced_type_ids(self, project_id: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT claim_type FROM claims WHERE project_id=?",
            (project_id,),
        ).fetchall()
        return {r["claim_type"] for r in rows}

    def query_claims(
        self,
        project_id: str,
        *,
        run_id: str | None = None,
        claim_type: str | None = None,
        agreement: str | None = None,
        verification_status: str | None = None,
        paper_canonical_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM claims WHERE project_id=?"
        args: list[Any] = [project_id]
        if run_id:
            sql += " AND run_id=?"
            args.append(run_id)
        if claim_type:
            sql += " AND claim_type=?"
            args.append(claim_type)
        if agreement:
            sql += " AND agreement=?"
            args.append(agreement)
        if verification_status:
            sql += " AND verification_status=?"
            args.append(verification_status)
        if paper_canonical_id:
            sql += " AND paper_canonical_id=?"
            args.append(paper_canonical_id)
        sql += " ORDER BY paper_canonical_id, source_start, claim_id"
        rows = self.conn.execute(sql, args).fetchall()
        claims = [row_to_claim(r) for r in rows]
        claims.sort(
            key=lambda c: (
                REVIEW_PRIORITY.get(c["agreement"], 9),
                c["paper_canonical_id"],
                c["source_char_offset"][0],
            )
        )
        return claims

    def apply_review(
        self,
        claim_id: str,
        verification_status: str,
        human_edit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if verification_status not in VERIFICATION:
            raise ValueError(f"Invalid verification_status {verification_status!r}")
        existing = self.get_claim(claim_id)
        if not existing:
            raise KeyError(claim_id)
        self._begin()
        try:
            if verification_status == "human_edited":
                if not human_edit:
                    raise ValueError("human_edited requires human_edit payload")
                original = existing.get("human_edit") or {
                    "claim_text": existing["claim_text"],
                    "structured_fields": existing["structured_fields"],
                    "claim_type": existing["claim_type"],
                    "quoted_source_span": existing["quoted_source_span"],
                }
                if existing.get("human_edit") and "original" in existing["human_edit"]:
                    original = existing["human_edit"]["original"]
                payload = {"original": original, "edited": human_edit}
                edit_json = json.dumps(payload, ensure_ascii=False)
                claim_text = human_edit.get("claim_text", existing["claim_text"])
                fields = human_edit.get("structured_fields", existing["structured_fields"])
                claim_type = human_edit.get("claim_type", existing["claim_type"])
                self.conn.execute(
                    "UPDATE claims SET verification_status=?, human_edit_json=?, claim_text=?, "
                    "structured_fields_json=?, claim_type=?, updated_at=? WHERE claim_id=?",
                    (
                        verification_status,
                        edit_json,
                        claim_text,
                        json.dumps(fields, ensure_ascii=False),
                        claim_type,
                        utcnow(),
                        claim_id,
                    ),
                )
            else:
                self.conn.execute(
                    "UPDATE claims SET verification_status=?, updated_at=? WHERE claim_id=?",
                    (verification_status, utcnow(), claim_id),
                )
            self._commit()
        except Exception:
            self._rollback()
            raise
        updated = self.get_claim(claim_id)
        assert updated is not None
        self._persist_claim_files([updated])
        return updated

    def run_status_dict(self, run_id: str) -> dict[str, Any] | None:
        row = self.get_run(run_id)
        if not row:
            return None
        pending = self.conn.execute(
            "SELECT COUNT(*) AS n FROM run_papers WHERE run_id=? AND status='pending'",
            (run_id,),
        ).fetchone()["n"]
        extracting = self.conn.execute(
            "SELECT COUNT(*) AS n FROM run_papers WHERE run_id=? AND status='extracting'",
            (run_id,),
        ).fetchone()["n"]
        return {
            "run_id": row["run_id"],
            "project_id": row["project_id"],
            "status": row["status"],
            "token_budget": row["token_budget"],
            "tokens_used": row["tokens_used"],
            "time_budget_seconds": row["time_budget_seconds"],
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "finished_at": row["finished_at"],
            "error": row["error"],
            "papers_total": row["papers_total"],
            "papers_done": row["papers_done"],
            "papers_skipped": row["papers_skipped"],
            "papers_pending": pending,
            "papers_extracting": extracting,
            "pause_requested": bool(row["pause_requested"]),
            "llm_backend": row["llm_backend"] if "llm_backend" in row.keys() else None,
            "llm_model": row["llm_model"] if "llm_model" in row.keys() else None,
            "schema_id": row["schema_id"] if "schema_id" in row.keys() else None,
            "claims_count": self.conn.execute(
                "SELECT COUNT(*) AS n FROM claims WHERE run_id=?", (run_id,)
            ).fetchone()["n"],
            "last_paper_error": self._last_paper_error(run_id),
        }

    def _last_paper_error(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT paper_canonical_id, error, updated_at FROM run_papers "
            "WHERE run_id=? AND status='error' AND error IS NOT NULL "
            "ORDER BY updated_at DESC, paper_canonical_id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        from citehop.claims.llm import retryable_backend_message

        message = row["error"] or ""
        return {
            "paper_canonical_id": row["paper_canonical_id"],
            "error": message,
            "retryable": retryable_backend_message(message),
            "updated_at": row["updated_at"],
        }


def _row_val(row: sqlite3.Row, name: str, default: Any = None) -> Any:
    try:
        keys = row.keys()
    except Exception:
        return default
    if name not in keys:
        return default
    val = row[name]
    return default if val is None else val


def row_to_claim(row: sqlite3.Row) -> dict[str, Any]:
    edit_raw = row["human_edit_json"]
    human_edit = json.loads(edit_raw) if edit_raw else None
    prompt_start = _row_val(row, "prompt_start")
    prompt_end = _row_val(row, "prompt_end")
    if prompt_start is not None and prompt_end is not None:
        prompt_char_range: list[int] | None = [int(prompt_start), int(prompt_end)]
    else:
        prompt_char_range = None
    return {
        "claim_id": row["claim_id"],
        "project_id": row["project_id"],
        "run_id": row["run_id"],
        "paper_canonical_id": row["paper_canonical_id"],
        "claim_type": row["claim_type"],
        "claim_text": row["claim_text"],
        "structured_fields": json.loads(row["structured_fields_json"] or "{}"),
        "quoted_source_span": row["quoted_source_span"],
        "source_char_offset": [row["source_start"], row["source_end"]],
        "confidence_self_reported": row["confidence_self_reported"],
        "present_in_pass_a": bool(row["present_in_pass_a"]),
        "present_in_pass_b": bool(row["present_in_pass_b"]),
        "agreement": row["agreement"],
        "disagreement_notes": row["disagreement_notes"],
        "verification_status": row["verification_status"],
        "human_edit": human_edit,
        "paper_title": _row_val(row, "paper_title"),
        "doi": _row_val(row, "doi"),
        "arxiv_id": _row_val(row, "arxiv_id"),
        "year": _row_val(row, "year"),
        "venue": _row_val(row, "venue"),
        "full_text_used": _row_val(row, "full_text_used"),
        "prompt_char_range": prompt_char_range,
        "schema_id": _row_val(row, "schema_id"),
    }
