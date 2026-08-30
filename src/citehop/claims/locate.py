"""Locate a quoted span in the paper's stored text. Offsets are required."""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def clamp_span(text: str, start: int, end: int) -> tuple[int, int, bool]:
    """Clamp [start, end) to text; third value is True if the input was out of range."""
    n = len(text)
    try:
        s0, e0 = int(start), int(end)
    except (TypeError, ValueError):
        return 0, 0, True
    out_of_range = s0 < 0 or e0 < 0 or s0 > n or e0 > n or s0 > e0
    s = max(0, min(s0, n))
    e = max(s, min(e0, n))
    return s, e, out_of_range


def locate_span(stored_text: str, quote: str) -> tuple[int, int] | None:
    """Return [start, end) character offsets into stored_text, or None."""
    if not stored_text or not quote:
        return None
    exact = stored_text.find(quote)
    if exact >= 0:
        return exact, exact + len(quote)
    stripped = quote.strip()
    if stripped and stripped != quote:
        exact = stored_text.find(stripped)
        if exact >= 0:
            return exact, exact + len(stripped)
    collapsed_hay = _WS.sub(" ", stored_text)
    collapsed_needle = _WS.sub(" ", quote.strip())
    if collapsed_needle:
        idx = collapsed_hay.find(collapsed_needle)
        if idx >= 0:
            return _map_collapsed_offset(stored_text, idx, len(collapsed_needle))
    return None


def _map_collapsed_offset(original: str, collapsed_start: int, collapsed_len: int) -> tuple[int, int]:
    """Map an index in whitespace-collapsed text back onto original."""
    orig_i = 0
    col_i = 0
    start = None
    while orig_i < len(original) and col_i <= collapsed_start + collapsed_len:
        if original[orig_i].isspace():
            if start is None and col_i == collapsed_start:
                start = orig_i
            orig_i += 1
            continue
        if col_i == collapsed_start and start is None:
            start = orig_i
        if col_i == collapsed_start + collapsed_len:
            return start if start is not None else orig_i, orig_i
        col_i += 1
        orig_i += 1
    if start is None:
        start = 0
    return start, orig_i


def collapse_ws(text: str) -> str:
    return _WS.sub(" ", (text or "").strip())


def search_needles(quote: str) -> list[str]:
    """PDF/search strings derived from a quoted span, longest first.

    Full quotes often fail in a PDF text layer (hyphenation, line breaks).
    Shorter unique prefixes usually still land on the discussed passage.
    """
    collapsed = collapse_ws(quote)
    if not collapsed:
        return []
    out: list[str] = []

    def add(s: str) -> None:
        s = collapse_ws(s)
        if s and s not in out:
            out.append(s)

    add(collapsed)
    if len(collapsed) > 96:
        cut = collapsed[:96]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        add(cut)
    words = collapsed.split()
    if len(words) >= 12:
        add(" ".join(words[:12]))
    elif len(words) >= 6:
        add(" ".join(words[:6]))
    return out
