"""Markdown evidence table from claim records. Shared by the UI and scripts."""

from __future__ import annotations

import json
from typing import Any


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _fields_cell(fields: object) -> str:
    if not fields:
        return ""
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except json.JSONDecodeError:
            return _cell(fields)
    if not isinstance(fields, dict):
        return _cell(fields)
    parts = []
    for key, val in fields.items():
        if val in (None, "", []):
            continue
        parts.append(f"{key}={val}")
    return _cell("; ".join(parts))


def to_markdown(claims: list[dict[str, Any]]) -> str:
    lines = [
        "| paper | type | fields | quote | verification |",
        "| --- | --- | --- | --- | --- |",
    ]
    for rec in claims:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(rec.get("paper_canonical_id")),
                    _cell(rec.get("claim_type")),
                    _fields_cell(rec.get("structured_fields")),
                    _cell(rec.get("quoted_source_span")),
                    _cell(rec.get("verification_status")),
                ]
            )
            + " |"
        )
    if len(claims) == 0:
        lines.append("| — | — | — | — | — |")
    return "\n".join(lines) + "\n"
