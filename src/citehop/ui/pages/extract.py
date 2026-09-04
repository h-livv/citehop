"""Extraction run dashboard. Start/pause/resume go through ClaimsAPI only."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from citehop.claims.api import (
    ClaimsAPI,
    ExtractionError,
    GenerationCancelled,
    LLMError,
    ProjectError,
    SchemaError,
    fetch_open_warning,
)
from citehop.ui.pages import Page
from citehop.ui.widgets import Kpi, StorageBanner, card, muted


class ExtractWorker(QThread):
    progress = Signal(object)
    failed = Signal(str)

    def __init__(self, project_id: str) -> None:
        super().__init__()
        self.project_id = project_id

    def run(self) -> None:
        api = ClaimsAPI()
        try:
            while not self.isInterruptionRequested():
                status = api.process_available(self.project_id, max_papers=1)
                self.progress.emit(status)
                if status.get("status") != "running":
                    return
        except GenerationCancelled:
            return
        except (ExtractionError, LLMError, SchemaError, ProjectError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ExtractPage(Page):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.api = ClaimsAPI()
        self._worker: ExtractWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self.storage = StorageBanner()
        root.addWidget(self.storage)
        self.project_lbl = muted("No project selected.")
        self.model_lbl = muted("")
        kpis = QGridLayout()
        kpis.setSpacing(10)
        self.kpi_papers = Kpi("Papers in this run")
        self.kpi_tokens = Kpi("Tokens")
        self.kpi_eta = Kpi("ETA")
        self.kpi_status = Kpi("Status")
        for i, w in enumerate((self.kpi_papers, self.kpi_tokens, self.kpi_eta, self.kpi_status)):
            kpis.addWidget(w, 0, i)
        self.coverage_lbl = muted("")
        self.last_error_lbl = muted("")

        self.start_btn = QPushButton("Start extraction")
        self.start_btn.setObjectName("accent")
        self.pause_btn = QPushButton("Pause")
        self.resume_btn = QPushButton("Resume")
        self.open_export_btn = QPushButton("Open exports")
        self.start_btn.clicked.connect(self._start)
        self.pause_btn.clicked.connect(self._pause)
        self.resume_btn.clicked.connect(self._resume)
        self.open_export_btn.clicked.connect(self._open_exports)
        btns = QHBoxLayout()
        btns.addWidget(self.start_btn)
        btns.addWidget(self.pause_btn)
        btns.addWidget(self.resume_btn)
        btns.addWidget(self.open_export_btn)
        btns.addStretch()
        btn_w = QWidget()
        btn_w.setLayout(btns)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)

        root.addWidget(self.project_lbl)
        root.addWidget(self.model_lbl)
        root.addLayout(kpis)
        root.addWidget(self.coverage_lbl)
        root.addWidget(self.last_error_lbl)
        root.addWidget(
            card(
                btn_w,
                muted("Dual-pass extraction against this project's schema. Pause aborts the in-flight FreeToken/Ollama request; the current paper is retried on resume. Each claim is written as JSON under the project claims/ folder as papers finish. Weights stay in VRAM until you Unload from VRAM on the Models tab."),
                title="Run",
            )
        )
        root.addWidget(card(self.log, title="Progress", expand=True), 1)

        self.poll = QTimer(self)
        self.poll.setInterval(1500)
        self.poll.timeout.connect(self._refresh_status)

    def on_show(self) -> None:
        self.storage.refresh()
        self._refresh_header()
        self._refresh_status()
        self.poll.start()

    def shutdown(self) -> None:
        self.poll.stop()
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
        if self._project_id:
            try:
                self.api.pause_run(self._project_id)
            except ExtractionError:
                pass
        from citehop.claims.llm import abort_generation

        abort_generation()
        if self._worker and self._worker.isRunning():
            self._worker.wait(5000)

    def hideEvent(self, event) -> None:  # noqa: ANN001
        if not (self._worker and self._worker.isRunning()):
            self.poll.stop()
        super().hideEvent(event)


    def set_project(self, project_id: str | None) -> None:
        super().set_project(project_id)
        self._refresh_header()
        if self.isVisible():
            self._refresh_status()

    def _refresh_header(self) -> None:
        try:
            self.model_lbl.setText(self.api.extraction_model()["label"])
        except Exception:  # noqa: BLE001
            self.model_lbl.setText("")
        if not self._project_id:
            self.project_lbl.setText("Select a project on the Projects tab.")
            return
        try:
            proj = self.api.get_project(self._project_id)
            self.project_lbl.setText(
                f"{proj.get('display_name') or proj['project_id']}  ·  {proj.get('corpus_dir')}"
            )
        except ProjectError as exc:
            self.project_lbl.setText(str(exc))

    def _refresh_status(self) -> None:
        if not self._project_id:
            self._apply_status(
                {
                    "status": "idle",
                    "papers_done": 0,
                    "papers_total": 0,
                    "papers_skipped": 0,
                    "tokens_used": 0,
                    "token_budget": 0,
                }
            )
            return
        try:
            status = self.api.run_status(self._project_id)
        except (ProjectError, OSError) as exc:
            self.log.appendPlainText(str(exc))
            return
        self._apply_status(status)

    def _apply_status(self, status: dict) -> None:
        done = int(status.get("papers_done") or 0)
        skipped = int(status.get("papers_skipped") or 0)
        total = int(status.get("papers_total") or 0)
        used = int(status.get("tokens_used") or 0)
        budget = int(status.get("token_budget") or 0)
        st = status.get("status") or "idle"
        self.kpi_papers.set_value(f"{done + skipped} / {total}", "done or skipped / queued")
        n_claims = int(status.get("claims_count") or status.get("claim_count") or 0)
        self.kpi_tokens.set_value(f"{used:,} / {budget:,}", "used / budget")
        self.kpi_status.set_value(st, f"{n_claims} claims")
        self.kpi_eta.set_value(_eta(status))
        cov = status.get("coverage")
        if isinstance(cov, dict):
            self.coverage_lbl.setText(_coverage_line(cov))
        running = bool(self._worker and self._worker.isRunning())
        self.start_btn.setEnabled(
            bool(self._project_id) and st in ("idle", "completed", "failed") and not running
        )
        self.pause_btn.setEnabled(running or st == "running")
        self.resume_btn.setEnabled(
            bool(self._project_id) and not running and st in ("paused", "running")
        )
        last = status.get("last_paper_error")
        if isinstance(last, dict) and last.get("error"):
            cid = last.get("paper_canonical_id") or ""
            msg = last.get("error") or ""
            if last.get("retryable"):
                kind = "retryable — Resume will try this paper again"
            else:
                kind = "hard error — this paper stays failed until you start a new run"
            self.last_error_lbl.setText(f"Last paper error ({cid}): {msg}  [{kind}]")
        else:
            self.last_error_lbl.setText("")
        err = status.get("error")
        if err:
            last = self.log.toPlainText().splitlines()
            if not last or last[-1] != err:
                self.log.appendPlainText(err)

    def _worker_running(self) -> bool:
        return bool(self._worker and self._worker.isRunning())

    def _start(self) -> None:
        if not self._project_id:
            QMessageBox.information(self, "Extract", "Select a project first.")
            return
        if self._worker_running():
            QMessageBox.information(
                self, "Extract", "Extraction is already running for this project."
            )
            return
        try:
            proj = self.api.get_project(self._project_id)
            warn = fetch_open_warning(Path(proj["corpus_dir"]))
        except (ProjectError, OSError) as exc:
            QMessageBox.warning(self, "Extract", str(exc))
            return
        if warn:
            go = QMessageBox.question(
                self,
                "Fetch still open",
                f"{warn}\n\nExtract anyway? Papers without a PDF will use abstracts.",
            )
            if go != QMessageBox.StandardButton.Yes:
                return
        try:
            status = self.api.start_run(self._project_id)
        except (ExtractionError, LLMError, SchemaError, ProjectError) as exc:
            QMessageBox.warning(self, "Extract", str(exc))
            self.log.appendPlainText(str(exc))
            return
        self.log.appendPlainText(f"Started run {status.get('run_id')}")
        extra = status.get("warning")
        if extra:
            self.log.appendPlainText(str(extra))
        self._apply_status(status)
        self._spawn_worker()

    def _pause(self) -> None:
        if not self._project_id:
            return
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
        try:
            status = self.api.pause_run(self._project_id)
            self._apply_status(status)
            self.log.appendPlainText(
                "Paused. Request aborted; in-flight paper stays pending. "
                "Weights stay in VRAM until Unload from VRAM on the Models tab."
            )
        except ExtractionError as exc:
            QMessageBox.warning(self, "Extract", str(exc))
        if self._worker and self._worker.isRunning():
            self._worker.wait(5000)

    def _resume(self) -> None:
        if not self._project_id:
            return
        if self._worker_running():
            QMessageBox.information(
                self, "Extract", "Extraction is already running for this project."
            )
            return
        try:
            status = self.api.resume_run(self._project_id)
        except (ExtractionError, LLMError, SchemaError, ProjectError) as exc:
            QMessageBox.warning(self, "Extract", str(exc))
            return
        n_stale = status.get("requeued_stale_text") or 0
        if n_stale:
            self.log.appendPlainText(
                f"Resumed. Requeued {n_stale} papers whose text file is newer than extract."
            )
        else:
            self.log.appendPlainText("Resumed.")
        self._apply_status(status)
        if status.get("status") == "running":
            self._spawn_worker()

    def _open_exports(self) -> None:
        if not self._project_id:
            QMessageBox.information(self, "Extract", "Select a project first.")
            return
        try:
            proj = self.api.get_project(self._project_id)
        except ProjectError as exc:
            QMessageBox.warning(self, "Extract", str(exc))
            return
        folder = Path(proj["project_dir"]) / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _spawn_worker(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        assert self._project_id
        worker = ExtractWorker(self._project_id)
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.failed.connect(self._on_fail)
        worker.finished.connect(self._on_worker_finished)
        worker.start()
        self.poll.start()

    @Slot(object)
    def _on_progress(self, status: object) -> None:
        if isinstance(status, dict):
            self._apply_status(status)
            done = status.get("papers_done")
            total = status.get("papers_total")
            self.log.appendPlainText(
                f"{status.get('status')}  papers {done}/{total}  tokens {status.get('tokens_used')}"
            )
            if status.get("status") == "completed":
                n = status.get("claim_count") or status.get("claims_count")
                export_path = status.get("export_path")
                claims_dir = status.get("claims_dir")
                if export_path:
                    self.log.appendPlainText(f"Handoff JSON: {export_path}")
                elif claims_dir:
                    self.log.appendPlainText(f"Claims JSON: {n} files in {claims_dir}")

    @Slot(str)
    def _on_fail(self, message: str) -> None:
        self.log.appendPlainText(message)
        if self._project_id:
            try:
                self.api.pause_run(self._project_id)
            except ExtractionError:
                pass
        QMessageBox.warning(self, "Extract", message)
        self._refresh_status()

    @Slot()
    def _on_worker_finished(self) -> None:
        self._worker = None
        self._refresh_status()


def _coverage_line(cov: dict) -> str:
    parts: list[str] = []
    back = cov.get("backward_ingested")
    fwd = cov.get("forward_ingested")
    if back is not None:
        parts.append(f"{back} cited by seed in corpus")
    if fwd is not None:
        parts.append(f"{fwd} citing the seed in corpus")
    s2_back = cov.get("s2_reference_count_reported")
    s2_fwd = cov.get("s2_citation_count_reported")
    oa_back = cov.get("openalex_referenced_works_count")
    oa_fwd = cov.get("openalex_cited_by_count")
    if s2_back or s2_fwd:
        parts.append(f"S2 listed {s2_back or '—'} refs · {s2_fwd or '—'} citations at resolve")
    if oa_back or oa_fwd:
        parts.append(
            f"OpenAlex listed {oa_back or '—'} referenced works · {oa_fwd or '—'} citations at resolve"
        )
    fetch_open = int(cov.get("fetch_open") or 0)
    if fetch_open:
        parts.append(f"fetch still open: {fetch_open} pending or retry")
    missing = int(cov.get("corpus_not_in_run") or 0)
    if missing:
        parts.append(f"{missing} corpus papers not in this run")
    newer = int(cov.get("done_text_newer") or 0)
    if newer:
        parts.append(f"{newer} done papers have newer text files")
    return " · ".join(parts)


def _eta(status: dict) -> str:
    pending = int(status.get("papers_pending") or 0)
    done = int(status.get("papers_done") or 0)
    started = status.get("started_at")
    if not pending:
        return "—" if status.get("status") != "completed" else "done"
    if not done or not started:
        return "…"
    try:
        t0 = datetime.fromisoformat(started)
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    except ValueError:
        return "…"
    if elapsed <= 0:
        return "…"
    remaining = elapsed / done * pending
    if remaining < 60:
        return f"{int(remaining)}s"
    if remaining < 3600:
        return f"{int(remaining // 60)}m"
    return f"{remaining / 3600:.1f}h"
