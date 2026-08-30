from __future__ import annotations

import shutil
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from citehop import icon_path
from citehop.catalog import CorpusSummary, summarize_corpus
from citehop.config import CORPORA_DIR
from citehop.pipeline import BuildPaused, CorpusBuilder
from citehop.seed import SeedQuery, get_named_seed
from citehop.ui.pages.analyze import AnalyzePage
from citehop.ui.pages.corpus import CorpusPage
from citehop.ui.pages.extract import ExtractPage
from citehop.ui.pages.models import ModelsPage
from citehop.ui.pages.projects import ProjectsPage
from citehop.ui.pages.review import ReviewPage
from citehop.ui.pages.schema import SchemaPage
from citehop.ui.theme import apply_theme

PAGES = [
    ("Analyze", AnalyzePage),
    ("Corpus", CorpusPage),
    ("Models", ModelsPage),
    ("Projects", ProjectsPage),
    ("Schema", SchemaPage),
    ("Extract", ExtractPage),
    ("Review", ReviewPage),
]
HEAD_BEFORE = {"Analyze": "CORPUS", "Models": "MODEL", "Projects": "PROJECT"}


class BuildWorker(QThread):
    log = Signal(str)
    progress = Signal(object)
    finished_ok = Signal(object)

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.payload = payload
        self._stop = threading.Event()
        self._builder: CorpusBuilder | None = None

    def request_pause(self) -> None:
        self._stop.set()
        self.requestInterruption()
        builder = self._builder
        if builder is not None:
            builder.pause()

    def run(self) -> None:
        builder: CorpusBuilder | None = None
        try:
            builder = _make_builder(
                self.payload, self.log.emit, self._stop, self.progress.emit
            )
            self._builder = builder
            if self._stop.is_set():
                builder.pause()
            if self.payload.get("mode") == "fetch_pdfs":
                builder.fetch_full_texts()
            else:
                builder.run()
            self.progress.emit(builder.snapshot_counts())
            self.finished_ok.emit(
                {
                    "ok": True,
                    "corpus_dir": str(builder.corpus_dir),
                    "mode": self.payload.get("mode"),
                }
            )
        except BuildPaused:
            path = str(builder.corpus_dir) if builder else ""
            if builder is not None:
                try:
                    self.progress.emit(builder.snapshot_counts())
                except Exception:
                    pass
            self.finished_ok.emit(
                {
                    "ok": True,
                    "paused": True,
                    "corpus_dir": path,
                    "mode": self.payload.get("mode"),
                }
            )
        except SystemExit as exc:
            msg = exc.code if isinstance(exc.code, str) else (str(exc) or "Stopped")
            self.finished_ok.emit({"ok": False, "error": msg})
        except Exception as exc:  # noqa: BLE001
            self.finished_ok.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            if builder is not None:
                builder.manifest.close()
            self._builder = None


def _seed_from_payload(payload: dict[str, Any]) -> SeedQuery:
    pdf = Path(payload["pdf"]).expanduser().resolve() if payload.get("pdf") else None
    seed = SeedQuery(
        doi=payload.get("doi"),
        arxiv_id=payload.get("arxiv"),
        title=payload.get("title"),
        author=payload.get("author"),
        venue=payload.get("venue"),
        year=payload.get("year"),
        pdf=pdf,
        preset=payload.get("preset"),
    ).normalized()
    if seed.preset:
        base = get_named_seed(seed.preset)
        if base:
            seed = replace(
                base,
                doi=seed.doi or base.doi,
                arxiv_id=seed.arxiv_id or base.arxiv_id,
                title=seed.title or base.title,
                author=seed.author or base.author,
                venue=seed.venue or base.venue,
                year=seed.year or base.year,
                pdf=seed.pdf or base.pdf,
                preset=seed.preset,
            ).normalized()
    return seed


