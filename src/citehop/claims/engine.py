"""Schema-driven dual-pass extraction. No claim taxonomy in this module."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from citehop.ids import file_id
from citehop.store import Manifest

from .align import merge_passes, merge_windows
from .llm import (
    CONFIDENCE,
    BackendUnavailable,
    ContextTooLong,
    GenerationCancelled,
    GenerationTimeout,
    LLMBackend,
    LLMError,
    check_cancelled,
    complete_prompt,
    parse_claims_json,
    retryable_backend_message,
    select_backend,
)
from .locate import locate_span
from .prompt import build_extraction_prompt
from .schema import SchemaError, type_ids, validate_schema_for_run
from .store import ClaimStore

MAX_PAPER_CHARS = 60_000
WINDOW_OVERLAP_CHARS = MAX_PAPER_CHARS // 8
MAX_WINDOWS = 6
MIN_PAPER_CHARS = 1_500


class ExtractionError(RuntimeError):
    pass


_ABSTRACT_ONLY_HEADER = "[abstract_only]\n\n"


def inspect_paper_text(corpus_dir: Path, paper: dict[str, Any]) -> tuple[str | None, str]:
    """Return (stored text, full_text_used). Never model memory.

    full_text_used is `full_text`, `abstract_only`, or `unknown`.
    """
    fid = paper.get("file_id") or file_id(paper["canonical_id"])
    path = Path(corpus_dir) / "text" / f"{fid}.txt"
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        kind = "full_text"
        if text.startswith(_ABSTRACT_ONLY_HEADER):
            text = text[len(_ABSTRACT_ONLY_HEADER) :]
            kind = "abstract_only"
        elif text.startswith("[abstract_only]\n"):
            text = text[len("[abstract_only]\n") :]
            kind = "abstract_only"
        if text.strip():
            return text, kind
    abstract = (paper.get("abstract") or "").strip()
    if abstract:
        return abstract, "abstract_only"
    return None, "unknown"


def load_paper_text(corpus_dir: Path, paper: dict[str, Any]) -> str | None:
    """Literal stored text for this paper. Never model memory."""
    text, _kind = inspect_paper_text(corpus_dir, paper)
    return text


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
                    "doi": row["doi"],
                    "arxiv_id": row["arxiv_id"],
                    "year": row["year"],
                    "venue": row["venue"],
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


def paper_windows(
    n: int,
    *,
    window: int | None = None,
    overlap: int | None = None,
    max_windows: int | None = None,
) -> list[tuple[int, int]]:
    """Overlapping [start, end) character windows into stored text.

    Short files return a single window covering the whole string. Huge files
    stop after `max_windows` so extraction cannot run forever.
    """
    window = MAX_PAPER_CHARS if window is None else int(window)
    overlap = WINDOW_OVERLAP_CHARS if overlap is None else int(overlap)
    max_windows = MAX_WINDOWS if max_windows is None else int(max_windows)
    if n <= 0:
        return [(0, 0)]
    if n <= window:
        return [(0, n)]
    overlap = max(0, min(overlap, window - 1)) if window > 1 else 0
    step = max(1, window - overlap)
    out: list[tuple[int, int]] = []
    start = 0
    while len(out) < max_windows:
        end = min(start + window, n)
        out.append((start, end))
        if end >= n:
            break
        start += step
        if start >= n:
            break
    return out


def extract_paper(
    *,
    project_id: str,
    run_id: str,
    schema: dict[str, Any],
    paper: dict[str, Any],
    stored_text: str,
    llm: LLMBackend,
    full_text_used: str = "unknown",
) -> tuple[list[dict[str, Any]], int]:
    schema = validate_schema_for_run(schema)
    windows = paper_windows(len(stored_text))
    capped = bool(windows) and windows[-1][1] < len(stored_text)
    per_window: list[list[dict[str, Any]]] = []
    tokens = 0
    for win_start, win_end in windows:
        located, used = _extract_window(
            schema=schema,
            stored_text=stored_text,
            win_start=win_start,
            win_end=win_end,
            llm=llm,
        )
        tokens += used
        per_window.append(located)
    merged = merge_windows(per_window)
    cap_note = None
    if capped:
        cap_note = (
            f"Paper longer than {MAX_WINDOWS} windows of {MAX_PAPER_CHARS} characters "
            f"(overlap {WINDOW_OVERLAP_CHARS}); extraction stopped at character "
            f"{windows[-1][1]} of {len(stored_text)}."
        )
    out = []
    for item in merged:
        rec = _claim_record(
            item,
            project_id=project_id,
            run_id=run_id,
            paper=paper,
            schema=schema,
            full_text_used=full_text_used,
        )
        if cap_note:
            existing = rec.get("disagreement_notes")
            rec["disagreement_notes"] = f"{existing} {cap_note}".strip() if existing else cap_note
        out.append(rec)
    return out, tokens


def _extract_window(
    *,
    schema: dict[str, Any],
    stored_text: str,
    win_start: int,
    win_end: int,
    llm: LLMBackend,
) -> tuple[list[dict[str, Any]], int]:
    clip_len = max(0, win_end - win_start)
    floor = min(MIN_PAPER_CHARS, clip_len) if clip_len else 0
    last_ctx: ContextTooLong | None = None
    while True:
        try:
            located, tokens = _extract_paper_clipped(
                schema=schema,
                stored_text=stored_text,
                clip_start=win_start,
                clip_len=clip_len,
                llm=llm,
            )
            prompt_range = [win_start, win_start + clip_len]
            for rec in located:
                rec["prompt_char_range"] = prompt_range
            return located, tokens
        except GenerationCancelled:
            raise
        except ContextTooLong as exc:
            last_ctx = exc
            if clip_len <= floor:
                break
            next_len = max(floor, clip_len // 2)
            if next_len >= clip_len:
                break
            clip_len = next_len
    raise LLMError(
        f"Paper still exceeds this model's context window after truncating to "
        f"{clip_len} characters. {last_ctx or ''}".strip()
    ) from last_ctx


def _extract_paper_clipped(
    *,
    schema: dict[str, Any],
    stored_text: str,
    clip_len: int,
    llm: LLMBackend,
    clip_start: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    clipped = stored_text[clip_start : clip_start + clip_len]
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
    return merge_passes(located_passes[0], located_passes[1]), tokens


def _claim_record(
    item: dict[str, Any],
    *,
    project_id: str,
    run_id: str,
    paper: dict[str, Any],
    schema: dict[str, Any],
    full_text_used: str,
) -> dict[str, Any]:
    year = paper.get("year")
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = None
    return {
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
        "paper_title": paper.get("title"),
        "doi": paper.get("doi"),
        "arxiv_id": paper.get("arxiv_id"),
        "year": year,
        "venue": paper.get("venue"),
        "full_text_used": full_text_used,
        "prompt_char_range": list(item["prompt_char_range"])
        if item.get("prompt_char_range") is not None
        else None,
        "schema_id": schema.get("schema_id"),
    }


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

    text, kind = inspect_paper_text(corpus_dir, paper)
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
            full_text_used=kind,
        )
    except GenerationCancelled:
        store.release_paper(run_id, cid)
        raise
    except GenerationTimeout as exc:
        store.complete_paper(run_id, cid, status="error", error=str(exc))
        return "error"
    except BackendUnavailable:
        store.release_paper(run_id, cid)
        raise
    except (LLMError, SchemaError, ExtractionError) as exc:
        if isinstance(exc, LLMError) and retryable_backend_message(str(exc)):
            store.release_paper(run_id, cid)
            raise BackendUnavailable(str(exc)) from exc
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
