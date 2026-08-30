"""End-to-end 1-hop corpus pipeline. Resumable via manifest.db."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import artifacts
from .clients import arxiv as arxiv_api
from .clients import openalex as openalex_api
from .clients import s2 as s2_api
from .clients import unpaywall as unpaywall_api
from .config import MAX_PDF_BYTES
from .extract import extract_eprint_text, extract_pdf_text, is_html_bytes, is_pdf_bytes
from .http_client import FetchCancelled, PermanentHttpError, RateLimitedClient
from .ids import canonical_id, file_id, normalize_arxiv, normalize_doi, normalize_openalex
from .seed import SeedQuery, resolve as resolve_seed_live
from .store import Manifest, utcnow


def _cid_rank(cid: str | None) -> int:
    if not cid:
        return 99
    if cid.startswith("openalex:"):
        return 3
    if cid.startswith("s2:"):
        return 2
    if cid.startswith("arxiv:"):
        return 1
    return 0


def _coalesce(*vals: Any) -> Any:
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None


def _prefer_authors(old: list[str] | None, new: list[str] | None) -> list[str] | None:
    old = old or []
    new = new or []
    if not old:
        return new or None
    if not new:
        return old
    old0 = old[0] if old else ""
    new0 = new[0] if new else ""
    if len(new0) > len(old0) + 3:
        return new
    if len(old) >= len(new):
        return old
    return new


class BuildPaused(Exception):
    """User paused or closed the app. Manifest on disk is consistent; resume the same seed."""


class CorpusBuilder:
    def __init__(
        self,
        corpus_dir: Path,
        *,
        seed: SeedQuery | None = None,
        sample_backward: int | None = None,
        sample_forward: int | None = None,
        write_readme: bool = False,
        log: Callable[[str], None] | None = None,
        stop: threading.Event | None = None,
    ):
        self.seed = seed.normalized() if seed else None
        self.corpus_dir = Path(corpus_dir)
        self.sample_backward = sample_backward
        self.sample_forward = sample_forward
        self.write_readme = write_readme
        self.sample_mode = sample_backward is not None or sample_forward is not None
        self._log = log or (lambda msg: print(msg, flush=True))
        self._stop = stop if stop is not None else threading.Event()
        self.corpus_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("raw", "text", "metadata"):
            (self.corpus_dir / sub).mkdir(parents=True, exist_ok=True)
        self.manifest = Manifest(self.corpus_dir / "manifest.db")
        self.http = RateLimitedClient(self.corpus_dir / "fetch_log.jsonl", cancelled=self._stop)
        self.unresolved_path = self.corpus_dir / "unresolved.jsonl"

    def pause(self) -> None:
        self._stop.set()
        self.http.abort()

    def paused(self) -> bool:
        return self._stop.is_set()

    def _check_pause(self) -> None:
        if self._stop.is_set():
            raise BuildPaused("Corpus analysis paused")

    def run(self) -> None:
        try:
            self._run_stages()
        except (BuildPaused, FetchCancelled, KeyboardInterrupt) as exc:
            self.pause()
            self.manifest.set_meta("run_paused_at", utcnow())
            artifacts.write_citation_graph(self.corpus_dir, self.manifest)
            artifacts.write_run_state(self.corpus_dir, self.manifest)
            self._log(
                "Paused. Progress is saved in this corpus folder. "
                "Resume with the same seed; already-fetched papers are skipped."
            )
            if isinstance(exc, BuildPaused):
                raise
            raise BuildPaused("Corpus analysis paused") from exc

    def _run_stages(self) -> None:
        if not self.manifest.get_meta("run_started_at"):
            self.manifest.set_meta("run_started_at", utcnow())
        self.manifest.set_meta("run_paused_at", "")
        self.manifest.set_meta(
            "run_mode",
            (
                f"sample:backward={self.sample_backward},forward={self.sample_forward}"
                if self.sample_mode
                else "full_1hop"
            ),
        )
        self._check_pause()
        self._log("Resolving seed against live APIs…")
        self.resolve_seed()
        self._check_pause()
        self._log("Fetching backward references (papers the seed cites)…")
        self.fetch_backward_s2()
        self._check_pause()
        self._log("Fetching forward citations (papers that cite the seed)…")
        self.fetch_forward_s2()
        if not self.sample_mode:
            self._check_pause()
            self._log("Fetching OpenAlex citations and references…")
            self.fetch_openalex_citations()
            self.fetch_openalex_references()
        self._check_pause()
        self._log("Enriching OpenAlex identifiers…")
        self.enrich_openalex_ids()
        self._check_pause()
        self._log("Fetching full texts where an OA copy exists…")
        self.fetch_full_texts()
        self.manifest.set_meta("run_finished_at", utcnow())
        artifacts.write_citation_graph(self.corpus_dir, self.manifest)
        artifacts.write_run_state(self.corpus_dir, self.manifest)
        if self.write_readme:
            readme = artifacts.generate_readme(self.manifest)
            artifacts.atomic_write_text(self.corpus_dir / "README.md", readme)
        self._rewrite_all_metadata()
        counts = self.manifest.counts_by_relation()
        self._log(
            "Run complete. "
            f"papers={sum(counts.values())} "
            f"seed={counts.get('seed', 0)} "
            f"cited_by_seed={counts.get('backward_reference', 0)} "
            f"cites_seed={counts.get('forward_citation', 0)}"
        )

    def resolve_seed(self) -> str:
        job = self.manifest.get_job("seed_resolve")
        cid = self.manifest.get_meta("seed_canonical_id")
        s2_id = self.manifest.get_meta("seed_s2_id")
        stored_fp = self.manifest.get_meta("seed_query_fingerprint")
        if self.seed and stored_fp and stored_fp != self.seed.fingerprint():
            raise SystemExit(
                f"Corpus dir {self.corpus_dir} already belongs to {stored_fp}; "
                f"this run asked for {self.seed.fingerprint()}. Use a different --corpus-dir."
            )
        if job and job["status"] == "complete" and cid and s2_id:
            self._log(f"Seed already resolved: {cid}")
            return cid
        if not self.seed:
            raise SystemExit(
                "This corpus has no resolved seed. Pass --doi, --arxiv, --title, or --preset."
            )

        rec = resolve_seed_live(self.http, self.seed)
        cid = rec["canonical_id"]
        arxiv_hit = rec.get("arxiv_hit") or {}
        xref_hit = rec.get("xref_hit") or {}
        s2_paper = rec.get("s2_paper") or {}
        oa_work = rec.get("oa_work") or {}
        extras = {
            "arxiv_journal_ref": arxiv_hit.get("journal_ref"),
            "crossref_volume": xref_hit.get("volume"),
            "crossref_issued": xref_hit.get("issued"),
            "s2_open_access_pdf_url": s2_paper.get("open_access_pdf_url"),
            "openalex_oa_url": oa_work.get("oa_url"),
            "openalex_oa_status": oa_work.get("oa_status"),
            "confirmation": {
                "title": rec["title"],
                "arxiv_id": rec["arxiv_id"],
                "doi": rec["doi"],
                "query_fingerprint": self.seed.fingerprint(),
            },
        }
        self.manifest.upsert_paper(
            {
                "canonical_id": cid,
                "file_id": file_id(cid),
                "status": "pending",
                "relation_to_seed": "seed",
                "title": rec["title"],
                "authors": rec["authors"],
                "year": rec["year"],
                "venue": rec["venue"],
                "arxiv_id": rec["arxiv_id"],
                "doi": rec["doi"],
                "semantic_scholar_id": rec["semantic_scholar_id"],
                "openalex_id": rec["openalex_id"],
                "abstract": rec["abstract"],
                "metadata": extras,
            }
        )
        self.manifest.set_meta("seed_canonical_id", cid)
        self.manifest.set_meta("seed_arxiv_id", rec["arxiv_id"])
        self.manifest.set_meta("seed_doi", rec["doi"])
        self.manifest.set_meta("seed_s2_id", rec["semantic_scholar_id"])
        self.manifest.set_meta("seed_openalex_id", rec["openalex_id"])
        self.manifest.set_meta("seed_query_fingerprint", self.seed.fingerprint())
        if s2_paper:
            self.manifest.set_meta("s2_reference_count_reported", s2_paper.get("reference_count"))
            self.manifest.set_meta("s2_citation_count_reported", s2_paper.get("citation_count"))
        if oa_work:
            self.manifest.set_meta(
                "openalex_referenced_works_count", oa_work.get("referenced_works_count")
            )
            self.manifest.set_meta("openalex_cited_by_count", oa_work.get("cited_by_count"))
            artifacts.atomic_write_text(
                self.corpus_dir / "seed_openalex_referenced_works.json",
                json.dumps(oa_work.get("referenced_works") or [], indent=2) + "\n",
            )
        self.manifest.set_job(
            "seed_resolve",
            "complete",
            {"canonical_id": cid, "doi": rec["doi"], "arxiv_id": rec["arxiv_id"]},
        )
        self.http.log(
            paper_id=cid,
            action="seed_resolve",
            outcome="ok",
            doi=rec["doi"],
            arxiv_id=rec["arxiv_id"],
            s2_id=rec["semantic_scholar_id"],
            openalex_id=rec["openalex_id"],
        )
        self._log(
            f"Seed confirmed: {cid}\n"
            f"  title={rec['title']}\n"
            f"  arXiv={rec['arxiv_id']}  DOI={rec['doi']}\n"
            f"  S2 refs={self.manifest.get_meta('s2_reference_count_reported')} "
            f"cites={self.manifest.get_meta('s2_citation_count_reported')}\n"
            f"  OpenAlex refs={self.manifest.get_meta('openalex_referenced_works_count')} "
            f"cites={self.manifest.get_meta('openalex_cited_by_count')}"
        )
        return cid

    def _seed_s2_key(self) -> str:
        s2_id = self.manifest.get_meta("seed_s2_id")
        if s2_id:
            return s2_id
        arxiv_id = self.manifest.get_meta("seed_arxiv_id")
        if arxiv_id:
            return f"ARXIV:{arxiv_id}"
        doi = self.manifest.get_meta("seed_doi")
        if doi:
            return f"DOI:{doi}"
        raise RuntimeError("seed identifiers missing")

    def fetch_backward_s2(self) -> None:
        seed_cid = self.manifest.get_meta("seed_canonical_id")
        wanted = f"sample:{self.sample_backward}" if self.sample_mode else "complete"
        job = self.manifest.get_job("backward_s2")
        if job and job["status"] == wanted:
            return
        if job and job["status"] == "complete" and self.sample_mode:
            return
        limit = self.sample_backward if self.sample_mode else None
        count = 0
        try:
            for paper in s2_api.iter_references(
                self.http, self._seed_s2_key(), seed_id=seed_cid, limit=limit
            ):
                self._check_pause()
                cid = self.ingest_paper(paper, "backward_reference")
                if cid:
                    self.manifest.add_edge(seed_cid, cid, "cites")
                    count += 1
        except FetchCancelled as exc:
            self._log(f"Paused after ingesting {count} backward references.")
            raise BuildPaused("Corpus analysis paused") from exc
        self.manifest.set_job("backward_s2", wanted, {"ingested": count})
        self.manifest.set_meta("backward_job", wanted)
        self._log(f"Backward references ingested: {count}")

    def fetch_forward_s2(self) -> None:
        seed_cid = self.manifest.get_meta("seed_canonical_id")
        wanted = f"sample:{self.sample_forward}" if self.sample_mode else "complete"
        job = self.manifest.get_job("forward_s2")
        if job and job["status"] == wanted:
            return
        if job and job["status"] == "complete" and self.sample_mode:
            return
        as_of = utcnow()
        if not self.manifest.get_meta("forward_citations_as_of"):
            self.manifest.set_meta("forward_citations_as_of", as_of)
        limit = self.sample_forward if self.sample_mode else None
        count = 0
        try:
            for paper in s2_api.iter_citations(
                self.http, self._seed_s2_key(), seed_id=seed_cid, limit=limit
            ):
                self._check_pause()
                cid = self.ingest_paper(paper, "forward_citation")
                if cid:
                    self.manifest.add_edge(cid, seed_cid, "cites")
                    count += 1
        except FetchCancelled as exc:
            self._log(f"Paused after ingesting {count} forward citations.")
            raise BuildPaused("Corpus analysis paused") from exc
        self.manifest.set_job("forward_s2", wanted, {"ingested": count, "as_of": as_of})
        self.manifest.set_meta("forward_job", wanted)
        self._log(f"Forward citations ingested: {count}")

    def fetch_openalex_citations(self) -> None:
        job = self.manifest.get_job("forward_openalex")
        if job and job["status"] == "complete":
            return
        seed_cid = self.manifest.get_meta("seed_canonical_id")
        oa_id = self.manifest.get_meta("seed_openalex_id")
        if not oa_id:
            self.manifest.set_job("forward_openalex", "skipped", {"reason": "no openalex id"})
            return
        count = 0
        try:
            for paper in openalex_api.iter_cited_by(self.http, oa_id, seed_id=seed_cid):
                self._check_pause()
                cid = self.ingest_paper(paper, "forward_citation")
                if cid and cid != seed_cid:
                    self.manifest.add_edge(cid, seed_cid, "cites")
                    count += 1
        except FetchCancelled as exc:
            self._log(f"Paused after ingesting {count} OpenAlex citations.")
            raise BuildPaused("Corpus analysis paused") from exc
        self.manifest.set_job("forward_openalex", "complete", {"ingested": count})

    def fetch_openalex_references(self) -> None:
        job = self.manifest.get_job("backward_openalex")
        if job and job["status"] == "complete":
            return
        seed_cid = self.manifest.get_meta("seed_canonical_id")
        path = self.corpus_dir / "seed_openalex_referenced_works.json"
        if not path.exists():
            self.manifest.set_job("backward_openalex", "skipped", {"reason": "no referenced_works file"})
            return
        ids = json.loads(path.read_text(encoding="utf-8"))
        count = 0
        try:
            for raw in ids:
                self._check_pause()
                oid = normalize_openalex(raw)
                if not oid:
                    continue
                existing = self.manifest.find_by_any_id(openalex_id=oid)
                if existing:
                    self.manifest.add_edge(seed_cid, existing["canonical_id"], "cites")
                    continue
                work = openalex_api.get_by_id(self.http, oid, paper_id=seed_cid)
                if not work:
                    self._unresolved({"source": "openalex_referenced_works", "openalex_id": oid})
                    continue
                cid = self.ingest_paper(work, "backward_reference")
                if cid:
                    self.manifest.add_edge(seed_cid, cid, "cites")
                    count += 1
        except FetchCancelled as exc:
            self._log(f"Paused after hydrating {count} OpenAlex references.")
            raise BuildPaused("Corpus analysis paused") from exc
        self.manifest.set_job("backward_openalex", "complete", {"hydrated_new": count})

    def enrich_openalex_ids(self) -> None:
        for row in self.manifest.all_papers():
            self._check_pause()
            extras = json.loads(row["metadata_json"] or "{}")
            if row["openalex_id"] and extras.get("openalex_oa_url") is not None:
                continue
            if not row["doi"]:
                continue
            work = openalex_api.get_by_doi(
                self.http, row["doi"], paper_id=row["canonical_id"]
            )
            if not work:
                extras["openalex_lookup"] = "not_found"
                self.manifest.upsert_paper(
                    {
                        "canonical_id": row["canonical_id"],
                        "file_id": row["file_id"],
                        "status": row["status"],
                        "relation_to_seed": row["relation_to_seed"],
                        "metadata": extras,
                    }
                )
                continue
            extras["openalex_oa_url"] = work.get("oa_url")
            extras["openalex_oa_status"] = work.get("oa_status")
            extras["openalex_is_oa"] = work.get("is_oa")
            self.ingest_paper(
                {
                    "doi": work.get("doi") or row["doi"],
                    "arxiv_id": work.get("arxiv_id") or row["arxiv_id"],
                    "semantic_scholar_id": row["semantic_scholar_id"],
                    "openalex_id": work.get("openalex_id"),
                    "title": work.get("title") or row["title"],
                    "authors": json.loads(row["authors_json"] or "[]"),
                    "year": work.get("year") or row["year"],
                    "venue": work.get("venue") or row["venue"],
                    "abstract": row["abstract"],
                    "oa_url": work.get("oa_url"),
                    "pdf_url": work.get("pdf_url"),
                },
                row["relation_to_seed"],
            )

    def ingest_paper(self, rec: dict[str, Any], relation: str) -> str | None:
        doi = normalize_doi(rec.get("doi"))
        arxiv_id = normalize_arxiv(rec.get("arxiv_id"))
        s2_id = rec.get("semantic_scholar_id")
        oa_id = normalize_openalex(rec.get("openalex_id"))
        cid = canonical_id(doi=doi, arxiv_id=arxiv_id, s2_id=s2_id, openalex_id=oa_id)
        if not cid:
            self._unresolved({"relation": relation, "title": rec.get("title"), "raw_ids": {
                "doi": doi, "arxiv_id": arxiv_id, "s2": s2_id, "openalex": oa_id
            }})
            return None

        existing = self.manifest.find_by_any_id(
            doi=doi, arxiv_id=arxiv_id, s2_id=s2_id, openalex_id=oa_id
        )
        extras = {}
        authors = rec.get("authors")
        title = rec.get("title")
        year = rec.get("year")
        venue = rec.get("venue")
        abstract = rec.get("abstract")
        status = "pending"
        if existing:
            extras = json.loads(existing["metadata_json"] or "{}")
            authors = _prefer_authors(json.loads(existing["authors_json"] or "[]"), authors)
            title = _coalesce(existing["title"], title)
            year = _coalesce(existing["year"], year)
            venue = _coalesce(existing["venue"], venue)
            abstract = _coalesce(existing["abstract"], abstract)
            doi = doi or existing["doi"]
            arxiv_id = arxiv_id or existing["arxiv_id"]
            s2_id = s2_id or existing["semantic_scholar_id"]
            oa_id = oa_id or existing["openalex_id"]
            better = canonical_id(doi=doi, arxiv_id=arxiv_id, s2_id=s2_id, openalex_id=oa_id)
            if existing["relation_to_seed"] == "seed":
                relation = "seed"
            elif existing["relation_to_seed"] != relation:
                extras["also_relation_to_seed"] = relation
                relation = existing["relation_to_seed"]
            status = existing["status"]
            if better and better != existing["canonical_id"] and _cid_rank(better) < _cid_rank(
                existing["canonical_id"]
            ):
                self._migrate_canonical(existing["canonical_id"], better)
                cid = better
            else:
                cid = existing["canonical_id"]

        if rec.get("open_access_pdf_url"):
            extras["s2_open_access_pdf_url"] = rec["open_access_pdf_url"]
        if rec.get("oa_url"):
            extras["openalex_oa_url"] = rec["oa_url"]
        if rec.get("pdf_url"):
            extras["openalex_pdf_url"] = rec["pdf_url"]
        if rec.get("oa_status"):
            extras["openalex_oa_status"] = rec["oa_status"]

        self.manifest.upsert_paper(
            {
                "canonical_id": cid,
                "file_id": file_id(cid),
                "status": status,
                "relation_to_seed": relation,
                "title": title,
                "authors": authors,
                "year": year,
                "venue": venue,
                "arxiv_id": arxiv_id,
                "doi": doi,
                "semantic_scholar_id": s2_id,
                "openalex_id": oa_id,
                "abstract": abstract,
                "metadata": extras,
            }
        )
        row = self.manifest.get_paper(cid)
        if row:
            artifacts.write_paper_metadata(self.corpus_dir, row)
        return cid

    def _migrate_canonical(self, old: str, new: str) -> None:
        old_row = self.manifest.get_paper(old)
        if not old_row:
            return
        dest = self.manifest.get_paper(new)
        new_fid = file_id(new)
        old_fid = old_row["file_id"]
        if dest and dest["canonical_id"] != old:
            # merge into dest; remap edges; drop old
            pass
        data = dict(old_row)
        data["canonical_id"] = new
        data["file_id"] = new_fid
        authors = json.loads(data.pop("authors_json") or "[]")
        data["authors"] = authors
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        self.manifest.conn.execute("DELETE FROM papers WHERE canonical_id=?", (old,))
        self.manifest.conn.commit()
        self.manifest.upsert_paper(data)
        self.manifest.conn.execute(
            "UPDATE edges SET source=? WHERE source=?", (new, old)
        )
        self.manifest.conn.execute(
            "UPDATE edges SET target=? WHERE target=?", (new, old)
        )
        self.manifest.conn.commit()
        if old_fid != new_fid:
            for sub, ext in (("raw", ".pdf"), ("text", ".txt"), ("metadata", ".json"), ("papers", ".md")):
                src = self.corpus_dir / sub / f"{old_fid}{ext}"
                dst = self.corpus_dir / sub / f"{new_fid}{ext}"
                if src.exists() and not dst.exists():
                    src.replace(dst)

    def fetch_full_texts(self) -> None:
        pending = self.manifest.papers_needing_fetch()
        self._log(f"Full-text queue: {len(pending)} papers")
        for row in pending:
            self._check_pause()
            self._log(f"  fetching {row['canonical_id']!s:.80} [{row['relation_to_seed']}]")
            try:
                self._fetch_one(row)
            except (BuildPaused, FetchCancelled):
                raise
            except Exception as exc:
                self.http.log(
                    paper_id=row["canonical_id"],
                    action="full_text",
                    outcome="failed_retry",
                    error=str(exc),
                )
                self.manifest.set_status(
                    row["canonical_id"],
                    "failed_retry",
                    fetch_status="failed",
                    failure_reason="exception",
                    fetch_timestamp=utcnow(),
                )
                artifacts.write_paper_metadata(
                    self.corpus_dir, self.manifest.get_paper(row["canonical_id"])
                )

    def _fetch_one(self, row) -> None:
        cid = row["canonical_id"]
        extras = json.loads(row["metadata_json"] or "{}")
        pdf_bytes: bytes | None = None
        text: str | None = None
        method: str | None = None
        source_url: str | None = None
        self._check_pause()

        if row["arxiv_id"]:
            try:
                data, _ctype = arxiv_api.fetch_eprint(
                    self.http, row["arxiv_id"], paper_id=cid
                )
                hint, extracted, inner_pdf = extract_eprint_text(data)
                if hint == "arxiv_latex" and extracted and len(extracted.strip()) > 200:
                    method, text, source_url = (
                        "arxiv_latex",
                        extracted,
                        f"https://export.arxiv.org/e-print/{row['arxiv_id']}",
                    )
                elif hint == "arxiv_pdf" and inner_pdf:
                    pdf_bytes = inner_pdf
                    method = "arxiv_pdf"
                    source_url = f"https://export.arxiv.org/e-print/{row['arxiv_id']}"
                    text = extracted
                if method is None:
                    pdf_bytes = arxiv_api.fetch_pdf(self.http, row["arxiv_id"], paper_id=cid)
                    if is_pdf_bytes(pdf_bytes) and not is_html_bytes(pdf_bytes):
                        method = "arxiv_pdf"
                        source_url = f"https://export.arxiv.org/pdf/{row['arxiv_id']}.pdf"
                        text = extract_pdf_text(pdf_bytes)
            except PermanentHttpError as exc:
                extras["arxiv_error"] = f"{exc.status_code}"
            except (BuildPaused, FetchCancelled):
                raise
            except Exception as exc:
                extras["arxiv_error"] = str(exc)[:300]

        if method is None:
            oa_candidates = [
                extras.get("s2_open_access_pdf_url"),
                extras.get("openalex_pdf_url"),
                extras.get("openalex_oa_url"),
            ]
            if row["doi"] and not any(oa_candidates):
                upw = unpaywall_api.lookup(self.http, row["doi"], paper_id=cid)
                if upw:
                    extras["unpaywall_is_oa"] = upw.get("is_oa")
                    extras["unpaywall_oa_status"] = upw.get("oa_status")
                    oa_candidates.append(upw.get("pdf_url"))
            for url in oa_candidates:
                if not url or not str(url).startswith("http"):
                    continue
                try:
                    resp = self.http.download(url, paper_id=cid, action="oa_pdf")
                except (BuildPaused, FetchCancelled):
                    raise
                except PermanentHttpError as exc:
                    extras["oa_error"] = f"{url} -> {exc.status_code}"
                    if exc.status_code in (401, 403):
                        return self._finish_no_fulltext(cid, extras, "paywalled")
                    continue
                body = resp.content
                if len(body) > MAX_PDF_BYTES:
                    extras["oa_error"] = f"{url} too large"
                    continue
                if is_html_bytes(body):
                    extras["oa_error"] = f"{url} returned HTML"
                    continue
                if not is_pdf_bytes(body):
                    extras["oa_error"] = f"{url} not a PDF"
                    continue
                pdf_bytes = body
                method = "oa_pdf"
                source_url = url
                text = extract_pdf_text(body)
                break

        seed_pdf = self.seed.pdf if self.seed else None
        if method is None and row["relation_to_seed"] == "seed" and seed_pdf and seed_pdf.exists():
            pdf_bytes = seed_pdf.read_bytes()
            if is_pdf_bytes(pdf_bytes):
                method = "oa_pdf"
                source_url = str(seed_pdf)
                text = extract_pdf_text(pdf_bytes)
                extras["local_seed_pdf_used"] = True

        if method in ("arxiv_latex", "arxiv_pdf", "oa_pdf") and text and len(text.strip()) > 200:
            fid = file_id(cid)
            if pdf_bytes and method in ("arxiv_pdf", "oa_pdf"):
                artifacts.atomic_write_bytes(self.corpus_dir / "raw" / f"{fid}.pdf", pdf_bytes)
            artifacts.atomic_write_text(self.corpus_dir / "text" / f"{fid}.txt", text)
            self.manifest.set_status(
                cid,
                "fetched",
                full_text_available=1,
                fetch_method=method,
                source_url=source_url,
                fetch_timestamp=utcnow(),
                fetch_status="success",
                failure_reason=None,
                metadata_json=json.dumps(extras, ensure_ascii=False),
            )
            artifacts.write_paper_metadata(self.corpus_dir, self.manifest.get_paper(cid))
            self.http.log(
                paper_id=cid, action="full_text", outcome="success", fetch_method=method
            )
            return

        if method and (not text or len(text.strip()) <= 200):
            return self._finish_no_fulltext(
                cid, extras, "pdf_text_extraction_empty", fetch_status="partial", method=method, url=source_url
            )

        reason = "no_open_access_version"
        if extras.get("oa_error") and "403" in str(extras.get("oa_error")):
            reason = "paywalled"
        return self._finish_no_fulltext(cid, extras, reason, method="abstract_only")

    def _finish_no_fulltext(
        self,
        cid: str,
        extras: dict,
        reason: str,
        *,
        fetch_status: str = "success",
        method: str = "abstract_only",
        url: str | None = None,
    ) -> None:
        row = self.manifest.get_paper(cid)
        if row and row["abstract"]:
            fid = file_id(cid)
            artifacts.atomic_write_text(
                self.corpus_dir / "text" / f"{fid}.txt",
                f"[abstract_only]\n\n{row['abstract']}\n",
            )
        self.manifest.set_status(
            cid,
            "fetched",
            full_text_available=0,
            fetch_method=method,
            source_url=url,
            fetch_timestamp=utcnow(),
            fetch_status=fetch_status,
            failure_reason=reason,
            metadata_json=json.dumps(extras, ensure_ascii=False),
        )
        artifacts.write_paper_metadata(self.corpus_dir, self.manifest.get_paper(cid))
        self.http.log(
            paper_id=cid,
            action="full_text",
            outcome="no_full_text",
            failure_reason=reason,
            fetch_method=method,
        )

    def _unresolved(self, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload["timestamp"] = utcnow()
        with self.unresolved_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.http.log(paper_id=None, action="unresolved", outcome="skipped", **payload)

    def _rewrite_all_metadata(self) -> None:
        n = artifacts.export_readable(self.corpus_dir, self.manifest)
        self._log(f"Wrote metadata JSON and markdown notes for {n} papers")
