"""SQLite manifest for resumable corpus construction."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

STATUSES = ("pending", "fetched", "failed_permanent", "failed_retry")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Manifest:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS papers (
                canonical_id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                relation_to_seed TEXT NOT NULL,
                title TEXT,
                authors_json TEXT,
                year INTEGER,
                venue TEXT,
                arxiv_id TEXT,
                doi TEXT,
                semantic_scholar_id TEXT,
                openalex_id TEXT,
                full_text_available INTEGER,
                fetch_method TEXT,
                source_url TEXT,
                fetch_timestamp TEXT,
                fetch_status TEXT,
                failure_reason TEXT,
                abstract TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS edges (
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                relation TEXT NOT NULL,
                PRIMARY KEY (source, target, relation)
            );

            CREATE TABLE IF NOT EXISTS jobs (
                key TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS run_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status);
            CREATE INDEX IF NOT EXISTS idx_papers_relation ON papers(relation_to_seed);
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def set_meta(self, key: str, value: Any) -> None:
        if value is None:
            value = ""
        elif not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        if value in ("null", "None"):
            value = ""
        self.conn.execute(
            "INSERT INTO run_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM run_meta WHERE key=?", (key,)
        ).fetchone()
        if not row:
            return None
        val = row["value"]
        if val in ("", "null", "None"):
            return None
        return val

    def set_job(self, key: str, status: str, payload: Any | None = None) -> None:
        self.conn.execute(
            "INSERT INTO jobs(key, status, payload_json, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET status=excluded.status, "
            "payload_json=excluded.payload_json, updated_at=excluded.updated_at",
            (key, status, json.dumps(payload, ensure_ascii=False) if payload is not None else None, utcnow()),
        )
        self.conn.commit()

    def get_job(self, key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE key=?", (key,)).fetchone()

    def upsert_paper(self, record: dict[str, Any]) -> None:
        now = utcnow()
        record = dict(record)
        record.setdefault("created_at", now)
        record["updated_at"] = now
        if isinstance(record.get("authors"), list):
            record["authors_json"] = json.dumps(record.pop("authors"), ensure_ascii=False)
        if "metadata" in record and isinstance(record["metadata"], dict):
            record["metadata_json"] = json.dumps(record.pop("metadata"), ensure_ascii=False)
        cols = [
            "canonical_id",
            "file_id",
            "status",
            "relation_to_seed",
            "title",
            "authors_json",
            "year",
            "venue",
            "arxiv_id",
            "doi",
            "semantic_scholar_id",
            "openalex_id",
            "full_text_available",
            "fetch_method",
            "source_url",
            "fetch_timestamp",
            "fetch_status",
            "failure_reason",
            "abstract",
            "metadata_json",
            "created_at",
            "updated_at",
        ]
        existing = self.get_paper(record["canonical_id"])
        if existing:
            # Preserve a more-specific relation: seed wins; otherwise keep existing
            # if the incoming relation would overwrite seed.
            if existing["relation_to_seed"] == "seed":
                record["relation_to_seed"] = "seed"
            record["created_at"] = existing["created_at"]
            # Do not clobber a terminal fetched status with pending.
            if existing["status"] == "fetched" and record.get("status") == "pending":
                record["status"] = "fetched"
            for col in cols:
                if col not in record or record[col] is None:
                    if col == "authors_json":
                        record[col] = existing["authors_json"]
                    elif col in existing.keys():
                        record[col] = existing[col]
        placeholders = ",".join("?" for _ in cols)
        colsql = ",".join(cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("canonical_id", "created_at"))
        values = [record.get(c) for c in cols]
        self.conn.execute(
            f"INSERT INTO papers({colsql}) VALUES({placeholders}) "
            f"ON CONFLICT(canonical_id) DO UPDATE SET {updates}, created_at=papers.created_at",
            values,
        )
        self.conn.commit()

    def get_paper(self, canonical_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM papers WHERE canonical_id=?", (canonical_id,)
        ).fetchone()

    def find_by_any_id(
        self,
        *,
        doi: str | None = None,
        arxiv_id: str | None = None,
        s2_id: str | None = None,
        openalex_id: str | None = None,
    ) -> sqlite3.Row | None:
        clauses = []
        args: list[str] = []
        if doi:
            clauses.append("doi=?")
            args.append(doi)
        if arxiv_id:
            clauses.append("arxiv_id=?")
            args.append(arxiv_id)
        if s2_id:
            clauses.append("semantic_scholar_id=?")
            args.append(s2_id)
        if openalex_id:
            clauses.append("openalex_id=?")
            args.append(openalex_id)
        if not clauses:
            return None
        return self.conn.execute(
            f"SELECT * FROM papers WHERE {' OR '.join(clauses)} LIMIT 1", args
        ).fetchone()

    def set_status(
        self,
        canonical_id: str,
        status: str,
        **fields: Any,
    ) -> None:
        if status not in STATUSES:
            raise ValueError(status)
        sets = ["status=?", "updated_at=?"]
        args: list[Any] = [status, utcnow()]
        for key, value in fields.items():
            sets.append(f"{key}=?")
            args.append(value)
        args.append(canonical_id)
        self.conn.execute(
            f"UPDATE papers SET {', '.join(sets)} WHERE canonical_id=?", args
        )
        self.conn.commit()

    def papers_needing_fetch(self) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM papers WHERE status IN ('pending', 'failed_retry') "
            "ORDER BY relation_to_seed, canonical_id"
        ).fetchall()
        return list(rows)

    def all_papers(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM papers ORDER BY relation_to_seed, canonical_id"))

    def add_edge(self, source: str, target: str, relation: str = "cites") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO edges(source, target, relation) VALUES(?,?,?)",
            (source, target, relation),
        )
        self.conn.commit()

    def all_edges(self) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT source, target, relation FROM edges ORDER BY source, target"
        ).fetchall()
        return [dict(r) for r in rows]

    def counts_by_relation(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT relation_to_seed, COUNT(*) AS n FROM papers GROUP BY relation_to_seed"
        ).fetchall()
        return {r["relation_to_seed"]: r["n"] for r in rows}

    def counts_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM papers GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def merge_identifiers(self, canonical_id: str, **ids: str | None) -> None:
        paper = self.get_paper(canonical_id)
        if not paper:
            return
        updates = {}
        for key in ("arxiv_id", "doi", "semantic_scholar_id", "openalex_id"):
            incoming = ids.get(key)
            if incoming and not paper[key]:
                updates[key] = incoming
        if updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            self.conn.execute(
                f"UPDATE papers SET {sets}, updated_at=? WHERE canonical_id=?",
                [*updates.values(), utcnow(), canonical_id],
            )
            self.conn.commit()
