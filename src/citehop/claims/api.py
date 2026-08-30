"""Public API for schema, extraction runs, claim query, and human review.

The desktop UI and CLI call only this module — never engine internals.
"""

from __future__ import annotations

import json
import os
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from citehop.catalog import list_corpora
from citehop.ids import file_id

from .engine import (
    ExtractionError,
    coerce_fields,
    corpus_papers,
    load_paper_text,
    process_one_paper,
)
from .llm import (
    BackendUnavailable,
    GenerationCancelled,
    LLMError,
    abort_generation,
    clear_generation_abort,
    select_backend,
)
from .projects import ProjectError, ProjectStore, require_schema_for_run
from .schema import SchemaError, fields_for_type, list_templates, type_ids
from .store import ClaimStore, VERIFICATION, extract_lease_seconds


class ClaimsAPI:
    def __init__(self, projects_root: Path | None = None) -> None:
        self.projects = ProjectStore(projects_root)

    def templates(self) -> list[dict[str, Any]]:
        return list_templates()

    def corpora(self) -> list[dict[str, Any]]:
        out = []
        for item in list_corpora():
            out.append(
                {
                    "slug": item.slug,
                    "path": str(item.path),
                    "label": item.label,
                    "paper_count": item.paper_count,
                }
            )
        return out

    def list_projects(self) -> list[dict[str, Any]]:
        return self.projects.list_projects()

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self.projects.get_project(project_id)

    def create_project(
        self,
        display_name: str,
        corpus_dir: str | Path,
        template_id: str | None = None,
        token_budget: int = 500_000,
        time_budget_seconds: int | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.projects.create_project(
            display_name,
            corpus_dir,
            template_id=template_id,
            token_budget=token_budget,
            time_budget_seconds=time_budget_seconds,
            project_id=project_id,
        )

    def update_project(self, project_id: str, **fields: Any) -> dict[str, Any]:
        return self.projects.update_project(project_id, **fields)

    def get_schema(self, project_id: str) -> dict[str, Any]:
        return self.projects.load_schema(project_id)

    def update_schema(self, project_id: str, schema: dict[str, Any]) -> dict[str, Any]:
        return self.projects.save_schema(project_id, schema)

    def apply_template(self, project_id: str, template_id: str) -> dict[str, Any]:
        from .schema import clone_schema, load_template

        current = self.get_schema(project_id)
        schema_id = current.get("schema_id") or f"{project_id}-schema"
        cloned = clone_schema(load_template(template_id), schema_id)
        return self.update_schema(project_id, cloned)

    def schema_type_ids(self, project_id: str) -> list[str]:
        return type_ids(self.get_schema(project_id))

    def extraction_models(self) -> list[dict[str, Any]]:
        from citehop.models import list_models

        return list_models()

    def extraction_model(self) -> dict[str, Any]:
        from citehop.models import load_settings, settings_label

        settings = load_settings()
        return {"settings": settings, "label": settings_label(settings)}

    def use_extraction_model(self, row: dict[str, Any]) -> dict[str, Any]:
        from citehop.models import prepare_extraction, select_model

        settings = select_model(row)
        loaded = prepare_extraction()
        return {**settings, **loaded}

    def unload_extraction_models(self) -> dict[str, Any]:
        from citehop.models import unload_loaded_models

        return unload_loaded_models()

    def _store(self, project_id: str) -> ClaimStore:
        self.projects.get_project(project_id)
        return ClaimStore(self.projects.db_path(project_id))

    def start_run(self, project_id: str) -> dict[str, Any]:
        """Start a new extraction run.

        After a completed run, this creates a *new* run_id and re-extracts every
        paper. The review UI shows the latest run; prior claims stay in SQLite
        under the old run_id. Duplicate claims in the *same* run are a bug.
        Resume a paused or crashed (status still 'running') run instead of starting.
        """
        project = self.projects.get_project(project_id)
        require_schema_for_run(self.projects, project_id)
        store = self._store(project_id)
        try:
            latest = store.latest_run(project_id)
            if latest and latest["status"] == "running":
                raise ExtractionError(
                    "An extraction run is already in progress for this project. "
                    "Resume it if the worker died, or pause it first."
                )
            if latest and latest["status"] == "paused":
                raise ExtractionError(
                    "A paused run exists for this project. Resume it instead of starting a new one."
                )
            papers = corpus_papers(Path(project["corpus_dir"]))
            if not papers:
                raise ExtractionError("Corpus has no papers")
            select_backend()
            clear_generation_abort()
            identity = self._run_identity(project_id)
            run_id = store.create_run(
                project_id,
                [{"canonical_id": p["canonical_id"], "file_id": p["file_id"]} for p in papers],
                token_budget=int(project.get("token_budget") or 500_000),
                time_budget_seconds=project.get("time_budget_seconds"),
                llm_backend=identity["llm_backend"],
                llm_model=identity["llm_model"],
                schema_id=identity["schema_id"],
            )
            status = store.run_status_dict(run_id)
            assert status is not None
            return status
        finally:
            store.close()

    def pause_run(self, project_id: str) -> dict[str, Any]:
        store = self._store(project_id)
        try:
            run = store.latest_run(project_id)
            if not run:
                raise ExtractionError("No extraction run to pause")
            if run["status"] not in ("running", "paused"):
                raise ExtractionError(
                    f"Cannot pause a run with status {run['status']!r}"
                )
            store.request_pause(run["run_id"])
            abort_generation()
            if run["status"] == "running":
                store.set_run_status(run["run_id"], "paused")
            store.requeue_extracting(run["run_id"])
            status = store.run_status_dict(run["run_id"])
            assert status is not None
            return status
        finally:
            store.close()

    def resume_run(self, project_id: str) -> dict[str, Any]:
        """Continue a paused run, or reattach after a crash that left status=running."""
        project = self.projects.get_project(project_id)
        require_schema_for_run(self.projects, project_id)
        store = self._store(project_id)
        try:
            run = store.latest_run(project_id)
            if not run:
                raise ExtractionError("No extraction run to resume")
            if run["status"] == "completed":
                raise ExtractionError("Run already completed; start a new run")
            if run["status"] == "failed":
                raise ExtractionError("Run failed; start a new run")
            select_backend()
            clear_generation_abort()
            store.requeue_extracting(run["run_id"])
            store.requeue_retryable_errors(run["run_id"])
            corpus_dir = Path(project["corpus_dir"])
            papers_by_id = {p["canonical_id"]: p for p in corpus_papers(corpus_dir)}
            ready: list[str] = []
            for cid in store.papers_with_status(run["run_id"], "skipped_no_text"):
                paper = papers_by_id.get(cid) or {"canonical_id": cid}
                if load_paper_text(corpus_dir, paper):
                    ready.append(cid)
            store.requeue_skipped(run["run_id"], ready)
            remaining = store.next_pending_paper(run["run_id"])
            used = int(run["tokens_used"])
            budget = int(project.get("token_budget") or run["token_budget"])
            if remaining is None:
                store.set_run_status(run["run_id"], "completed")
            elif used >= budget:
                store.set_run_status(run["run_id"], "paused", error="Token budget reached")
            else:
                store.set_run_status(run["run_id"], "running")
            status = store.run_status_dict(run["run_id"])
            assert status is not None
            return status
        finally:
            store.close()

    def run_status(self, project_id: str) -> dict[str, Any]:
        store = self._store(project_id)
        try:
            run = store.latest_run(project_id)
            if not run:
                return {
                    "project_id": project_id,
                    "status": "idle",
                    "papers_total": 0,
                    "papers_done": 0,
                    "papers_skipped": 0,
                    "papers_pending": 0,
                    "tokens_used": 0,
                    "token_budget": self.projects.get_project(project_id).get("token_budget") or 0,
                    "run_id": None,
                    "error": None,
                }
            status = store.run_status_dict(run["run_id"])
            assert status is not None
            return status
        finally:
            store.close()

    def process_available(self, project_id: str, max_papers: int = 1) -> dict[str, Any]:
        """Process up to max_papers pending papers. Called by the UI worker and CLI."""
        project = self.projects.get_project(project_id)
        schema = require_schema_for_run(self.projects, project_id)
        store = self._store(project_id)
        just_completed = False
        try:
            run = store.latest_run(project_id)
            if not run:
                raise ExtractionError("Start a run first")
            run_id = run["run_id"]
            if run["status"] != "running":
                status = store.run_status_dict(run_id)
                assert status is not None
                return status
            try:
                llm = _ready_llm()
            except LLMError as exc:
                store.set_run_status(run_id, "paused", error=str(exc))
                status = store.run_status_dict(run_id)
                assert status is not None
                return status
            papers = {p["canonical_id"]: p for p in corpus_papers(Path(project["corpus_dir"]))}
            watch_stop = threading.Event()
            watcher = threading.Thread(
                target=_watch_pause_flag,
                args=(store.db_path, run_id, watch_stop),
                name="citehop-pause-watch",
                daemon=True,
            )
            watcher.start()
            processed = 0
            try:
                while processed < max_papers:
                    run = store.get_run(run_id)
                    if not run:
                        break
                    if _time_budget_exceeded(run):
                        store.set_run_status(run_id, "paused", error="Time budget reached")
                        break
                    if store.pause_requested(run_id):
                        abort_generation()
                        store.set_run_status(run_id, "paused")
                        store.requeue_extracting(run_id)
                        break
                    store.requeue_stale_extracting(run_id, extract_lease_seconds())
                    pending = store.claim_next_paper(run_id)
                    if pending is None:
                        if store.count_papers(run_id, "extracting") > 0:
                            # Another worker holds in-flight papers; do not mark complete.
                            break
                        store.set_run_status(run_id, "completed")
                        just_completed = True
                        break
                    try:
                        result = process_one_paper(
                            store,
                            project_id=project_id,
                            run_id=run_id,
                            schema=schema,
                            corpus_dir=Path(project["corpus_dir"]),
                            paper_row=pending,
                            papers_by_id=papers,
                            llm=llm,
                            token_budget=int(project.get("token_budget") or run["token_budget"]),
                        )
                    except GenerationCancelled:
                        run = store.get_run(run_id)
                        if run and run["status"] == "running":
                            store.set_run_status(run_id, "paused")
                        store.requeue_extracting(run_id)
                        break
                    except BackendUnavailable as exc:
                        store.set_run_status(run_id, "paused", error=str(exc))
                        break
                    processed += 1
                    if result == "budget":
                        break
                    run = store.get_run(run_id)
                    if run and run["status"] != "running":
                        break
            finally:
                watch_stop.set()
            status = store.run_status_dict(run_id)
            assert status is not None
        finally:
            store.close()
        if just_completed and status is not None:
            try:
                exported = self.export_claims(project_id)
                status = {
                    **status,
                    "export_path": exported["path"],
                    "claim_count": exported["claim_count"],
                    "claims_dir": str(self.projects.project_dir(project_id) / "claims"),
                }
            except ExtractionError:
                pass
        return status

    def list_claims(
        self,
        project_id: str,
        *,
        claim_type: str | None = None,
        agreement: str | None = None,
        verification_status: str | None = None,
        paper_canonical_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        store = self._store(project_id)
        try:
            if not run_id:
                latest = store.latest_run(project_id)
                run_id = latest["run_id"] if latest else None
            if not run_id:
                return []
            return store.query_claims(
                project_id,
                run_id=run_id,
                claim_type=claim_type or None,
                agreement=agreement or None,
                verification_status=verification_status or None,
                paper_canonical_id=paper_canonical_id or None,
            )
        finally:
            store.close()

    def get_claim(self, project_id: str, claim_id: str) -> dict[str, Any]:
        store = self._store(project_id)
        try:
            rec = store.get_claim(claim_id)
            if not rec or rec["project_id"] != project_id:
                raise KeyError(claim_id)
            return rec
        finally:
            store.close()

    def review_claim(
        self,
        project_id: str,
        claim_id: str,
        action: str,
        edit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action = action.strip().lower()
        mapping = {
            "confirm": "human_confirmed",
            "reject": "human_rejected",
            "edit": "human_edited",
            "human_confirmed": "human_confirmed",
            "human_rejected": "human_rejected",
            "human_edited": "human_edited",
        }
        if action not in mapping:
            raise ValueError(f"Unknown review action {action!r}")
        status = mapping[action]
        if status not in VERIFICATION:
            raise ValueError(status)
        rec = self.get_claim(project_id, claim_id)
        schema = self.get_schema(project_id)
        allowed = set(type_ids(schema))
        if status == "human_edited":
            if not edit:
                raise ValueError("edit action requires an edit object")
            ctype = edit.get("claim_type", rec["claim_type"])
            if ctype not in allowed:
                raise SchemaError(f"claim_type {ctype!r} is not in this project's schema")
            fields = edit.get("structured_fields", rec["structured_fields"])
            edit = {
                **edit,
                "claim_type": ctype,
                "structured_fields": coerce_fields(
                    fields_for_type(schema, ctype), fields, strict=True
                ),
            }
        store = self._store(project_id)
        try:
            return store.apply_review(claim_id, status, human_edit=edit)
        finally:
            store.close()

    def paper_source(self, project_id: str, paper_canonical_id: str) -> dict[str, Any]:
        project = self.projects.get_project(project_id)
        corpus_dir = Path(project["corpus_dir"])
        papers = {p["canonical_id"]: p for p in corpus_papers(corpus_dir)}
        paper = papers.get(paper_canonical_id)
        if not paper:
            raise KeyError(paper_canonical_id)
        text = load_paper_text(corpus_dir, paper) or ""
        fid = paper.get("file_id") or file_id(paper_canonical_id)
        pdf = corpus_dir / "raw" / f"{fid}.pdf"
        return {
            "paper_canonical_id": paper_canonical_id,
            "title": paper.get("title") or paper_canonical_id,
            "file_id": fid,
            "text": text,
            "pdf_path": str(pdf) if pdf.is_file() else None,
        }

    def export_claims(
        self,
        project_id: str,
        dest: Path | None = None,
        *,
        run_id: str | None = None,
        verification_status: str | None = None,
    ) -> dict[str, Any]:
        """Write claims + review state as JSON. Citehop's job ends at this file."""
        project = self.projects.get_project(project_id)
        schema = self.get_schema(project_id)
        store = self._store(project_id)
        try:
            run = store.get_run(run_id) if run_id else store.latest_run(project_id)
            if not run:
                raise ExtractionError("No extraction run to export")
            rid = run["run_id"]
            status = store.run_status_dict(rid)
            assert status is not None
            claims = store.query_claims(
                project_id,
                run_id=rid,
                verification_status=verification_status or None,
            )
        finally:
            store.close()
        payload = {
            "format": "citehop.claims.v1",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "handoff": (
                "Citehop's job ends at this file. These are extracted spans plus "
                "your review flags (confirm / reject / edit). Interpreting them as "
                "a research conclusion, a problem statement, or input to another "
                "tool is yours."
            ),
            "project": {
                "project_id": project["project_id"],
                "display_name": project.get("display_name"),
                "corpus_dir": project.get("corpus_dir"),
            },
            "schema": {
                "schema_id": schema.get("schema_id"),
                "project_domain_label": schema.get("project_domain_label") or "",
                "claim_types": [
                    {
                        "type_id": ct["type_id"],
                        "display_name": ct.get("display_name"),
                        "description": ct.get("description"),
                    }
                    for ct in schema.get("claim_types") or []
                ],
            },
            "run": {
                "run_id": status["run_id"],
                "status": status["status"],
                "llm_backend": status.get("llm_backend"),
                "llm_model": status.get("llm_model"),
                "schema_id": status.get("schema_id"),
                "started_at": status.get("started_at"),
                "updated_at": status.get("updated_at"),
                "papers_total": status.get("papers_total"),
                "papers_done": status.get("papers_done"),
                "papers_skipped": status.get("papers_skipped"),
                "papers_pending": status.get("papers_pending"),
                "tokens_used": status.get("tokens_used"),
            },
            "agreement_counts": dict(Counter(c["agreement"] for c in claims)),
            "verification_counts": dict(Counter(c["verification_status"] for c in claims)),
            "claims": claims,
        }
        if dest is None:
            dest = self.projects.project_dir(project_id) / "exports" / f"claims-{rid}.json"
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(dest)
        from .files import write_claim_files

        write_claim_files(self.projects.project_dir(project_id), claims)
        return {
            "path": str(dest),
            "claims_dir": str(self.projects.project_dir(project_id) / "claims"),
            "claim_count": len(claims),
            "run_id": rid,
        }

    def _run_identity(self, project_id: str) -> dict[str, str | None]:
        schema = self.get_schema(project_id)
        env = (os.environ.get("CITEHOP_LLM") or "").strip().lower()
        if env in ("fixture", "grounded", "test"):
            return {
                "llm_backend": "fixture",
                "llm_model": "fixture",
                "schema_id": schema.get("schema_id"),
            }
        from citehop.models import load_settings

        settings = load_settings() or {}
        return {
            "llm_backend": settings.get("backend"),
            "llm_model": settings.get("model"),
            "schema_id": schema.get("schema_id"),
        }


__all__ = [
    "BackendUnavailable",
    "ClaimsAPI",
    "ExtractionError",
    "GenerationCancelled",
    "LLMError",
    "ProjectError",
    "SchemaError",
]


def _watch_pause_flag(db_path: Path, run_id: str, stop: threading.Event) -> None:
    """Abort generation when another process or the UI sets pause_requested."""
    other = ClaimStore(db_path)
    try:
        while not stop.wait(0.05):
            run = other.get_run(run_id)
            if not run:
                return
            if run["status"] != "running" or run["pause_requested"]:
                abort_generation()
                return
    finally:
        other.close()


def _time_budget_exceeded(run: Any) -> bool:
    seconds = run["time_budget_seconds"] if run else None
    started = run["started_at"] if run else None
    if not seconds or not started:
        return False
    try:
        t0 = datetime.fromisoformat(started)
    except ValueError:
        return False
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t0).total_seconds() >= int(seconds)


def _ready_llm():
    """Resolve the configured backend and make sure it is loaded for extraction."""
    choice = (os.environ.get("CITEHOP_LLM") or "").strip().lower()
    if choice not in ("fixture", "grounded", "test"):
        from citehop.models import prepare_extraction

        try:
            prepare_extraction()
        except RuntimeError as exc:
            raise BackendUnavailable(str(exc)) from exc
    return select_backend()

