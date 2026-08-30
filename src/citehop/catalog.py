"""Read-only listing of corpora and papers under the configured corpora directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import row_to_metadata
from .config import CORPORA_DIR, ensure_data_dirs
from .store import Manifest

RELATION_LABELS = {
    "seed": "Seed",
    "backward_reference": "Cited by seed",
    "forward_citation": "Cites seed",
}


@dataclass
class CorpusSummary:
    slug: str
    path: Path
    seed_title: str
    seed_id: str | None
    seed_doi: str | None
    seed_arxiv: str | None
    year: int | None
    paper_count: int
    relation_counts: dict[str, int]
    status_counts: dict[str, int]
    run_mode: str | None
    started_at: str | None
    finished_at: str | None
    pdf_count: int = 0
    success_count: int = 0
    s2_reference_count_reported: str | None = None
    s2_citation_count_reported: str | None = None
    openalex_referenced_works_count: str | None = None
    openalex_cited_by_count: str | None = None

    @property
    def label(self) -> str:
        title = self.seed_title or self.slug
        year = f" ({self.year})" if self.year else ""
        return f"{title}{year}"


def hop_counts(summary: CorpusSummary) -> tuple[int, int, int, int]:
    """seed, cited-by-seed, citing-seed, total papers in the corpus."""
    rel = summary.relation_counts or {}
    seed = int(rel.get("seed", 0) or 0)
    back = int(rel.get("backward_reference", 0) or 0)
    fwd = int(rel.get("forward_citation", 0) or 0)
    total = int(summary.paper_count or 0)
    if not total:
        total = seed + back + fwd
    return seed, back, fwd, total


def pdf_over_cited_citing(summary: CorpusSummary) -> tuple[int, int]:
    """PDF files in raw/ over cited-by-seed + citing-the-seed (seed excluded)."""
    _, back, fwd, _ = hop_counts(summary)
    return int(summary.pdf_count or 0), back + fwd


def coverage_caption(summary: CorpusSummary) -> str:
    """One line: API lists at resolve, fetch still open. Not a fraction."""
    parts: list[str] = []
    s2_back = summary.s2_reference_count_reported
    s2_fwd = summary.s2_citation_count_reported
    oa_back = summary.openalex_referenced_works_count
    oa_fwd = summary.openalex_cited_by_count

    def _ok(raw: str | None) -> bool:
        return raw not in (None, "", "null", "None")

    if _ok(s2_back) or _ok(s2_fwd):
        parts.append(
            f"S2 listed {s2_back or '—'} references and {s2_fwd or '—'} citations at resolve"
        )
    if _ok(oa_back) or _ok(oa_fwd):
        parts.append(
            f"OpenAlex listed {oa_back or '—'} referenced works and {oa_fwd or '—'} citations at resolve"
        )
    st = summary.status_counts or {}
    open_n = int(st.get("pending", 0) or 0) + int(st.get("failed_retry", 0) or 0)
    if open_n:
        parts.append(f"fetch still open: {open_n} pending or retry")
    return " · ".join(parts)


def _seed_row(manifest: Manifest):
    cid = manifest.get_meta("seed_canonical_id")
    if cid:
        row = manifest.get_paper(cid)
        if row:
            return row
    for row in manifest.all_papers():
        if row["relation_to_seed"] == "seed":
            return row
    return None


def summarize_corpus(corpus_dir: Path) -> CorpusSummary | None:
    db = Path(corpus_dir) / "manifest.db"
    if not db.is_file():
        return None
    manifest = Manifest(db)
    try:
        seed = _seed_row(manifest)
        title = (seed["title"] if seed else None) or corpus_dir.name
        year = int(seed["year"]) if seed and seed["year"] else None
        raw = Path(corpus_dir) / "raw"
        pdf_count = 0
        if raw.is_dir():
            pdf_count = sum(1 for p in raw.iterdir() if p.suffix.lower() == ".pdf")
        return CorpusSummary(
            slug=corpus_dir.name,
            path=Path(corpus_dir),
            seed_title=title,
            seed_id=manifest.get_meta("seed_canonical_id"),
            seed_doi=manifest.get_meta("seed_doi") or (seed["doi"] if seed else None),
            seed_arxiv=manifest.get_meta("seed_arxiv_id")
            or (seed["arxiv_id"] if seed else None),
            year=year,
            paper_count=sum(manifest.counts_by_relation().values()),
            relation_counts=manifest.counts_by_relation(),
            status_counts=manifest.counts_by_status(),
            run_mode=manifest.get_meta("run_mode"),
            started_at=manifest.get_meta("run_started_at"),
            finished_at=manifest.get_meta("run_finished_at"),
            pdf_count=pdf_count,
            success_count=manifest.count_successful_fetches(),
            s2_reference_count_reported=manifest.get_meta("s2_reference_count_reported"),
            s2_citation_count_reported=manifest.get_meta("s2_citation_count_reported"),
            openalex_referenced_works_count=manifest.get_meta(
                "openalex_referenced_works_count"
            ),
            openalex_cited_by_count=manifest.get_meta("openalex_cited_by_count"),
        )
    finally:
        manifest.close()


def list_corpora(root: Path | None = None) -> list[CorpusSummary]:
    if root is None:
        ensure_data_dirs()
    base = Path(root) if root else CORPORA_DIR
    if not base.is_dir():
        return []
    out: list[CorpusSummary] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name.startswith("_"):
            continue
        summary = summarize_corpus(child)
        if summary:
            out.append(summary)
    out.sort(key=lambda s: (s.started_at or "", s.slug), reverse=True)
    return out


def load_papers(corpus_dir: Path) -> list[dict[str, Any]]:
    db = Path(corpus_dir) / "manifest.db"
    if not db.is_file():
        return []
    manifest = Manifest(db)
    try:
        papers = [row_to_metadata(row) for row in manifest.all_papers()]
        for paper in papers:
            paper["relation_label"] = RELATION_LABELS.get(
                paper.get("relation_to_seed") or "",
                paper.get("relation_to_seed") or "",
            )
            fid = paper.get("file_id")
            paper["pdf_path"] = None
            paper["text_path"] = None
            paper["note_path"] = None
            if fid:
                pdf = Path(corpus_dir) / "raw" / f"{fid}.pdf"
                txt = Path(corpus_dir) / "text" / f"{fid}.txt"
                note = Path(corpus_dir) / "papers" / f"{fid}.md"
                if pdf.is_file():
                    paper["pdf_path"] = str(pdf)
                if txt.is_file():
                    paper["text_path"] = str(txt)
                if note.is_file():
                    paper["note_path"] = str(note)
        order = {"seed": 0, "backward_reference": 1, "forward_citation": 2}
        papers.sort(
            key=lambda p: (
                order.get(p.get("relation_to_seed") or "", 9),
                -(p.get("year") or 0),
                (p.get("title") or "").lower(),
            )
        )
        return papers
    finally:
        manifest.close()
