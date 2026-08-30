"""CiteHop — resumable 1-hop citation corpus builder for any seed paper."""

from pathlib import Path

__version__ = "0.1.0"


def icon_path() -> Path:
    return Path(__file__).resolve().parent / "assets" / "citehop.svg"
