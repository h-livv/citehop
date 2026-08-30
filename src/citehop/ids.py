"""Canonical identifier normalization and filesystem-safe slugs.

Priority for canonical_id: DOI > arXiv ID > Semantic Scholar ID > OpenAlex ID.
"""

from __future__ import annotations

import re
from typing import Any

_DOI_PREFIX = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:)", re.I)
_ARXIV_PREFIX = re.compile(r"^(arxiv:|https?://arxiv\.org/abs/)", re.I)
_ARXIV_VERSION = re.compile(r"v\d+$", re.I)
_OLD_ARXIV = re.compile(r"^([a-z-]+)/(\d+)$", re.I)
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = _DOI_PREFIX.sub("", value.strip()).strip().strip("/")
    doi = doi.lower()
    return doi or None


def normalize_arxiv(value: str | None) -> str | None:
    if not value:
        return None
    aid = _ARXIV_PREFIX.sub("", value.strip())
    aid = aid.split("?")[0].strip().strip("/")
    aid = _ARXIV_VERSION.sub("", aid)
    return aid or None


def normalize_openalex(value: str | None) -> str | None:
    if not value:
        return None
    vid = value.strip()
    if "/" in vid:
        vid = vid.rstrip("/").rsplit("/", 1)[-1]
    vid = vid.upper()
    if vid.startswith("W") and vid[1:].isdigit():
        return vid
    return None


def normalize_s2(value: str | None) -> str | None:
    if not value:
        return None
    vid = value.strip().lower()
    return vid or None


def canonical_id(
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    s2_id: str | None = None,
    openalex_id: str | None = None,
) -> str | None:
    doi_n = normalize_doi(doi)
    if doi_n:
        return doi_n
    arxiv_n = normalize_arxiv(arxiv_id)
    if arxiv_n:
        return f"arxiv:{arxiv_n}"
    s2_n = normalize_s2(s2_id)
    if s2_n:
        return f"s2:{s2_n}"
    oa_n = normalize_openalex(openalex_id)
    if oa_n:
        return f"openalex:{oa_n}"
    return None


def file_id(canonical: str) -> str:
    slug = _SAFE.sub("_", canonical).strip("._")
    if len(slug) > 180:
        slug = slug[:180]
    return slug or "unknown"


def s2_external_ids(external: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Return (doi, arxiv_id) from a Semantic Scholar externalIds object."""
    if not external:
        return None, None
    doi = normalize_doi(external.get("DOI") or external.get("doi"))
    arxiv = normalize_arxiv(
        external.get("ArXiv") or external.get("ARXIV") or external.get("arxiv")
    )
    return doi, arxiv
