from __future__ import annotations

from typing import Iterator
from urllib.parse import quote, urlencode

from ..config import CONTACT_EMAIL
from ..http_client import PermanentHttpError, RateLimitedClient
from ..ids import normalize_arxiv, normalize_doi, normalize_openalex


def _params(**extra: str) -> str:
    extra = {k: v for k, v in extra.items() if v is not None}
    extra["mailto"] = CONTACT_EMAIL
    return urlencode(extra)


def get_by_doi(client: RateLimitedClient, doi: str, paper_id: str | None = None) -> dict | None:
    doi_n = normalize_doi(doi)
    if not doi_n:
        return None
    url = f"https://api.openalex.org/works/doi:{quote(doi_n)}?{_params()}"
    try:
        data = client.get_json(url, paper_id=paper_id, action="openalex_doi")
    except PermanentHttpError as exc:
        if exc.status_code == 404:
            return None
        raise
    return normalize_work(data)


def get_by_id(client: RateLimitedClient, openalex_id: str, paper_id: str | None = None) -> dict | None:
    oid = normalize_openalex(openalex_id)
    if not oid:
        return None
    url = f"https://api.openalex.org/works/{oid}?{_params()}"
    try:
        data = client.get_json(url, paper_id=paper_id, action="openalex_id")
    except PermanentHttpError as exc:
        if exc.status_code == 404:
            return None
        raise
    return normalize_work(data)


def iter_cited_by(
    client: RateLimitedClient,
    openalex_id: str,
    *,
    seed_id: str,
    limit: int | None = None,
) -> Iterator[dict]:
    oid = normalize_openalex(openalex_id)
    cursor = "*"
    fetched = 0
    per_page = 50 if limit is not None else 200
    while cursor:
        if limit is not None and fetched >= limit:
            return
        if limit is not None:
            per_page = min(per_page, limit - fetched)
        qs = _params(filter=f"cites:{oid}", per_page=str(per_page), cursor=cursor)
        url = f"https://api.openalex.org/works?{qs}"
        data = client.get_json(url, paper_id=seed_id, action="openalex_citations")
        for work in data.get("results") or []:
            yield normalize_work(work)
            fetched += 1
            if limit is not None and fetched >= limit:
                return
        cursor = (data.get("meta") or {}).get("next_cursor")


def normalize_work(work: dict) -> dict:
    ids = work.get("ids") or {}
    authorships = work.get("authorships") or []
    authors = []
    for a in authorships:
        name = ((a.get("author") or {}).get("display_name") or "").strip()
        if name:
            authors.append(name)
    pl = work.get("primary_location") or {}
    source = pl.get("source") or {}
    oa = work.get("open_access") or {}
    ext_arxiv = None
    loc_landing = pl.get("landing_page_url") or ""
    if "arxiv.org" in loc_landing:
        ext_arxiv = normalize_arxiv(loc_landing)
    # OpenAlex sometimes puts arXiv in ids.arxiv or locations
    if ids.get("arxiv"):
        ext_arxiv = normalize_arxiv(ids["arxiv"])
    pdf_url = None
    if pl.get("pdf_url"):
        pdf_url = pl["pdf_url"]
    elif oa.get("oa_url"):
        pdf_url = oa["oa_url"]
    return {
        "openalex_id": normalize_openalex(work.get("id") or ids.get("openalex")),
        "doi": normalize_doi(work.get("doi") or ids.get("doi")),
        "arxiv_id": ext_arxiv,
        "title": work.get("title") or work.get("display_name"),
        "year": work.get("publication_year"),
        "venue": source.get("display_name"),
        "authors": authors,
        "oa_url": oa.get("oa_url"),
        "oa_status": oa.get("oa_status"),
        "is_oa": oa.get("is_oa"),
        "pdf_url": pdf_url,
        "cited_by_count": work.get("cited_by_count"),
        "referenced_works_count": work.get("referenced_works_count"),
        "referenced_works": work.get("referenced_works") or [],
        "ids": ids,
    }
