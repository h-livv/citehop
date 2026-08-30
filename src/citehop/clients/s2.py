from __future__ import annotations

from typing import Iterator
from urllib.parse import quote, urlencode

from ..http_client import PermanentHttpError, RateLimitedClient
from ..ids import normalize_doi, normalize_s2, s2_external_ids

BASE = "https://api.semanticscholar.org/graph/v1"

PAPER_FIELDS = ",".join(
    [
        "paperId",
        "title",
        "year",
        "venue",
        "publicationVenue",
        "externalIds",
        "authors",
        "abstract",
        "openAccessPdf",
        "citationCount",
        "referenceCount",
        "publicationDate",
    ]
)

REF_FIELDS = ",".join(f"citedPaper.{f}" for f in PAPER_FIELDS.split(","))
CITE_FIELDS = ",".join(f"citingPaper.{f}" for f in PAPER_FIELDS.split(","))


def paper_url_id(*, doi: str | None = None, s2_id: str | None = None, arxiv_id: str | None = None) -> str:
    if s2_id:
        return s2_id
    if doi:
        return f"DOI:{doi}"
    if arxiv_id:
        return f"ARXIV:{arxiv_id}"
    raise ValueError("need an identifier for Semantic Scholar lookup")


def get_paper(client: RateLimitedClient, paper_key: str, paper_id: str | None = None) -> dict | None:
    encoded = quote(paper_key, safe=":")
    url = f"{BASE}/paper/{encoded}?fields={PAPER_FIELDS}"
    try:
        data = client.get_json(url, paper_id=paper_id, action="s2_paper")
    except PermanentHttpError as exc:
        if exc.status_code == 404:
            return None
        raise
    if not isinstance(data, dict) or not data.get("paperId") or data.get("error"):
        return None
    return normalize_s2_paper(data)


def search_best(
    client: RateLimitedClient,
    title: str,
    author: str | None = None,
    paper_id: str | None = "seed",
) -> dict | None:
    query = f"{title} {author}".strip() if author else title
    qs = urlencode({"query": query, "limit": "5", "fields": PAPER_FIELDS})
    url = f"{BASE}/paper/search?{qs}"
    try:
        data = client.get_json(url, paper_id=paper_id, action="s2_search")
    except PermanentHttpError as exc:
        if exc.status_code in (404, 400):
            return None
        raise
    rows = data.get("data") or []
    if not rows:
        return None
    want = "".join(ch for ch in title.lower() if ch.isalnum() or ch.isspace())
    for row in rows:
        rec = normalize_s2_paper(row)
        got = "".join(ch for ch in (rec.get("title") or "").lower() if ch.isalnum() or ch.isspace())
        if want and (want in got or got in want):
            return rec
    return normalize_s2_paper(rows[0])


def get_paper_by_ids(
    client: RateLimitedClient,
    *,
    paper_id: str | None = None,
    s2_id: str | None = None,
    arxiv_id: str | None = None,
    doi: str | None = None,
) -> dict | None:
    keys: list[str] = []
    if s2_id:
        keys.append(s2_id)
    if arxiv_id:
        keys.append(f"ARXIV:{arxiv_id}")
    if doi:
        keys.append(f"DOI:{doi}")
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        rec = get_paper(client, key, paper_id=paper_id)
        if rec:
            return rec
    return None


def iter_references(
    client: RateLimitedClient,
    paper_key: str,
    *,
    seed_id: str,
    limit: int | None = None,
    page_size: int = 100,
) -> Iterator[dict]:
    encoded = quote(paper_key, safe=":")
    yield from _iter_list(
        client,
        f"{BASE}/paper/{encoded}/references",
        fields=REF_FIELDS,
        nested_key="citedPaper",
        seed_id=seed_id,
        action="s2_references",
        limit=limit,
        page_size=page_size,
    )


def iter_citations(
    client: RateLimitedClient,
    paper_key: str,
    *,
    seed_id: str,
    limit: int | None = None,
    page_size: int = 100,
) -> Iterator[dict]:
    encoded = quote(paper_key, safe=":")
    yield from _iter_list(
        client,
        f"{BASE}/paper/{encoded}/citations",
        fields=CITE_FIELDS,
        nested_key="citingPaper",
        seed_id=seed_id,
        action="s2_citations",
        limit=limit,
        page_size=page_size,
    )


def _iter_list(
    client: RateLimitedClient,
    endpoint: str,
    *,
    fields: str,
    nested_key: str,
    seed_id: str,
    action: str,
    limit: int | None,
    page_size: int,
) -> Iterator[dict]:
    offset = 0
    fetched = 0
    page_size = min(page_size, 1000)
    while True:
        remaining = None if limit is None else max(0, limit - fetched)
        if remaining == 0:
            return
        this_limit = page_size if remaining is None else min(page_size, remaining)
        qs = urlencode({"fields": fields, "offset": offset, "limit": this_limit})
        data = client.get_json(f"{endpoint}?{qs}", paper_id=seed_id, action=action)
        rows = data.get("data") or []
        if not rows:
            return
        for row in rows:
            paper = row.get(nested_key) if isinstance(row, dict) else None
            if not paper:
                continue
            yield normalize_s2_paper(paper)
            fetched += 1
            if limit is not None and fetched >= limit:
                return
        nxt = data.get("next")
        if nxt is None:
            return
        offset = int(nxt)


def normalize_s2_paper(paper: dict) -> dict:
    ext = paper.get("externalIds") or {}
    doi, arxiv_id = s2_external_ids(ext)
    authors = []
    for a in paper.get("authors") or []:
        name = (a.get("name") or "").strip()
        if name:
            authors.append(name)
    oa = paper.get("openAccessPdf") or {}
    venue_obj = paper.get("publicationVenue") or {}
    venue = paper.get("venue") or venue_obj.get("name")
    return {
        "semantic_scholar_id": normalize_s2(paper.get("paperId")),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "venue": venue,
        "authors": authors,
        "abstract": paper.get("abstract"),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "open_access_pdf_url": (oa or {}).get("url") if isinstance(oa, dict) else None,
        "citation_count": paper.get("citationCount"),
        "reference_count": paper.get("referenceCount"),
        "external_ids": ext,
    }
