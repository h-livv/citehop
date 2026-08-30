"""Runtime configuration for citehop."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CORPORA = Path("/run/media/h-livv/Vault/CiteHop")


def _corpora_dir() -> Path:
    raw = os.environ.get("CITEHOP_CORPORA_DIR")
    path = Path(raw).expanduser() if raw else _DEFAULT_CORPORA
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


CORPORA_DIR = _corpora_dir()
PROJECTS_DIR = Path(os.environ.get("CITEHOP_PROJECTS_DIR", CORPORA_DIR / "_projects"))
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR = Path(os.environ.get("CITEHOP_CONFIG_DIR", Path.home() / ".config" / "citehop"))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONTACT_EMAIL = os.environ.get("CITEHOP_CONTACT_EMAIL", "harliv.research@gmail.com")
USER_AGENT = (
    f"citehop/0.1 (mailto:{CONTACT_EMAIL}; "
    "local 1-hop citation corpus builder)"
)

# Live-verified delays. Sources (fetched 2026-08-30):
# - Semantic Scholar official API overview: unauthenticated traffic shares a
#   1000 rps pool and may be throttled; API-key intro limit is 1 rps.
# - arXiv API ToU: legacy APIs, max one request every three seconds, single
#   connection (https://info.arxiv.org/help/api/tou.html).
# - OpenAlex: mailto polite pool; 0.2s is well under the historical 10 rps cap.
HOST_MIN_INTERVAL_SEC = {
    "api.semanticscholar.org": 2.0,
    "export.arxiv.org": 3.1,
    "arxiv.org": 3.1,
    "api.openalex.org": 0.20,
    "api.crossref.org": 0.25,
    "api.unpaywall.org": 0.25,
}

S2_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or os.environ.get("S2_API_KEY")

MAX_RETRIES = 8
BACKOFF_BASE_SEC = 2.0
BACKOFF_CAP_SEC = 120.0
HTTP_TIMEOUT_SEC = 60.0
DOWNLOAD_TIMEOUT_SEC = 180.0
MAX_PDF_BYTES = 150 * 1024 * 1024
