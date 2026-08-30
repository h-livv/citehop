"""Write corpus JSON artifacts and the full-run validation README."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .store import Manifest

RELATION_LABELS = {
    "seed": "Seed",
    "backward_reference": "Cited by seed",
    "forward_citation": "Cites seed",
}
RELATION_SLUG = {
    "seed": "seed",
    "backward_reference": "cited-by-seed",
    "forward_citation": "cites-seed",
}


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def row_to_metadata(row) -> dict[str, Any]:
    authors = []
    if row["authors_json"]:
        authors = json.loads(row["authors_json"])
    extra = json.loads(row["metadata_json"] or "{}")
    record = {
        "canonical_id": row["canonical_id"],
        "title": row["title"],
        "authors": authors,
        "year": row["year"],
        "venue": row["venue"],
        "arxiv_id": row["arxiv_id"],
        "doi": row["doi"],
        "semantic_scholar_id": row["semantic_scholar_id"],
        "openalex_id": row["openalex_id"],
        "relation_to_seed": row["relation_to_seed"],
        "full_text_available": bool(row["full_text_available"]),
        "fetch_method": row["fetch_method"],
        "source_url": row["source_url"],
        "fetch_timestamp": row["fetch_timestamp"],
        "fetch_status": row["fetch_status"],
        "failure_reason": row["failure_reason"],
        "abstract": row["abstract"],
        "file_id": row["file_id"],
        "manifest_status": row["status"],
    }
    if extra:
        record["live_extras"] = extra
    return record


def write_paper_metadata(corpus_dir: Path, row) -> None:
    meta = row_to_metadata(row)
    path = corpus_dir / "metadata" / f"{row['file_id']}.json"
    atomic_write_text(path, json.dumps(meta, indent=2, ensure_ascii=False) + "\n")


def _yaml_quote(value: Any) -> str:
    text = "" if value is None else str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_paper_note(corpus_dir: Path, meta: dict[str, Any]) -> Path:
    """Obsidian-readable note with frontmatter, identifiers, and abstract."""
    fid = meta.get("file_id") or "unknown"
    authors = meta.get("authors") or []
    if not isinstance(authors, list):
        authors = [str(authors)]
    relation = meta.get("relation_to_seed") or ""
    lines = [
        "---",
        f"title: {_yaml_quote(meta.get('title'))}",
        f"year: {meta.get('year') or ''}".rstrip(),
        f"doi: {_yaml_quote(meta.get('doi'))}",
        f"arxiv: {_yaml_quote(meta.get('arxiv_id'))}",
        f"venue: {_yaml_quote(meta.get('venue'))}",
        f"relation: {RELATION_SLUG.get(relation, relation or 'unknown')}",
        f"canonical_id: {_yaml_quote(meta.get('canonical_id'))}",
        "authors:",
    ]
    if authors:
        lines.extend(f"  - {_yaml_quote(a)}" for a in authors)
    else:
        lines.append("  []")
    lines += [
        "---",
        "",
        f"# {meta.get('title') or '(no title)'}",
        "",
    ]
    if authors:
        lines.append(", ".join(str(a) for a in authors))
        lines.append("")
    venue_year = " · ".join(str(x) for x in (meta.get("venue"), meta.get("year")) if x)
    if venue_year:
        lines.append(venue_year)
        lines.append("")
    lines += [
        f"- Relation: {RELATION_LABELS.get(relation, relation or '—')}",
        f"- DOI: {meta.get('doi') or '—'}",
        f"- arXiv: {meta.get('arxiv_id') or '—'}",
        f"- OpenAlex: {meta.get('openalex_id') or '—'}",
        f"- Semantic Scholar: {meta.get('semantic_scholar_id') or '—'}",
        "",
    ]
    abstract = (meta.get("abstract") or "").strip()
    if abstract:
        lines += ["## Abstract", "", abstract, ""]
    path = corpus_dir / "papers" / f"{fid}.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def _catalog_markdown(corpus_dir: Path, papers: list[dict[str, Any]]) -> str:
    seed = next((p for p in papers if p.get("relation_to_seed") == "seed"), None)
    title = (seed or {}).get("title") or corpus_dir.name
    rel_counts: dict[str, int] = {}
    for paper in papers:
        key = paper.get("relation_to_seed") or "other"
        rel_counts[key] = rel_counts.get(key, 0) + 1
    lines = [
        f"# {title}",
        "",
        f"- Corpus: `{corpus_dir}`",
        f"- Papers: **{len(papers)}**",
        f"  - seed: {rel_counts.get('seed', 0)}",
        f"  - cited by seed: {rel_counts.get('backward_reference', 0)}",
        f"  - citing seed: {rel_counts.get('forward_citation', 0)}",
        "",
    ]
    if seed:
        authors = seed.get("authors") or []
        author_line = ", ".join(str(a) for a in authors) if isinstance(authors, list) else str(authors)
        lines += [
            "## Seed",
            "",
            f"**{seed.get('title')}**",
            "",
            author_line,
            "",
            f"{seed.get('venue') or ''} ({seed.get('year') or ''})".strip(),
            "",
            f"- DOI: `{seed.get('doi') or '—'}`",
            f"- arXiv: `{seed.get('arxiv_id') or '—'}`",
            "",
        ]
        abstract = (seed.get("abstract") or "").strip()
        if abstract:
            lines += [abstract, ""]
        fid = seed.get("file_id")
        if fid:
            lines += [f"Note: [[{fid}|open seed note]]", ""]

    order = ("backward_reference", "forward_citation")
    for rel in order:
        group = [p for p in papers if p.get("relation_to_seed") == rel]
        group.sort(key=lambda p: (-(p.get("year") or 0), (p.get("title") or "").lower()))
        lines += [f"## {RELATION_LABELS.get(rel, rel)} ({len(group)})", ""]
        for paper in group:
            year = paper.get("year") or ""
            authors = paper.get("authors") or []
            author_line = ", ".join(str(a) for a in authors[:8]) if isinstance(authors, list) else ""
            if isinstance(authors, list) and len(authors) > 8:
                author_line += ", …"
            heading = (paper.get("title") or paper.get("canonical_id") or "untitled").replace(
                "[[", ""
            ).replace("]]", "")
            fid = paper.get("file_id")
            if fid:
                lines.append(f"- [[{fid}|{heading}]] ({year})")
            else:
                lines.append(f"- {heading} ({year})")
            bits = [b for b in (author_line, paper.get("doi"), paper.get("arxiv_id")) if b]
            if bits:
                lines.append(f"  { ' · '.join(str(b) for b in bits)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_readable(corpus_dir: Path, manifest: Manifest) -> int:
    """Write JSON + markdown for every paper, and a vault-visible catalog."""
    corpus_dir = Path(corpus_dir)
    papers = [row_to_metadata(row) for row in manifest.all_papers()]
    if not papers:
        return 0
    for row in manifest.all_papers():
        write_paper_metadata(corpus_dir, row)
        write_paper_note(corpus_dir, row_to_metadata(row))
    catalog = _catalog_markdown(corpus_dir, papers)
    atomic_write_text(corpus_dir / "CATALOG.md", catalog)
    atomic_write_text(corpus_dir.parent / f"{corpus_dir.name}.md", catalog)
    write_run_state(corpus_dir, manifest)
    write_citation_graph(corpus_dir, manifest)
    return len(papers)


def write_citation_graph(corpus_dir: Path, manifest: Manifest) -> None:
    payload = {
        "seed_canonical_id": manifest.get_meta("seed_canonical_id"),
        "forward_citations_as_of": manifest.get_meta("forward_citations_as_of"),
        "run_mode": manifest.get_meta("run_mode"),
        "edges": manifest.all_edges(),
    }
    atomic_write_text(
        corpus_dir / "citation_graph.json",
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def write_run_state(corpus_dir: Path, manifest: Manifest) -> None:
    keys = [
        "run_mode",
        "run_started_at",
        "run_paused_at",
        "run_finished_at",
        "seed_canonical_id",
        "seed_arxiv_id",
        "seed_doi",
        "seed_s2_id",
        "seed_openalex_id",
        "forward_citations_as_of",
        "s2_reference_count_reported",
        "s2_citation_count_reported",
        "openalex_referenced_works_count",
        "openalex_cited_by_count",
        "backward_job",
        "forward_job",
    ]
    state = {k: manifest.get_meta(k) for k in keys}
    state["paper_counts"] = manifest.counts_by_relation()
    state["status_counts"] = manifest.counts_by_status()
    atomic_write_text(
        corpus_dir / "run_state.json",
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
    )


def generate_readme(manifest: Manifest) -> str:
    started = manifest.get_meta("run_started_at") or "?"
    finished = manifest.get_meta("run_finished_at") or "?"
    as_of = manifest.get_meta("forward_citations_as_of") or "?"
    mode = manifest.get_meta("run_mode") or "?"
    rel = manifest.counts_by_relation()
    total = sum(rel.values())
    papers = manifest.all_papers()
    ft_true = sum(1 for p in papers if p["full_text_available"])
    ft_false = total - ft_true
    reasons = Counter(
        (p["failure_reason"] or "unspecified")
        for p in papers
        if not p["full_text_available"]
    )
    methods = Counter(p["fetch_method"] or "none" for p in papers)
    permanent = [
        {
            "canonical_id": p["canonical_id"],
            "title": p["title"],
            "failure_reason": p["failure_reason"],
            "status": p["status"],
        }
        for p in papers
        if p["status"] == "failed_permanent"
    ]
    edges = manifest.all_edges()
    lines = [
        "# citehop corpus — validation report",
        "",
        f"- Run mode: `{mode}`",
        f"- Run date range (UTC): `{started}` → `{finished}`",
        f'- Forward citations fetched as of `{as_of}` — this corpus does not include citations added after this date.',
        "",
        "## Size",
        "",
        f"- Total papers: **{total}**",
        f"  - seed: {rel.get('seed', 0)}",
        f"  - backward_reference: {rel.get('backward_reference', 0)}",
        f"  - forward_citation: {rel.get('forward_citation', 0)}",
        f"- Citation graph edges: **{len(edges)}**",
        "",
        "## Full text",
        "",
        f"- full_text_available true: {ft_true}",
        f"- full_text_available false: {ft_false}",
        "",
        "### Failure / non-full-text reasons",
        "",
    ]
    if reasons:
        for reason, n in reasons.most_common():
            lines.append(f"- `{reason}`: {n}")
    else:
        lines.append("- (none)")
    lines += [
        "",
        "### Fetch methods",
        "",
    ]
    for method, n in methods.most_common():
        lines.append(f"- `{method}`: {n}")
    lines += [
        "",
        "## API counts at seed resolution (reported by providers, not corpus size)",
        "",
        f"- Semantic Scholar referenceCount: {manifest.get_meta('s2_reference_count_reported')}",
        f"- Semantic Scholar citationCount: {manifest.get_meta('s2_citation_count_reported')}",
        f"- OpenAlex referenced_works_count: {manifest.get_meta('openalex_referenced_works_count')}",
        f"- OpenAlex cited_by_count: {manifest.get_meta('openalex_cited_by_count')}",
        "",
        "## Permanent failures (manual follow-up)",
        "",
    ]
    if not permanent:
        lines.append("- None.")
    else:
        for item in permanent:
            lines.append(
                f"- `{item['canonical_id']}` — {item['title'] or '(no title)'} "
                f"({item['failure_reason']})"
            )
    lines += [
        "",
        "## Stretch goal (not executed)",
        "",
        "2-hop expansion (references-of-references and citations-of-citations) is gated.",
        "Do not run it without an explicit request: combinatorial blowup and relevance drift.",
        "",
        "## Resume",
        "",
        "Re-run `python -m citehop sample` or `python -m citehop build --yes` with the same seed flags.",
        "Status lives in `manifest.db`. Papers with status `fetched` or `failed_permanent` are not re-fetched.",
        "`failed_retry` and `pending` are retried.",
        "",
    ]
    return "\n".join(lines) + "\n"
