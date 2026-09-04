"""Merge two extraction passes using spatial proximity of source spans.

Alignment does not inspect schema field names or domain labels. Pairing is
same claim_type plus span proximity; agreement is generic field/quote equality.
"""

from __future__ import annotations

from typing import Any

PROXIMITY_CHARS = 120
AGREEMENT_RANK = {
    "disagreement": 0,
    "single_pass_only": 1,
    "partial_match": 2,
    "match": 3,
}


def span_gap(a: list[int] | tuple[int, int], b: list[int] | tuple[int, int]) -> int:
    """0 if ranges overlap; otherwise the gap between them."""
    a0, a1 = int(a[0]), int(a[1])
    b0, b1 = int(b[0]), int(b[1])
    if a1 < b0:
        return b0 - a1
    if b1 < a0:
        return a0 - b1
    return 0


def _norm_str(value: str) -> str:
    """Fold hyphen variants and collapse whitespace for string compares only."""
    folded = (
        value.replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2015", "-")
        .replace("\u2212", "-")
        .replace("\u00ad", "")
    )
    return " ".join(folded.split())


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return _norm_str(a) == _norm_str(b)
    return a == b


def _fields_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = set(a) | set(b)
    for key in keys:
        if not _values_equal(a.get(key), b.get(key)):
            return False
    return True


def merge_windows(window_claims: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One row per finding across overlapping windows.

    Pairing is the same as dual-pass: claim_type + span proximity. The kept
    row's prompt_char_range is from the earlier window. disagreement_notes is
    appended only when structured fields actually differed.
    """
    if not window_claims:
        return []
    merged = list(window_claims[0])
    for nxt in window_claims[1:]:
        merged = _merge_window_pair(merged, nxt)
    return merged


def _merge_window_pair(
    lefts: list[dict[str, Any]],
    rights: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for left in lefts:
        partner_i = _nearest_partner(left, rights, used)
        if partner_i is None:
            out.append(left)
            continue
        used.add(partner_i)
        out.append(_merge_window_hit(left, rights[partner_i]))
    for i, right in enumerate(rights):
        if i not in used:
            out.append(right)
    return out


def _merge_window_hit(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    fields_a = a.get("structured_fields") or {}
    fields_b = b.get("structured_fields") or {}
    if not _fields_equal(fields_a, fields_b):
        extra = (
            "Overlapping windows differed on fields. "
            f"Kept window {a.get('prompt_char_range')!r} fields={fields_a!r}; "
            f"other window {b.get('prompt_char_range')!r} fields={fields_b!r}."
        )
        existing = out.get("disagreement_notes")
        out["disagreement_notes"] = f"{existing} {extra}".strip() if existing else extra
    if len(b.get("quoted_source_span") or "") > len(out.get("quoted_source_span") or ""):
        out["quoted_source_span"] = b["quoted_source_span"]
        out["source_char_offset"] = list(b["source_char_offset"])
    return out


def merge_passes(
    pass_a: list[dict[str, Any]],
    pass_b: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    used_b: set[int] = set()
    merged: list[dict[str, Any]] = []
    for left in pass_a:
        partner_i = _nearest_partner(left, pass_b, used_b)
        if partner_i is None:
            merged.append(_single(left, pass_name="a"))
            continue
        used_b.add(partner_i)
        merged.append(_pair(left, pass_b[partner_i]))
    for i, right in enumerate(pass_b):
        if i not in used_b:
            merged.append(_single(right, pass_name="b"))
    return merged


def _nearest_partner(
    left: dict[str, Any],
    pool: list[dict[str, Any]],
    used: set[int],
) -> int | None:
    best_i: int | None = None
    best_gap = None
    for i, right in enumerate(pool):
        if i in used:
            continue
        if right.get("claim_type") != left.get("claim_type"):
            continue
        gap = span_gap(left["source_char_offset"], right["source_char_offset"])
        if gap > PROXIMITY_CHARS:
            continue
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best_i = i
    return best_i


def _single(claim: dict[str, Any], pass_name: str) -> dict[str, Any]:
    out = dict(claim)
    out["present_in_pass_a"] = pass_name == "a"
    out["present_in_pass_b"] = pass_name == "b"
    out["agreement"] = "single_pass_only"
    out["disagreement_notes"] = f"Only present in pass {pass_name.upper()}"
    return out


def _pair(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    fields_a = a.get("structured_fields") or {}
    fields_b = b.get("structured_fields") or {}
    quotes_alike = _quotes_alike(a.get("quoted_source_span") or "", b.get("quoted_source_span") or "")
    fields_same = _fields_equal(fields_a, fields_b)
    shared = _shared_field_values(fields_a, fields_b)
    gap = span_gap(a["source_char_offset"], b["source_char_offset"])
    if fields_same and (quotes_alike or gap == 0):
        agreement = "match"
        notes = None
    elif shared or gap == 0 or quotes_alike:
        agreement = "partial_match"
        notes = _diff_notes(a, b)
    else:
        agreement = "disagreement"
        notes = _diff_notes(a, b)
    out = dict(a)
    out["present_in_pass_a"] = True
    out["present_in_pass_b"] = True
    out["agreement"] = agreement
    out["disagreement_notes"] = notes
    if len(b.get("quoted_source_span") or "") > len(out.get("quoted_source_span") or ""):
        out["quoted_source_span"] = b["quoted_source_span"]
        out["source_char_offset"] = list(b["source_char_offset"])
    return out


def _quotes_alike(a: str, b: str) -> bool:
    aa, bb = _norm_str(a.strip()), _norm_str(b.strip())
    if not aa or not bb:
        return False
    return aa == bb or aa in bb or bb in aa


def _shared_field_values(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = set(a) | set(b)
    for key in keys:
        if key in a and key in b and a[key] not in (None, "", []) and _values_equal(a[key], b[key]):
            return True
    return False


def _diff_notes(a: dict[str, Any], b: dict[str, Any]) -> str:
    return (
        "Pass A and pass B were nearby in the source but differed. "
        f"A fields={a.get('structured_fields')!r}; B fields={b.get('structured_fields')!r}."
    )
