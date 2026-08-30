"""PDF quote location and highlight copy."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf

from citehop.claims.locate import search_needles
from citehop.claims.pdf_locate import copy_pdf_with_quote_highlight, locate_quote_in_pdf


def _pdf_with_pages(pages: list[str], dest: Path) -> Path:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dest)
    doc.close()
    return dest


class SearchNeedlesTests(unittest.TestCase):
    def test_longest_first_and_prefix(self) -> None:
        quote = "The quantum volume of the superconducting device is 32 according to the authors here."
        needles = search_needles(quote)
        self.assertEqual(needles[0], quote)
        self.assertTrue(any(n.startswith("The quantum volume") for n in needles))

    def test_empty(self) -> None:
        self.assertEqual(search_needles("   "), [])


class PdfLocateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_finds_quote_on_later_page_and_highlights(self) -> None:
        src = _pdf_with_pages(
            [
                "Intro page one with filler.",
                "The quantum volume of the device is 32 according to the authors.",
                "Conclusion page three.",
            ],
            self.root / "paper.pdf",
        )
        quote = "The quantum volume of the device is 32 according to the authors."
        hit = locate_quote_in_pdf(src, quote, char_start=40)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.page_index, 1)
        self.assertTrue(hit.highlighted)

        dest = self.root / "hi.pdf"
        copied = copy_pdf_with_quote_highlight(src, dest, quote, char_start=40)
        self.assertEqual(copied.page_index, 1)
        self.assertTrue(dest.is_file())
        out = pymupdf.open(dest)
        try:
            annots = list(out[1].annots() or [])
            self.assertTrue(annots)
        finally:
            out.close()

    def test_prefers_page_matching_char_offset(self) -> None:
        repeated = "shared unique phrase appears twice"
        src = _pdf_with_pages(
            [repeated, "middle", repeated],
            self.root / "dup.pdf",
        )
        doc = pymupdf.open(src)
        try:
            texts = [page.get_text("text") for page in doc]
        finally:
            doc.close()
        start = sum(len(t) + 1 for t in texts[:-1])
        hit = locate_quote_in_pdf(src, repeated, char_start=start)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.page_index, 2)

    def test_missing_file(self) -> None:
        self.assertIsNone(locate_quote_in_pdf(self.root / "nope.pdf", "hello"))


if __name__ == "__main__":
    unittest.main()
