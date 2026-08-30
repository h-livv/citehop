"""Schema-driven dual-pass extraction. No claim taxonomy in this module."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from citehop.ids import file_id
from citehop.store import Manifest

from .align import merge_passes
from .llm import (
    CONFIDENCE,
    BackendUnavailable,
    ContextTooLong,
    GenerationCancelled,
    LLMBackend,
    LLMError,
    check_cancelled,
    complete_prompt,
    parse_claims_json,
    select_backend,
)
from .locate import locate_span
from .prompt import build_extraction_prompt
from .schema import SchemaError, type_ids, validate_schema_for_run
from .store import ClaimStore

MAX_PAPER_CHARS = 60_000


class ExtractionError(RuntimeError):
    pass


def load_paper_text(corpus_dir: Path, paper: dict[str, Any]) -> str | None:
    """Literal stored text for this paper. Never model memory."""
    fid = paper.get("file_id") or file_id(paper["canonical_id"])
    path = Path(corpus_dir) / "text" / f"{fid}.txt"
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            return text
    abstract = (paper.get("abstract") or "").strip()
    return abstract or None


def corpus_papers(corpus_dir: Path) -> list[dict[str, Any]]:
    db = Path(corpus_dir) / "manifest.db"
    if not db.is_file():
        raise ExtractionError(f"No manifest.db in corpus {corpus_dir}")
    manifest = Manifest(db)
    try:
        out = []
        for row in manifest.all_papers():
            out.append(
                {
                    "canonical_id": row["canonical_id"],
                    "file_id": row["file_id"],
                    "title": row["title"],
                    "abstract": row["abstract"],
                    "full_text_available": bool(row["full_text_available"]),
                }
            )
        return out
    finally:
        manifest.close()


def coerce_fields(
    field_defs: list[dict[str, Any]],
    raw: Any,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Coerce structured_fields to the schema.

    Extraction (strict=False) accepts JSON-ish LLM output (numbers as strings).
    Human review (strict=True) rejects the wrong JSON type instead of coercing
    a string into a number.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SchemaError("structured_fields must be an object")
    out: dict[str, Any] = {}
    for field in field_defs:
        key = field["key"]
        value = raw.get(key)
        ftype = field["type"]
        if value is None:
            out[key] = None
            continue
        if ftype == "number":
            if isinstance(value, bool) or (strict and not isinstance(value, (int, float))):
                raise SchemaError(
                    f"Field {key!r} must be a number, not {type(value).__name__}"
                )
            try:
                out[key] = float(value)
            except (TypeError, ValueError) as exc:
                raise SchemaError(f"Field {key!r} must be a number") from exc
        elif ftype == "boolean":
            if isinstance(value, bool):
                out[key] = value
            elif strict:
                raise SchemaError(
                    f"Field {key!r} must be a boolean, not {type(value).__name__}"
                )
            elif value in (0, 1, "true", "false", "True", "False"):
                out[key] = value in (True, 1, "true", "True")
            else:
                raise SchemaError(f"Field {key!r} must be a boolean")
        elif ftype == "enum":
            text = str(value)
            allowed = field.get("enum_values") or []
            if allowed and text not in allowed:
                raise SchemaError(
                    f"Field {key!r} must be one of {allowed}, not {text!r}"
                )
            out[key] = text
        else:
            if strict and not isinstance(value, str):
                raise SchemaError(
                    f"Field {key!r} must be a string, not {type(value).__name__}"
                )
            out[key] = str(value)
    return out


def normalize_raw_claim(
    raw: dict[str, Any],
    schema: dict[str, Any],
    stored_text: str,
) -> dict[str, Any] | None:
    allowed = set(type_ids(schema))
    ctype = raw.get("claim_type")
    if ctype not in allowed:
        return None
    quote = raw.get("quoted_source_span")
    if not isinstance(quote, str) or not quote.strip():
        return None
    located = locate_span(stored_text, quote.strip())
    if located is None:
        return None
    start, end = located
    verbatim = stored_text[start:end]
    field_defs = []
    for item in schema["claim_types"]:
        if item["type_id"] == ctype:
            field_defs = item["structured_fields"]
            break
    try:
        fields = coerce_fields(field_defs, raw.get("structured_fields"))
    except SchemaError:
        return None
    conf = raw.get("confidence_self_reported")
    if conf not in CONFIDENCE:
        conf = "medium"
    text = raw.get("claim_text")
    if not isinstance(text, str) or not text.strip():
        text = verbatim[:240]
    return {
        "claim_type": ctype,
        "claim_text": text.strip(),
        "structured_fields": fields,
        "quoted_source_span": verbatim,
        "source_char_offset": [start, end],
        "confidence_self_reported": conf,
    }


MIN_PAPER_CHARS = 1_500


def extract_paper(
    *,
    project_id: str,
    run_id: str,
    schema: dict[str, Any],
    paper: dict[str, Any],
    stored_text: str,
    llm: LLMBackend,
) -> tuple[list[dict[str, Any]], int]:
    schema = validate_schema_for_run(schema)
    clip_len = min(MAX_PAPER_CHARS, len(stored_text)) or len(stored_text)
    last_ctx: ContextTooLong | None = None
    while True:
        try:
            return _extract_paper_clipped(
                project_id=project_id,
                run_id=run_id,
                schema=schema,
                paper=paper,
                stored_text=stored_text,
                clip_len=clip_len,
                llm=llm,
            )
        except GenerationCancelled:
            raise
        except ContextTooLong as exc:
            last_ctx = exc
            if clip_len <= MIN_PAPER_CHARS:
                break
            next_len = max(MIN_PAPER_CHARS, clip_len // 2)
            if next_len >= clip_len:
                break
            clip_len = next_len
    raise LLMError(
        f"Paper still exceeds this model's context window after truncating to "
        f"{clip_len} characters. {last_ctx or ''}".strip()
    ) from last_ctx


def _extract_paper_clipped(
    *,
    project_id: str,
    run_id: str,
    schema: dict[str, Any],
    paper: dict[str, Any],
    stored_text: str,
    clip_len: int,
    llm: LLMBackend,
) -> tuple[list[dict[str, Any]], int]:
    clipped = stored_text[:clip_len]
    tokens = 0
    located_passes: list[list[dict[str, Any]]] = []
    for pass_id in ("A", "B"):
        check_cancelled()
        prompt = build_extraction_prompt(schema, clipped, pass_id)
        raw_text, used = complete_prompt(llm, prompt)
        tokens += used
        raw_claims = parse_claims_json(raw_text)
        located = []
        for raw in raw_claims:
            if not isinstance(raw, dict):
                continue
            rec = normalize_raw_claim(raw, schema, stored_text)
            if rec:
                located.append(rec)
        located_passes.append(located)
    merged = merge_passes(located_passes[0], located_passes[1])
    out = []
    for item in merged:
        rec = {
            "claim_id": uuid.uuid4().hex,
            "project_id": project_id,
            "run_id": run_id,
            "paper_canonical_id": paper["canonical_id"],
            "claim_type": item["claim_type"],
            "claim_text": item["claim_text"],
            "structured_fields": item.get("structured_fields") or {},
            "quoted_source_span": item["quoted_source_span"],
            "source_char_offset": list(item["source_char_offset"]),
            "confidence_self_reported": item.get("confidence_self_reported") or "medium",
            "present_in_pass_a": bool(item.get("present_in_pass_a")),
            "present_in_pass_b": bool(item.get("present_in_pass_b")),
            "agreement": item["agreement"],
            "disagreement_notes": item.get("disagreement_notes"),
            "verification_status": "unverified_by_human",
            "human_edit": None,
        }
        out.append(rec)
    return out, tokens


def process_one_paper(
    store: ClaimStore,
    *,
    project_id: str,
    run_id: str,
    schema: dict[str, Any],
    corpus_dir: Path,
    paper_row: Any,
    papers_by_id: dict[str, dict[str, Any]],
    llm: LLMBackend,
    token_budget: int,
) -> str:
    """Extract one pending paper. Returns the paper status written."""
    cid = paper_row["paper_canonical_id"]
    paper = papers_by_id.get(cid) or {"canonical_id": cid, "file_id": paper_row.get("file_id")}
    run = store.get_run(run_id)
    if run and int(run["tokens_used"]) >= token_budget:
        store.release_paper(run_id, cid)
        store.set_run_status(run_id, "paused", error="Token budget reached")
        return "budget"

    text = load_paper_text(corpus_dir, paper)
    if not text:
        store.complete_paper(run_id, cid, status="skipped_no_text")
        return "skipped_no_text"
    try:
        check_cancelled()
        claims, tokens = extract_paper(
            project_id=project_id,
            run_id=run_id,
            schema=schema,
            paper=paper,
            stored_text=text,
            llm=llm,
        )
    except GenerationCancelled:
        store.release_paper(run_id, cid)
        raise
    except BackendUnavailable:
        store.release_paper(run_id, cid)
        raise
    except (LLMError, SchemaError, ExtractionError) as exc:
        store.complete_paper(run_id, cid, status="error", error=str(exc))
        return "error"
    store.complete_paper(
        run_id,
        cid,
        claims=claims,
        tokens_used=tokens,
        add_tokens=tokens,
        status="done",
    )
    run = store.get_run(run_id)
    if run and int(run["tokens_used"]) >= token_budget:
        store.set_run_status(run_id, "paused", error="Token budget reached")
    return "done"