def _make_builder(
    payload: dict[str, Any],
    log,  # noqa: ANN001
    stop: threading.Event,
    progress=None,  # noqa: ANN001
) -> CorpusBuilder:
    if payload.get("mode") == "fetch_pdfs":
        corpus_dir = Path(payload["corpus_dir"]).expanduser()
        if not (corpus_dir / "manifest.db").is_file():
            raise SystemExit(f"No manifest.db in {corpus_dir}")
        log(f"Fetching remaining OA/arXiv PDFs in {corpus_dir}")
        return CorpusBuilder(
            corpus_dir, log=log, stop=stop, progress=progress, write_readme=False
        )
    seed = _seed_from_payload(payload)
    if not (seed.doi or seed.arxiv_id or seed.title or seed.preset):
        raise SystemExit("Provide a PDF with identifiers, or a DOI / arXiv id / title.")
    corpus_dir = seed.default_corpus_dir()
    try:
        corpus_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(
            f"Cannot write {corpus_dir}. Mount the Vault drive first. ({exc})"
        ) from exc
    if seed.pdf and seed.pdf.is_file():
        dest = corpus_dir / "seed.pdf"
        if seed.pdf.resolve() != dest.resolve():
            shutil.copy2(seed.pdf, dest)
        seed = replace(seed, pdf=dest)
    log(f"Corpus directory: {corpus_dir}")
    kwargs: dict[str, Any] = {
        "seed": seed,
        "log": log,
        "write_readme": payload.get("mode") == "full",
        "stop": stop,
        "progress": progress,
    }
    if payload.get("mode") != "full":
        kwargs["sample_backward"] = int(payload.get("n_backward") or 5)
        kwargs["sample_forward"] = int(payload.get("n_forward") or 5)
    return CorpusBuilder(corpus_dir, **kwargs)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CiteHop")
        self.resize(1280, 880)
        self.setMinimumSize(980, 640)
        icon = icon_path()
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        self._worker: BuildWorker | None = None
        self._run_dir: Path | None = None
        self._project_id: str | None = None
        self._poll_error_logged = False

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 18, 12, 12)
        side.setSpacing(4)
        brand = QLabel("CiteHop")
        brand.setObjectName("brand")
        sub = QLabel("Finds citations and reviews literature claims locally")
        sub.setObjectName("brandSub")
        side.addWidget(brand)
        side.addWidget(sub)
        side.addSpacing(8)

        self.stack = QStackedWidget()
        self.pages: list[tuple[str, Any]] = []
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for i, (name, cls) in enumerate(PAGES):
            if name in HEAD_BEFORE:
                heading = QLabel(HEAD_BEFORE[name])
                heading.setObjectName("navSection")
                side.addWidget(heading)
            page = cls()
            btn = QPushButton(name)
            btn.setObjectName("nav")
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            self.nav_group.addButton(btn, i)
            side.addWidget(btn)
            if isinstance(page, AnalyzePage):
                page.start_requested.connect(self.start_analysis)
                page.pause_requested.connect(self.pause_analysis)
            if isinstance(page, CorpusPage):
                page.start_requested.connect(self.start_analysis)
                page.pause_requested.connect(self.pause_analysis)
            page.project_changed.connect(self._set_project)
            page.show_page.connect(self._show_page)
            self.stack.addWidget(page)
            self.pages.append((name, page))
        self.nav_group.idClicked.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(self._on_stack)
        side.addStretch()
        self.side_status = QLabel("Idle")
        self.side_status.setObjectName("brandSub")
        self.side_status.setWordWrap(True)
        side.addWidget(self.side_status)

        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_scroll.setFixedWidth(220)
        side_scroll.setWidget(sidebar)

        main = QWidget()
        main_l = QVBoxLayout(main)
        main_l.setContentsMargins(24, 18, 24, 18)
        main_l.setSpacing(12)

        self.banner = QFrame()
        self.banner.setObjectName("banner")
        self.banner.setProperty("level", "ok")
        banner_l = QHBoxLayout(self.banner)
        self.banner_text = QLabel("")
        self.banner_text.setWordWrap(True)
        banner_l.addWidget(self.banner_text)
        self.banner.hide()
        main_l.addWidget(self.banner)

        pills = QHBoxLayout()
        self.pill_mode, self.pill_mode_lbl = self._make_pill("Mode")
        self.pill_run, self.pill_run_lbl = self._make_pill("Run")
        pills.addWidget(self.pill_mode)
        pills.addWidget(self.pill_run)
        pills.addStretch()
        main_l.addLayout(pills)
        main_l.addWidget(self.stack, 1)

        layout.addWidget(side_scroll)
        layout.addWidget(main, 1)

        status = QStatusBar()
        self.setStatusBar(status)
        status.showMessage(f"CiteHop  ·  live APIs  ·  {CORPORA_DIR}/<slug>/")

        self.poll = QTimer(self)
        self.poll.setInterval(1500)
        self.poll.timeout.connect(self._poll_run)

        self.page_by_name = {name: page for name, page in self.pages}

    def _on_stack(self, index: int) -> None:
        btn = self.nav_group.button(index)
        if btn:
            btn.setChecked(True)
        if 0 <= index < len(self.pages):
            self.pages[index][1].on_show()

    def _set_project(self, project_id: str) -> None:
        self._project_id = project_id
        for _, page in self.pages:
            page.set_project(project_id)
        self.pill_run_lbl.setText(f"Project  {project_id}")
        self.statusBar().showMessage(f"Project  {project_id}")

    @staticmethod
    def _make_pill(prefix: str) -> tuple[QFrame, QLabel]:
        frame = QFrame()
        frame.setObjectName("card")
        frame.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 8, 14, 8)
        label = QLabel(f"{prefix}  —")
        label.setObjectName("muted")
        layout.addWidget(label)
        return frame, label

    def _analyze(self) -> AnalyzePage:
        return self.page_by_name["Analyze"]

    def _corpus(self) -> CorpusPage:
        return self.page_by_name["Corpus"]

    def _show_page(self, name: str) -> None:
        for i, (page_name, _) in enumerate(self.pages):
            if page_name == name:
                self.stack.setCurrentIndex(i)
                btn = self.nav_group.button(i)
                if btn:
                    btn.setChecked(True)
                return

    def _set_banner(self, text: str, level: str) -> None:
        self.banner_text.setText(text)
        self.banner.setProperty("level", level)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner.setVisible(bool(text.strip()))

    @Slot(dict)
    def start_analysis(self, payload: dict) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "An analysis is already running.")
            return
        fetch_only = payload.get("mode") == "fetch_pdfs"
        if fetch_only:
            corpus_dir = Path(payload["corpus_dir"]).expanduser()
            if not (corpus_dir / "manifest.db").is_file():
                QMessageBox.warning(self, "Corpus", f"No manifest.db in {corpus_dir}")
                return
            self._run_dir = corpus_dir
            self._analyze().append_log(f"Fetching remaining PDFs in {corpus_dir}")
            self.pill_mode_lbl.setText("Mode  fetch PDFs")
            self._set_banner(
                "Fetching remaining OA/arXiv PDFs. Pause on Corpus or Analyze to stop.",
                "ok",
            )
        else:
            self._run_dir = _seed_from_payload(payload).default_corpus_dir()
            self._analyze().append_log("Starting analysis…")
            self.pill_mode_lbl.setText(f"Mode  {payload.get('mode', 'sample')}")
            self._set_banner(
                "Analysis running. You can switch to Corpus to watch papers appear.",
                "ok",
            )
        self._analyze().set_run_state("running")
        self._corpus().set_pipeline_busy(True)
        self.pill_run_lbl.setText("Run  in progress")
        self.side_status.setText(
            "Fetching remaining PDFs…" if fetch_only else "Fetching 1-hop corpus…"
        )
        worker = BuildWorker(payload)
        self._worker = worker
        worker.log.connect(self._analyze().append_log)
        worker.log.connect(self._corpus().set_fetch_line)
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_done)
        worker.start()
        self.poll.start()
        self._poll_error_logged = False

    def pause_analysis(self) -> None:
        if not (self._worker and self._worker.isRunning()):
            return
        self._analyze().append_log("Pausing… in-flight fetch is aborted; already-written papers stay.")
        self._worker.request_pause()

    @Slot(object)
    def _on_done(self, result: object) -> None:
        self.poll.stop()
        self._corpus().set_pipeline_busy(False)
        payload = result if isinstance(result, dict) else {"ok": False, "error": str(result)}
        fetch_only = payload.get("mode") == "fetch_pdfs"
        corpus_dir = str(payload.get("corpus_dir") or "")
        if corpus_dir:
            self._run_dir = Path(corpus_dir)
            self._poll_run()
            self._corpus().reload_corpora(select_path=corpus_dir)
        if payload.get("paused"):
            if fetch_only:
                self._analyze().set_run_state("idle")
                msg = "PDF fetch paused. Click Fetch remaining PDFs on Corpus to continue."
                self._analyze().append_log(msg)
                self._corpus().set_fetch_line(msg)
                self.pill_run_lbl.setText("Run  paused")
                self.side_status.setText("Paused")
                self._set_banner(msg, "ok")
                self.statusBar().showMessage(f"Paused  ·  {corpus_dir}")
                return
            self._analyze().set_run_state("paused")
            self._analyze().append_log(
                "Paused. Nothing is lost — resume or Start analysis with the same seed to continue."
            )
            self.pill_run_lbl.setText("Run  paused")
            self.side_status.setText("Paused")
            self._set_banner(
                "Analysis paused. Progress is in the corpus folder. Resume to continue.",
                "ok",
            )
            self.statusBar().showMessage(f"Paused  ·  {corpus_dir}")
            return
        self._analyze().set_run_state("idle")
        if not payload.get("ok"):
            err = str(payload.get("error") or "Analysis failed")
            self._analyze().append_log(err)
            self.pill_run_lbl.setText("Run  failed")
            self.side_status.setText("Failed")
            self._set_banner(err, "critical")
            self.statusBar().showMessage(err)
            QMessageBox.warning(self, "Analysis failed", err)
            return
        self.pill_run_lbl.setText("Run  complete")
        self.side_status.setText("Idle")
        if fetch_only:
            msg = f"PDF fetch finished. Files are in {corpus_dir}/raw/."
            self._analyze().append_log(msg)
            self._corpus().set_fetch_line(msg)
            self._set_banner(msg, "ok")
            self.statusBar().showMessage(f"Complete  ·  {corpus_dir}")
            return
        self._set_banner(
            f"Finished. Corpus is in {corpus_dir} (metadata + OA PDFs in raw/). "
            "Next: Models → Projects → Schema → Extract.",
            "ok",
        )
        self.statusBar().showMessage(f"Complete  ·  {corpus_dir}")
        go = QMessageBox.question(
            self,
            "Analysis complete",
            f"Corpus is in:\n{corpus_dir}\n\n"
            "Open the Corpus tab, or go to Projects next to extract claims?",
        )
        if go == QMessageBox.StandardButton.Yes:
            self._show_page("Corpus")

    def _on_progress(self, payload: object) -> None:
        summary = _summary_from_progress(payload)
        if summary is None:
            return
        self._apply_live_summary(summary)

    def _apply_live_summary(self, summary) -> None:  # noqa: ANN001
        self._analyze().apply_summary(summary)
        self._corpus().apply_summary(summary)

    def _poll_run(self) -> None:
        path = self._run_dir
        if path is None and self._worker and self._worker.isRunning():
            return
        if path and path.is_dir():
            try:
                summary = summarize_corpus(path)
            except Exception as exc:  # noqa: BLE001
                if not self._poll_error_logged:
                    self._analyze().append_log(f"Could not refresh counts from disk: {exc}")
                    self._poll_error_logged = True
                return
            self._apply_live_summary(summary)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        extract = self.page_by_name.get("Extract")
        if isinstance(extract, ExtractPage):
            extract.shutdown()
        if self._worker and self._worker.isRunning():
            self.pause_analysis()
            self._worker.wait(8000)
        super().closeEvent(event)


def _summary_from_progress(payload: object) -> CorpusSummary | None:
    if not isinstance(payload, dict):
        return None
    path = Path(payload.get("path") or ".")
    return CorpusSummary(
        slug=str(payload.get("slug") or path.name),
        path=path,
        seed_title=str(payload.get("slug") or path.name),
        seed_id=None,
        seed_doi=None,
        seed_arxiv=None,
        year=None,
        paper_count=int(payload.get("paper_count") or 0),
        relation_counts=dict(payload.get("relation_counts") or {}),
        status_counts=dict(payload.get("status_counts") or {}),
        run_mode=payload.get("run_mode"),
        started_at=None,
        finished_at=None,
        pdf_count=int(payload.get("pdf_count") or 0),
        success_count=int(payload.get("success_count") or 0),
    )


def run_app() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("CiteHop")
    app.setOrganizationName("CiteHop")
    app.setDesktopFileName("citehop")
    apply_theme(app)
    window = MainWindow()

    def _abort_model() -> None:
        from citehop.claims.llm import abort_generation

        abort_generation()

    app.aboutToQuit.connect(_abort_model)
    window.show()
    return app.exec()
