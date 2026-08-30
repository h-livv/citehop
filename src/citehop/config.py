"""Runtime configuration for CiteHop."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CORPORA = Path("/run/media/h-livv/Vault/CiteHop")


def try_mkdir(path: Path) -> bool:
    """Create *path* if the parent volume is mounted. Never raise."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path.is_dir()
    except OSError:
        return False


def _resolve_dir(raw: str | None, default: Path) -> Path:
    path = Path(raw).expanduser() if raw else default
    try_mkdir(path)
    try:
        return path.resolve()
    except OSError:
        return path


def _corpora_dir() -> Path:
    return _resolve_dir(os.environ.get("CITEHOP_CORPORA_DIR"), _DEFAULT_CORPORA)


CORPORA_DIR = _corpora_dir()
PROJECTS_DIR = _resolve_dir(
    os.environ.get("CITEHOP_PROJECTS_DIR"),
    CORPORA_DIR / "_projects",
)
CONFIG_DIR = Path(os.environ.get("CITEHOP_CONFIG_DIR", Path.home() / ".config" / "citehop"))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def ensure_data_dirs() -> bool:
    """Retry creating corpus/project dirs (e.g. after the Vault is mounted)."""
    ok_c = try_mkdir(CORPORA_DIR)
    ok_p = try_mkdir(PROJECTS_DIR)
    return ok_c and ok_p


def storage_warning() -> str:
    """Empty when corpus storage is usable; otherwise a UI/CLI message."""
    ensure_data_dirs()
    if CORPORA_DIR.is_dir():
        return ""
    return (
        f"Corpus disk is not mounted ({CORPORA_DIR}). "
        "Mount the Vault drive, then Refresh or restart CiteHop."
    )


CONTACT_EMAIL = os.environ.get("CITEHOP_CONTACT_EMAIL", "harliv.research@gmail.com")
USER_AGENT = (
    f"citehop/1.0 (mailto:{CONTACT_EMAIL}; "
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
