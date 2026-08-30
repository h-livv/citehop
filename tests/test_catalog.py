"""Corpus count labels: hop split vs provider lists vs full text."""

from __future__ import annotations

import unittest
from pathlib import Path

from citehop.catalog import CorpusSummary, coverage_caption, hop_counts, pdf_over_cited_citing


def _summary(**kwargs) -> CorpusSummary:
    base = dict(
        slug="x",
        path=Path("."),
        seed_title="t",
        seed_id=None,
        seed_doi=None,
        seed_arxiv=None,
        year=None,
        paper_count=10,
        relation_counts={"seed": 1, "backward_reference": 6, "forward_citation": 3},
        status_counts={"pending": 2},
        run_mode=None,
        started_at=None,
        finished_at=None,
        pdf_count=4,
        success_count=5,
        s2_reference_count_reported="5",
        s2_citation_count_reported="9",
        openalex_referenced_works_count="7",
        openalex_cited_by_count="8",
    )
    base.update(kwargs)
    return CorpusSummary(**base)


class CatalogCaptionTests(unittest.TestCase):
    def test_hop_counts(self) -> None:
        seed, back, fwd, total = hop_counts(_summary())
        self.assertEqual((seed, back, fwd, total), (1, 6, 3, 10))

    def test_pdf_over_cited_citing_excludes_seed(self) -> None:
        self.assertEqual(pdf_over_cited_citing(_summary()), (4, 9))

    def test_coverage_caption_is_not_a_fraction(self) -> None:
        cap = coverage_caption(_summary())
        self.assertNotIn("of 5 reported", cap)
        self.assertNotIn("of 9 reported", cap)
        self.assertIn("S2 listed 5 references and 9 citations at resolve", cap)
        self.assertIn("OpenAlex listed 7 referenced works and 8 citations at resolve", cap)
        self.assertNotIn("PDF files", cap)
        self.assertIn("fetch still open: 2 pending or retry", cap)


if __name__ == "__main__":
    unittest.main()
