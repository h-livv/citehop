"""Plain-text extraction from arXiv e-prints and PDFs."""

from __future__ import annotations

import gzip
import io
import re
import tarfile
import zipfile
from pathlib import Path

from pylatexenc.latex2text import LatexNodes2Text

import pymupdf

_COMMENT = re.compile(r"(?<!\\)%.*?$", re.M)
_MAIN_HINTS = ("main.tex", "ms.tex", "paper.tex", "article.tex", "manuscript.tex")


def is_pdf_bytes(data: bytes) -> bool:
    return data.startswith(b"%PDF")


def is_html_bytes(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def extract_pdf_text(data: bytes) -> str:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        parts = []
        for page in doc:
            parts.append(page.get_text("text"))
        return "\n".join(parts)
    finally:
        doc.close()


_DOI_FIND = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
_ARXIV_NEW = re.compile(
    r"(?:arxiv:|arxiv\.org/(?:abs|pdf)/)\s*(\d{4}\.\d{4,5})(?:v\d+)?",
    re.I,
)
_ARXIV_OLD = re.compile(
    r"(?:arxiv:|arxiv\.org/(?:abs|pdf)/)\s*([a-z\-]+/\d{7})(?:v\d+)?",
    re.I,
)
_DOI_URL = re.compile(r"(?:doi\.org/|doi:)\s*(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.I)


def _clean_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    doi = raw.strip().rstrip(").,;]}>")
    doi = doi.split()[0] if doi else ""
    if not re.match(r"^10\.\d{4,9}/.+$", doi, re.I):
        return None
    return doi.lower()


def _first_arxiv(blob: str) -> str | None:
    m = _ARXIV_NEW.search(blob) or _ARXIV_OLD.search(blob)
    return m.group(1) if m else None


def _title_from_page(page) -> str | None:  # noqa: ANN001
    data = page.get_text("dict")
    candidates: list[tuple[float, str]] = []
    for block in data.get("blocks", []):
        for line in block.get("lines", []) if isinstance(block, dict) else []:
            spans = line.get("spans") or []
            if not spans:
                continue
            text = "".join(str(s.get("text") or "") for s in spans).strip()
            text = re.sub(r"\s+", " ", text)
            if len(text) < 12 or len(text) > 280:
                continue
            low = text.lower()
            if low.startswith("arxiv:") or low.startswith("doi:"):
                continue
            if low in {"abstract", "introduction", "references"}:
                continue
            size = max((float(s.get("size") or 0) for s in spans), default=0.0)
            candidates.append((size, text))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return candidates[0][1]


def _author_family(author: str | None) -> str | None:
    if not author:
        return None
    first = re.split(r"[,;&]|\band\b", author, maxsplit=1, flags=re.I)[0].strip()
    if not first:
        return None
    parts = [p for p in re.split(r"\s+", first) if p and p.lower() not in {"and"}]
    if not parts:
        return None
    particles = {"di", "de", "del", "della", "van", "von", "da", "dos", "das"}
    if len(parts) >= 2 and parts[0].lower() in particles:
        return f"{parts[0]} {parts[1]}".strip(".,")
    family = parts[-1].strip(".,")
    return family or None


def inspect_pdf(path: Path) -> dict[str, str | int | None]:
    """Pull DOI, arXiv id, title, and author from a local PDF. No network."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    doc = pymupdf.open(path)
    try:
        meta = doc.metadata or {}
        blobs = [
            str(meta.get("title") or ""),
            str(meta.get("subject") or ""),
            str(meta.get("keywords") or ""),
            str(meta.get("author") or ""),
        ]
        links: list[str] = []
        title_from_layout: str | None = None
        for i, page in enumerate(doc):
            if i >= 3:
                break
            blobs.append(page.get_text("text") or "")
            if title_from_layout is None:
                title_from_layout = _title_from_page(page)
            for link in page.get_links() or []:
                uri = str(link.get("uri") or "")
                if uri:
                    links.append(uri)
        blob = "\n".join(blobs + links)
        doi = None
        for match in _DOI_URL.finditer(blob):
            doi = _clean_doi(match.group(1))
            if doi:
                break
        if not doi:
            found = _DOI_FIND.search(blob)
            doi = _clean_doi(found.group(0) if found else None)
        arxiv_id = _first_arxiv(blob)
        title = (meta.get("title") or "").strip() or None
        if title and title.lower() in {"untitled", "microsoft word", "title"}:
            title = None
        if not title:
            title = title_from_layout
        author = (meta.get("author") or "").strip() or None
        year = None
        created = str(meta.get("creationDate") or "")
        year_match = re.search(r"(19|20)\d{2}", created)
        if year_match:
            year = int(year_match.group(0))
        return {
            "path": str(path),
            "title": title,
            "author": author,
            "author_family": _author_family(author),
            "doi": doi,
            "arxiv_id": arxiv_id,
            "year": year,
        }
    finally:
        doc.close()


def unpack_eprint(data: bytes) -> tuple[str, bytes | str]:
    """Return ('pdf', bytes), ('tex', str), or ('unknown', bytes)."""
    if is_pdf_bytes(data):
        return "pdf", data
    if is_html_bytes(data):
        return "unknown", data

    try:
        return "tex", _tex_from_tar(io.BytesIO(data))
    except tarfile.TarError:
        pass

    if zipfile.is_zipfile(io.BytesIO(data)):
        return "tex", _tex_from_zip(data)

    # gzip of a single file (tex or tar or pdf)
    try:
        inner = gzip.decompress(data)
    except OSError:
        inner = None
    if inner is not None:
        if is_pdf_bytes(inner):
            return "pdf", inner
        bio = io.BytesIO(inner)
        if tarfile.is_tarfile(bio):
            bio.seek(0)
            return "tex", _tex_from_tar(bio)
        text = _decode(inner)
        if "\\" in text[:2000] or "\\documentclass" in text:
            return "tex", text
        return "unknown", inner

    text = _decode(data)
    if "\\documentclass" in text or "\\begin{document}" in text:
        return "tex", text
    return "unknown", data


def _tex_from_tar(bio: io.BytesIO) -> str:
    with tarfile.open(fileobj=bio, mode="r:*") as tar:
        members = [
            m
            for m in tar.getmembers()
            if m.isfile() and m.name.lower().endswith(".tex") and not _skip_member(m.name)
        ]
        ordered = _order_tex_members([m.name for m in members])
        chunks = []
        by_name = {m.name: m for m in members}
        for name in ordered:
            extracted = tar.extractfile(by_name[name])
            if extracted is None:
                continue
            chunks.append(f"% --- {name} ---\n{_decode(extracted.read())}")
        return "\n\n".join(chunks)


def _tex_from_zip(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [
            n
            for n in zf.namelist()
            if n.lower().endswith(".tex") and not _skip_member(n)
        ]
        ordered = _order_tex_members(names)
        chunks = []
        for name in ordered:
            chunks.append(f"% --- {name} ---\n{_decode(zf.read(name))}")
        return "\n\n".join(chunks)


def _skip_member(name: str) -> bool:
    lower = name.lower()
    return (
        "/." in f"/{lower}"
        or lower.endswith(".sty.tex")
        or "tikz" in Path(lower).name
    )


def _order_tex_members(names: list[str]) -> list[str]:
    def score(name: str) -> tuple[int, str]:
        base = Path(name).name.lower()
        if base in _MAIN_HINTS:
            return (0, name)
        return (1, name)

    return sorted(names, key=score)


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def latex_to_text(tex: str) -> str:
    stripped = _COMMENT.sub("", tex)
    if len(stripped) > 2_000_000:
        return stripped
    try:
        converter = LatexNodes2Text(keep_comments=False)
        return converter.latex_to_text(stripped)
    except Exception:
        return stripped


def extract_eprint_text(data: bytes) -> tuple[str, str | None, bytes | None]:
    """Returns (fetch_method_hint, text, pdf_bytes_if_any).

    fetch_method_hint is 'arxiv_latex', 'arxiv_pdf', or 'failed'.
    """
    kind, payload = unpack_eprint(data)
    if kind == "pdf" and isinstance(payload, bytes):
        text = extract_pdf_text(payload)
        return "arxiv_pdf", text, payload
    if kind == "tex" and isinstance(payload, str) and payload.strip():
        return "arxiv_latex", latex_to_text(payload), None
    return "failed", None, None
