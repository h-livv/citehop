"""Map a quoted span onto a PDF page and optionally highlight it.

Offsets in the claims store index extracted text, not PDF page numbers.
This module searches the PDF text layer for the quote (and shorter needles)
and falls back to a page estimate from concatenated page text vs char offset.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .locate import search_needles


@dataclass(frozen=True)
class PdfQuoteHit:
    page_index: int
    needle: str
    rects: tuple[tuple[float, float, float, float], ...]

    @property
    def highlighted(self) -> bool:
        return bool(self.rects)


def locate_quote_in_pdf(
    pdf_path: Path | str,
    quote: str,
    *,
    char_start: int = 0,
) -> PdfQuoteHit | None:
    path = Path(pdf_path)
    if not path.is_file():
        return None
    doc = pymupdf.open(path)
    try:
        return _hit_in_doc(doc, quote, char_start)
    finally:
        doc.close()


def copy_pdf_with_quote_highlight(
    src: Path | str,
    dest: Path | str,
    quote: str,
    *,
    char_start: int = 0,
) -> PdfQuoteHit:
    """Write dest (a copy of src) with the quote highlighted when the text layer matches."""
    src_path = Path(src)
    dest_path = Path(dest)
    doc = pymupdf.open(src_path)
    try:
        hit = _hit_in_doc(doc, quote, char_start)
        if hit.rects and 0 <= hit.page_index < doc.page_count:
            page = doc[hit.page_index]
            for box in hit.rects:
                annot = page.add_highlight_annot(pymupdf.Rect(*box))
                annot.update()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(dest_path, garbage=0, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
        return hit
    finally:
        doc.close()


def _hit_in_doc(doc: pymupdf.Document, quote: str, char_start: int) -> PdfQuoteHit:
    page_texts = [page.get_text("text") for page in doc]
    estimated = _estimate_page(page_texts, char_start)
    needles = search_needles(quote)
    best: PdfQuoteHit | None = None
    best_dist = 10**9
    for i, page in enumerate(doc):
        for needle in needles:
            rects = _search_page(page, needle)
            if not rects:
                continue
            dist = abs(i - estimated)
            if dist < best_dist:
                best_dist = dist
                best = PdfQuoteHit(
                    page_index=i,
                    needle=needle,
                    rects=tuple((r.x0, r.y0, r.x1, r.y1) for r in rects),
                )
            break
    if best is not None:
        return best
    page_index = estimated if page_texts else 0
    return PdfQuoteHit(page_index=page_index, needle="", rects=())


def _search_page(page: pymupdf.Page, needle: str) -> list:
    if not needle:
        return []
    try:
        found = page.search_for(needle)
    except (ValueError, RuntimeError):
        return []
    return list(found or [])


def _estimate_page(page_texts: list[str], char_start: int) -> int:
    """Page index as if text were joined with a newline, matching extract_pdf_text."""
    if not page_texts:
        return 0
    try:
        start = max(0, int(char_start))
    except (TypeError, ValueError):
        start = 0
    pos = 0
    last = len(page_texts) - 1
    for i, text in enumerate(page_texts):
        end = pos + len(text)
        if start < end or i == last:
            return i
        pos = end + 1
    return last
