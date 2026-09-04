"""One JSON file per claim under <project>/claims/. SQLite stays the source of truth.

External citehop.claim.v1 files are discovered here so list_claims can ingest
rows that are not yet in the latest run.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from citehop.artifacts import atomic_write_text

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
INDEX_NAME = "index.json"


def claims_dir(project_dir: Path) -> Path:
    return Path(project_dir) / "claims"


def discover_claim_paths(project_dir: Path) -> list[Path]:
    """Claim JSON paths: index.json file names as hints, plus claims/*.json."""
    folder = claims_dir(project_dir)
    if not folder.is_dir():
        return []
    names: set[str] = set()
    index_path = folder / INDEX_NAME
    if index_path.is_file():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and data.get("format") == "citehop.claims.index.v1":
            for item in data.get("claims") or []:
                if isinstance(item, dict) and item.get("file"):
                    names.add(str(item["file"]))
    for path in folder.glob("*.json"):
        if path.name != INDEX_NAME:
            names.add(path.name)
    out: list[Path] = []
    for name in sorted(names):
        path = folder / name
        if path.is_file() and path.name != INDEX_NAME:
            out.append(path)
    return out


def read_claim_file(path: Path) -> dict[str, Any] | None:
    """Load one citehop.claim.v1 object, or None if unreadable or malformed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    fmt = data.get("format")
    if fmt is not None and fmt != "citehop.claim.v1":
        return None
    required = ("claim_id", "claim_type", "claim_text", "quoted_source_span")
    if any(not str(data.get(key) or "").strip() for key in required):
        return None
    return data


def claim_filename(claim_id: str) -> str:
    safe = _SAFE.sub("_", (claim_id or "").strip()).strip("._") or "claim"
    return f"{safe}.json"


def claim_file_path(project_dir: Path, claim_id: str) -> Path:
    return claims_dir(project_dir) / claim_filename(claim_id)


def claim_payload(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "citehop.claim.v1",
        "claim_id": rec.get("claim_id"),
        "project_id": rec.get("project_id"),
        "run_id": rec.get("run_id"),
        "paper_canonical_id": rec.get("paper_canonical_id"),
        "claim_type": rec.get("claim_type"),
        "claim_text": rec.get("claim_text"),
        "structured_fields": rec.get("structured_fields") or {},
        "quoted_source_span": rec.get("quoted_source_span"),
        "source_char_offset": rec.get("source_char_offset"),
        "confidence_self_reported": rec.get("confidence_self_reported"),
        "present_in_pass_a": rec.get("present_in_pass_a"),
        "present_in_pass_b": rec.get("present_in_pass_b"),
        "agreement": rec.get("agreement"),
        "disagreement_notes": rec.get("disagreement_notes"),
        "verification_status": rec.get("verification_status"),
        "human_edit": rec.get("human_edit"),
        "paper_title": rec.get("paper_title"),
        "doi": rec.get("doi"),
        "arxiv_id": rec.get("arxiv_id"),
        "year": rec.get("year"),
        "venue": rec.get("venue"),
        "full_text_used": rec.get("full_text_used"),
        "prompt_char_range": rec.get("prompt_char_range"),
        "schema_id": rec.get("schema_id"),
    }


def write_claim_file(project_dir: Path, rec: dict[str, Any]) -> Path:
    dest = claim_file_path(project_dir, rec["claim_id"])
    atomic_write_text(
        dest, json.dumps(claim_payload(rec), indent=2, ensure_ascii=False) + "\n"
    )
    return dest


def write_claim_files(project_dir: Path, records: list[dict[str, Any]]) -> list[Path]:
    paths = [
        write_claim_file(project_dir, rec) for rec in records if rec.get("claim_id")
    ]
    write_claims_index(project_dir)
    return paths


def write_claims_index(project_dir: Path) -> Path:
    folder = claims_dir(project_dir)
    folder.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        if path.name == INDEX_NAME:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items.append(
            {
                "claim_id": data.get("claim_id"),
                "file": path.name,
                "paper_canonical_id": data.get("paper_canonical_id"),
                "claim_type": data.get("claim_type"),
                "agreement": data.get("agreement"),
                "verification_status": data.get("verification_status"),
                "claim_text": data.get("claim_text"),
            }
        )
    dest = folder / INDEX_NAME
    payload = {
        "format": "citehop.claims.index.v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "claim_count": len(items),
        "claims": items,
    }
    tmp = dest.with_name(f".{INDEX_NAME}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(dest)
    return dest
