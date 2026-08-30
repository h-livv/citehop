from __future__ import annotations

from urllib.parse import quote

from ..config import CONTACT_EMAIL
from ..http_client import PermanentHttpError, RateLimitedClient
from ..ids import normalize_doi


def lookup(client: RateLimitedClient, doi: str, paper_id: str) -> dict | None:
    doi_n = normalize_doi(doi)
    if not doi_n:
        return None
    url = f"https://api.unpaywall.org/v2/{quote(doi_n)}?email={quote(CONTACT_EMAIL)}"
    try:
        data = client.get_json(url, paper_id=paper_id, action="unpaywall")
    except PermanentHttpError as exc:
        if exc.status_code in (404, 422):
            return None
        raise
    best = data.get("best_oa_location") or {}
    return {
        "is_oa": data.get("is_oa"),
        "oa_status": data.get("oa_status"),
        "pdf_url": best.get("url_for_pdf") or best.get("url"),
        "best_oa_location": best,
    }
