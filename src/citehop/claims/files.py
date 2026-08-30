"""One JSON file per claim under <project>/claims/. SQLite stays the source of truth."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from citehop.artifacts import atomic_write_text

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
INDEX_NAME = "index.json"


def claims_dir(project_dir: Path) -> Path:
    return Path(project_dir) / "claims"


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
    atomic_write_text(dest, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return dest
