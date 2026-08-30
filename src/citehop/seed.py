"""Identify a seed paper from DOI, arXiv id, title, or a named preset."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .clients import arxiv as arxiv_api
from .clients import crossref as crossref_api
from .clients import openalex as openalex_api
from .clients import s2 as s2_api
from .config import CORPORA_DIR
from .extract import inspect_pdf
from .http_client import RateLimitedClient
from .ids import canonical_id, file_id, normalize_arxiv, normalize_doi

_TITLE_KEEP = re.compile(r"[^a-z0-9]+")


def norm_title(value: str | None) -> str:
    if not value:
        return ""
    return _TITLE_KEEP.sub(" ", value.lower()).strip()


def titles_match(a: str | None, b: str | None) -> bool:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def has_author(authors: list[str] | None, family: str | None) -> bool:
    if not family:
        return True
    needle = family.lower()
    return any(needle in (a or "").lower() for a in (authors or []))


@dataclass
class SeedQuery:
    doi: str | None = None
    arxiv_id: str | None = None
    title: str | None = None
    author: str | None = None
    venue: str | None = None
    year: int | None = None
    pdf: Path | None = None
    preset: str | None = None

    def normalized(self) -> SeedQuery:
        pdf = self.pdf.expanduser().resolve() if self.pdf else None
        return replace(
            self,
            doi=normalize_doi(self.doi),
            arxiv_id=normalize_arxiv(self.arxiv_id),
            title=self.title.strip() if self.title else None,
            author=self.author.strip() if self.author else None,
            venue=self.venue.strip() if self.venue else None,
            pdf=pdf,
        )

    def fingerprint(self) -> str:
        q = self.normalized()
        if q.preset:
            return f"preset:{q.preset}"
        if q.doi:
            return f"doi:{q.doi}"
        if q.arxiv_id:
            return f"arxiv:{q.arxiv_id}"
        if q.title:
            return f"title:{norm_title(q.title)}"
        return "unknown"

    def slug(self) -> str:
        q = self.normalized()
        if q.preset:
            return q.preset
        if q.doi:
            return file_id(q.doi)
        if q.arxiv_id:
            return file_id(f"arxiv:{q.arxiv_id}")
        if q.title:
            return file_id("title_" + norm_title(q.title).replace(" ", "_"))[:80]
        return "unnamed"

    def default_corpus_dir(self) -> Path:
        return CORPORA_DIR / self.slug()


PRESETS: dict[str, SeedQuery] = {
    "qc4hep": SeedQuery(
        title="Quantum Computing for High-Energy Physics: State of the Art and Challenges",
        author="Di Meglio",
        venue="PRX Quantum",
        year=2024,
        pdf=Path("/home/h-livv/Library/Papers/QCHEP/PRXQuantum.5.037001.pdf"),
        preset="qc4hep",
    ),
}


def query_from_pdf(path: Path) -> SeedQuery:
    """Build a seed query from a local PDF's identifiers. Live APIs run later."""
    info = inspect_pdf(path)
    q = SeedQuery(
        doi=info.get("doi") if isinstance(info.get("doi"), str) else None,
        arxiv_id=info.get("arxiv_id") if isinstance(info.get("arxiv_id"), str) else None,
        title=info.get("title") if isinstance(info.get("title"), str) else None,
        author=info.get("author_family") if isinstance(info.get("author_family"), str) else None,
        year=info.get("year") if isinstance(info.get("year"), int) else None,
        pdf=Path(path),
    ).normalized()
    if not (q.doi or q.arxiv_id or q.title):
        raise ValueError(
            "Could not read a DOI, arXiv id, or title from this PDF. "
            "Enter identifiers manually, then start analysis."
        )
    return q


def query_from_args(args: Any) -> SeedQuery:
    preset = getattr(args, "preset", None)
    if preset:
        if preset not in PRESETS:
            raise SystemExit(f"Unknown preset {preset!r}. Known: {', '.join(sorted(PRESETS))}")
        base = PRESETS[preset]
        return replace(
            base,
            doi=getattr(args, "doi", None) or base.doi,
            arxiv_id=getattr(args, "arxiv", None) or base.arxiv_id,
            title=getattr(args, "title", None) or base.title,
            author=getattr(args, "author", None) or base.author,
            venue=getattr(args, "venue", None) or base.venue,
            year=getattr(args, "year", None) or base.year,
            pdf=getattr(args, "pdf", None) or base.pdf,
            preset=preset,
        ).normalized()
    q = SeedQuery(
        doi=getattr(args, "doi", None),
        arxiv_id=getattr(args, "arxiv", None),
        title=getattr(args, "title", None),
        author=getattr(args, "author", None),
        venue=getattr(args, "venue", None),
        year=getattr(args, "year", None),
        pdf=getattr(args, "pdf", None),
    ).normalized()
    if not (q.doi or q.arxiv_id or q.title):
        raise SystemExit(
            "Provide a seed paper: --doi, --arxiv, --title [--author], or --preset qc4hep"
        )
    return q


