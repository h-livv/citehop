from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote

from ..http_client import RateLimitedClient
from ..ids import normalize_arxiv, normalize_doi

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return " ".join(el.text.split()) or None


def _query(client: RateLimitedClient, search_query: str, *, action: str) -> dict | None:
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query={quote(search_query)}&start=0&max_results=5"
    )
    resp = client.get(url, paper_id="seed", action=action)
    root = ET.fromstring(resp.content)
    entries = root.findall(f"{ATOM}entry")
    if not entries:
        return None
    return _parse_entry(entries[0])


def search_by_title_author(client: RateLimitedClient, title: str, author: str) -> dict | None:
    return _query(client, f'ti:"{title}" AND au:"{author}"', action="arxiv_search")


def search_by_title(client: RateLimitedClient, title: str) -> dict | None:
    return _query(client, f'ti:"{title}"', action="arxiv_search_title")


def search_by_doi(client: RateLimitedClient, doi: str) -> dict | None:
    doi_n = normalize_doi(doi)
    if not doi_n:
        return None
    return _query(client, f"doi:{doi_n}", action="arxiv_search_doi")


def get_by_id(client: RateLimitedClient, arxiv_id: str, paper_id: str | None = None) -> dict | None:
    aid = normalize_arxiv(arxiv_id)
    url = f"https://export.arxiv.org/api/query?id_list={quote(aid or arxiv_id)}&max_results=1"
    resp = client.get(url, paper_id=paper_id, action="arxiv_id_lookup")
    root = ET.fromstring(resp.content)
    entries = root.findall(f"{ATOM}entry")
    if not entries:
        return None
    return _parse_entry(entries[0])


def _parse_entry(entry: ET.Element) -> dict:
    abs_id = _text(entry.find(f"{ATOM}id")) or ""
    arxiv_id = normalize_arxiv(abs_id)
    authors = []
    for a in entry.findall(f"{ATOM}author"):
        name = _text(a.find(f"{ATOM}name"))
        if name:
            authors.append(name)
    doi = normalize_doi(_text(entry.find(f"{ARXIV}doi")))
    return {
        "arxiv_id": arxiv_id,
        "title": _text(entry.find(f"{ATOM}title")),
        "abstract": _text(entry.find(f"{ATOM}summary")),
        "authors": authors,
        "published": _text(entry.find(f"{ATOM}published")),
        "updated": _text(entry.find(f"{ATOM}updated")),
        "journal_ref": _text(entry.find(f"{ARXIV}journal_ref")),
        "doi": doi,
        "pdf_url": f"https://export.arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else None,
        "eprint_url": f"https://export.arxiv.org/e-print/{arxiv_id}" if arxiv_id else None,
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None,
    }


def fetch_eprint(client: RateLimitedClient, arxiv_id: str, paper_id: str) -> tuple[bytes, str]:
    aid = normalize_arxiv(arxiv_id) or arxiv_id
    url = f"https://export.arxiv.org/e-print/{aid}"
    resp = client.download(url, paper_id=paper_id, action="arxiv_eprint")
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    return resp.content, ctype


def fetch_pdf(client: RateLimitedClient, arxiv_id: str, paper_id: str) -> bytes:
    aid = normalize_arxiv(arxiv_id) or arxiv_id
    url = f"https://export.arxiv.org/pdf/{aid}.pdf"
    resp = client.download(url, paper_id=paper_id, action="arxiv_pdf")
    return resp.content
