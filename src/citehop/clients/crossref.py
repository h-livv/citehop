from __future__ import annotations

from urllib.parse import quote, urlencode

from ..config import CONTACT_EMAIL
from ..http_client import PermanentHttpError, RateLimitedClient
from ..ids import normalize_doi


def search(
    client: RateLimitedClient,
    title: str,
    author: str | None = None,
    venue: str | None = None,
) -> dict | None:
    params = {
        "query.bibliographic": title,
        "rows": "5",
        "mailto": CONTACT_EMAIL,
    }
    if author:
        params["query.author"] = author
    if venue:
        params["query.container-title"] = venue
    url = "https://api.crossref.org/works?" + urlencode(params)
    data = client.get_json(url, paper_id="seed", action="crossref_search")
    items = (data.get("message") or {}).get("items") or []
    if not items:
        return None
    title_l = title.lower()
    for item in items:
        parsed = _parse_work(item)
        got = (parsed.get("title") or "").lower()
        if title_l in got or got in title_l:
            return parsed
    return _parse_work(items[0])


def search_title_author_venue(
    client: RateLimitedClient,
    title: str,
    author: str,
    venue: str,
) -> dict | None:
    return search(client, title, author=author, venue=venue)


def get_by_doi(client: RateLimitedClient, doi: str, paper_id: str | None = None) -> dict | None:
    doi_n = normalize_doi(doi)
    if not doi_n:
        return None
    url = f"https://api.crossref.org/works/{quote(doi_n)}?mailto={quote(CONTACT_EMAIL)}"
    try:
        data = client.get_json(url, paper_id=paper_id, action="crossref_doi")
    except PermanentHttpError as exc:
        if exc.status_code == 404:
            return None
        raise
    msg = data.get("message") or {}
    return _parse_work(msg)


def _parse_work(item: dict) -> dict:
    title_list = item.get("title") or []
    container = item.get("container-title") or []
    authors = []
    for a in item.get("author") or []:
        given = (a.get("given") or "").strip()
        family = (a.get("family") or "").strip()
        name = " ".join(p for p in (given, family) if p)
        if name:
            authors.append(name)
    issued = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    year = issued[0] if issued else None
    return {
        "doi": normalize_doi(item.get("DOI")),
        "title": title_list[0] if title_list else None,
        "venue": container[0] if container else None,
        "authors": authors,
        "year": year,
        "volume": item.get("volume"),
        "issue": item.get("issue"),
        "page": item.get("page"),
        "type": item.get("type"),
        "issued": item.get("issued"),
        "ISSN": item.get("ISSN"),
    }