def resolve(client: RateLimitedClient, query: SeedQuery) -> dict[str, Any]:
    """Live-resolve identifiers. Does not use remembered paper content."""
    q = query.normalized()
    arxiv_hit: dict | None = None
    xref_hit: dict | None = None
    s2_paper: dict | None = None
    oa_work: dict | None = None

    if q.arxiv_id:
        arxiv_hit = arxiv_api.get_by_id(client, q.arxiv_id, paper_id="seed")
        if not arxiv_hit:
            raise SystemExit(f"arXiv id not found: {q.arxiv_id}")
        q = replace(q, doi=q.doi or arxiv_hit.get("doi"), title=q.title or arxiv_hit.get("title"))

    if q.doi:
        xref_hit = crossref_api.get_by_doi(client, q.doi, paper_id="seed")
        if not arxiv_hit:
            arxiv_hit = arxiv_api.search_by_doi(client, q.doi)
        oa_work = openalex_api.get_by_doi(client, q.doi, paper_id="seed")
        s2_paper = s2_api.get_paper_by_ids(
            client,
            paper_id="seed",
            arxiv_id=(arxiv_hit or {}).get("arxiv_id") or q.arxiv_id,
            doi=q.doi,
        )

    if not q.doi and not q.arxiv_id and q.title:
        if q.author:
            arxiv_hit = arxiv_hit or arxiv_api.search_by_title_author(client, q.title, q.author)
        else:
            arxiv_hit = arxiv_hit or arxiv_api.search_by_title(client, q.title)
        xref_hit = xref_hit or crossref_api.search(
            client, title=q.title, author=q.author, venue=q.venue
        )
        s2_paper = s2_paper or s2_api.search_best(client, q.title, author=q.author)
        follow_doi = (arxiv_hit or {}).get("doi") or (xref_hit or {}).get("doi")
        if follow_doi and not xref_hit:
            xref_hit = crossref_api.get_by_doi(client, follow_doi, paper_id="seed")

    if not s2_paper:
        s2_paper = s2_api.get_paper_by_ids(
            client,
            paper_id="seed",
            arxiv_id=(arxiv_hit or {}).get("arxiv_id") or q.arxiv_id,
            doi=(xref_hit or {}).get("doi") or q.doi,
        )
    if not oa_work:
        doi_for_oa = (xref_hit or {}).get("doi") or (arxiv_hit or {}).get("doi") or q.doi
        if doi_for_oa:
            oa_work = openalex_api.get_by_doi(client, doi_for_oa, paper_id="seed")

    title = _coalesce(
        (xref_hit or {}).get("title"),
        (arxiv_hit or {}).get("title"),
        (s2_paper or {}).get("title"),
        (oa_work or {}).get("title"),
        q.title,
    )
    authors = _prefer_authors(
        (arxiv_hit or {}).get("authors"),
        (xref_hit or {}).get("authors"),
    ) or (s2_paper or {}).get("authors") or (oa_work or {}).get("authors")
    year = _coalesce(
        (xref_hit or {}).get("year"),
        (oa_work or {}).get("year"),
        (s2_paper or {}).get("year"),
        q.year,
    )
    venue = _coalesce(
        (xref_hit or {}).get("venue"),
        (oa_work or {}).get("venue"),
        (s2_paper or {}).get("venue"),
        q.venue,
    )
    doi = _coalesce(
        q.doi,
        (xref_hit or {}).get("doi"),
        (arxiv_hit or {}).get("doi"),
        (s2_paper or {}).get("doi"),
        (oa_work or {}).get("doi"),
    )
    arxiv_id = _coalesce(
        q.arxiv_id,
        (arxiv_hit or {}).get("arxiv_id"),
        (s2_paper or {}).get("arxiv_id"),
        (oa_work or {}).get("arxiv_id"),
    )

    errors = _confirm(q, title=title, authors=authors or [], year=year, venue=venue, arxiv_hit=arxiv_hit)
    if errors:
        raise SystemExit("Seed confirmation failed:\n- " + "\n- ".join(errors))
    if not title:
        raise SystemExit("Seed resolution failed: no title from live APIs")
    cid = canonical_id(
        doi=doi,
        arxiv_id=arxiv_id,
        s2_id=(s2_paper or {}).get("semantic_scholar_id"),
        openalex_id=(oa_work or {}).get("openalex_id"),
    )
    if not cid:
        raise SystemExit("Seed has no canonical identifier after live lookup")

    return {
        "canonical_id": cid,
        "title": title,
        "authors": authors or [],
        "year": year,
        "venue": venue,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "semantic_scholar_id": (s2_paper or {}).get("semantic_scholar_id"),
        "openalex_id": (oa_work or {}).get("openalex_id"),
        "abstract": _coalesce(
            (arxiv_hit or {}).get("abstract"),
            (s2_paper or {}).get("abstract"),
        ),
        "arxiv_hit": arxiv_hit,
        "xref_hit": xref_hit,
        "s2_paper": s2_paper,
        "oa_work": oa_work,
        "query": q,
    }


def _confirm(
    q: SeedQuery,
    *,
    title: str | None,
    authors: list[str],
    year: int | None,
    venue: str | None,
    arxiv_hit: dict | None,
) -> list[str]:
    errors: list[str] = []
    if q.title and not titles_match(title, q.title):
        errors.append(f"title mismatch: got {title!r}, expected {q.title!r}")
    if q.author and not has_author(authors, q.author):
        errors.append(f"author {q.author!r} not in {authors[:8]!r}")
    if q.venue:
        blob = " ".join(
            x for x in (venue, (arxiv_hit or {}).get("journal_ref"), title) if x
        ).lower()
        if q.venue.lower() not in blob:
            errors.append(f"venue mismatch: got {venue!r}, expected {q.venue!r}")
    if q.year and year and int(year) != int(q.year):
        journal_ref = (arxiv_hit or {}).get("journal_ref") or ""
        if str(q.year) not in str(year) and str(q.year) not in journal_ref:
            errors.append(f"year mismatch: got {year!r}, expected {q.year!r}")
    return errors


def _coalesce(*vals: Any) -> Any:
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None


def _prefer_authors(old: list[str] | None, new: list[str] | None) -> list[str] | None:
    old = old or []
    new = new or []
    if not old:
        return new or None
    if not new:
        return old
    old0 = old[0] if old else ""
    new0 = new[0] if new else ""
    if len(new0) > len(old0) + 3:
        return new
    if len(old) >= len(new):
        return old
    return new
