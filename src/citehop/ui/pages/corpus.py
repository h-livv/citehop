from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from citehop.catalog import (
    CorpusSummary,
    coverage_caption,
    hop_counts,
    list_corpora,
    load_papers,
    pdf_over_cited_citing,
)
from citehop.config import CORPORA_DIR
from citehop.ui.pages import Page
from citehop.ui.widgets import Kpi, card, muted


COLUMNS = ("Title", "Year", "Relation", "Status", "DOI", "arXiv", "Full text")


class CorpusPage(Page):
    pause_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._papers: list[dict] = []
        self._current_path: Path | None = None
        self._pipeline_busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        pick = QHBoxLayout()
        pick.addWidget(QLabel("Root paper"))
        self.selector = QComboBox()
        self.selector.setMinimumWidth(420)
        self.selector.currentIndexChanged.connect(self._on_select)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload_corpora)
        self.open_folder = QPushButton("Open folder")
        self.open_folder.clicked.connect(self._open_folder)
        self.fetch_pdfs = QPushButton("Fetch remaining PDFs")
        self.fetch_pdfs.clicked.connect(self._fetch_pdfs)
        self.pause_fetch = QPushButton("Pause fetch")
        self.pause_fetch.setEnabled(False)
        self.pause_fetch.clicked.connect(self.pause_requested.emit)
        pick.addWidget(self.selector, 1)
        pick.addWidget(refresh)
        pick.addWidget(self.open_folder)
        pick.addWidget(self.fetch_pdfs)
        pick.addWidget(self.pause_fetch)
        pick_wrap = QWidget()
        pick_wrap.setLayout(pick)
        root.addWidget(
            card(
                pick_wrap,
                muted(
                    f"Stored in {CORPORA_DIR}. Each corpus is one seed plus the papers it cites "
                    "and the papers that cite it. Fetch remaining PDFs retries OA/arXiv copies."
                ),
                title="Corpora",
            )
        )

        kpis = QGridLayout()
        kpis.setSpacing(10)
        self.kpi_back = Kpi("Cited by seed")
        self.kpi_fwd = Kpi("Citing the seed")
        self.kpi_text = Kpi("With full text")
        self.kpi_pdfs = Kpi("PDFs")
        for i, w in enumerate((self.kpi_back, self.kpi_fwd, self.kpi_text, self.kpi_pdfs)):
            kpis.addWidget(w, 0, i)
        root.addLayout(kpis)
        self.coverage_lbl = muted("")
        root.addWidget(self.coverage_lbl)
        self.fetch_line = muted("")
        root.addWidget(self.fetch_line)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, DOI, arXiv, authors…")
        self.search.textChanged.connect(self._apply_filter)
        self.relation = QComboBox()
        self.relation.addItem("All relations", None)
        self.relation.addItem("Seed", "seed")
        self.relation.addItem("Cited by seed", "backward_reference")
        self.relation.addItem("Cites seed", "forward_citation")
        self.relation.currentIndexChanged.connect(self._apply_filter)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.relation)
        filt_wrap = QWidget()
        filt_wrap.setLayout(filters)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self._on_row)
        self.table.cellDoubleClicked.connect(self._open_pdf)

        self.table.setMinimumHeight(260)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Select a paper to see authors, abstract, and identifiers.")
        self.detail.setMinimumHeight(180)
        self.open_pdf = QPushButton("Open PDF")
        self.open_pdf.setEnabled(False)
        self.open_pdf.clicked.connect(self._open_selected_pdf)
        self.open_note = QPushButton("Open note")
        self.open_note.setEnabled(False)
        self.open_note.clicked.connect(self._open_note)
        detail_col = QWidget()
        detail_l = QVBoxLayout(detail_col)
        detail_l.setContentsMargins(0, 0, 0, 0)
        detail_l.addWidget(self.detail, 1)
        btns = QHBoxLayout()
        btns.addWidget(self.open_pdf)
        btns.addWidget(self.open_note)
        btns.addStretch()
        detail_l.addLayout(btns)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(filt_wrap)
        body.addWidget(self.table, 1)
        body.addWidget(detail_col)
        body_wrap = QWidget()
        body_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        body_wrap.setLayout(body)
        root.addWidget(card(body_wrap, title="Fetched papers", expand=True), 1)

        self.reload_corpora()

    def reload_corpora(self, select_path: str | None = None) -> None:
        current = select_path or (
            self.selector.currentData() if self.selector.count() else None
        )
        self.selector.blockSignals(True)
        self.selector.clear()
        corpora = [item for item in list_corpora() if item.paper_count]
        if not corpora:
            self.selector.addItem("No corpora yet — analyze a seed first", None)
            self.selector.blockSignals(False)
            self._load_path(None)
            return
        for summary in corpora:
            self.selector.addItem(f"{summary.label}  ·  {summary.paper_count} papers", str(summary.path))
        self.selector.blockSignals(False)
        if current:
            idx = self.selector.findData(current)
            if idx >= 0:
                self.selector.setCurrentIndex(idx)
                self._load_path(Path(current))
                return
        self._on_select()

    def _on_select(self) -> None:
        data = self.selector.currentData()
        self._load_path(Path(data) if data else None)

    def _load_path(self, path: Path | None) -> None:
        self._current_path = path
        if not path:
            self._set_summary(None)
            self._fill_table([])
            self._sync_fetch_buttons()
            return
        from citehop.catalog import summarize_corpus

        self._set_summary(summarize_corpus(path))
        self._fill_table(load_papers(path))
        self._sync_fetch_buttons()

    def set_pipeline_busy(self, busy: bool) -> None:
        self._pipeline_busy = busy
        self._sync_fetch_buttons()

    def set_fetch_line(self, text: str) -> None:
        self.fetch_line.setText(text)

    def _sync_fetch_buttons(self) -> None:
        has = bool(self._current_path and (self._current_path / "manifest.db").is_file())
        self.fetch_pdfs.setEnabled(has and not self._pipeline_busy)
        self.pause_fetch.setEnabled(self._pipeline_busy)

    def _fetch_pdfs(self) -> None:
        if not self._current_path or not (self._current_path / "manifest.db").is_file():
            return
        self.start_requested.emit(
            {"mode": "fetch_pdfs", "corpus_dir": str(self._current_path)}
        )

    def apply_summary(self, summary: CorpusSummary | None) -> None:
        self._set_summary(summary)

    def on_show(self) -> None:
        if self._current_path:
            from citehop.catalog import summarize_corpus

            self.apply_summary(summarize_corpus(self._current_path))

    def _set_summary(self, summary: CorpusSummary | None) -> None:
        if not summary:
            for kpi in (self.kpi_back, self.kpi_fwd, self.kpi_text, self.kpi_pdfs):
                kpi.set_value("—", "")
            self.coverage_lbl.setText("")
            return
        seed, back, fwd, total = hop_counts(summary)
        self.kpi_back.set_value(str(back), "papers the seed cites")
        self.kpi_fwd.set_value(str(fwd), "papers that cite the seed")
        self.kpi_text.set_value(
            str(summary.success_count),
            f"of {total} papers in corpus" if total else "extracted text on disk",
        )
        pdfs, hop = pdf_over_cited_citing(summary)
        self.kpi_pdfs.set_value(
            f"{pdfs} / {hop}" if hop else (str(pdfs) if pdfs else "—"),
            "files in raw/ of cited + citing",
        )
        extra = f"{seed} seed" if seed else ""
        cap = coverage_caption(summary)
        self.coverage_lbl.setText(" · ".join(p for p in (extra, cap) if p))

    def _fill_table(self, papers: list[dict]) -> None:
        self._papers = papers
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(papers))
        for i, paper in enumerate(papers):
            values = [
                paper.get("title") or "",
                str(paper.get("year") or ""),
                paper.get("relation_label") or paper.get("relation_to_seed") or "",
                paper.get("manifest_status") or "",
                paper.get("doi") or "",
                paper.get("arxiv_id") or "",
                "yes" if paper.get("full_text_available") else "no",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, i)
                self.table.setItem(i, col, item)
        self.table.setSortingEnabled(True)
        self._apply_filter()
        self.detail.clear()
        self.open_pdf.setEnabled(False)
        self.open_note.setEnabled(False)

    def _open_folder(self) -> None:
        if self._current_path and self._current_path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current_path)))

    def _open_note(self) -> None:
        paper = self._selected_paper()
        if not paper or not paper.get("note_path"):
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(paper["note_path"]))

    def _apply_filter(self) -> None:
        needle = self.search.text().strip().lower()
        rel = self.relation.currentData()
        for row in range(self.table.rowCount()):
            title_item = self.table.item(row, 0)
            idx = title_item.data(Qt.ItemDataRole.UserRole) if title_item else None
            paper = self._papers[idx] if isinstance(idx, int) and 0 <= idx < len(self._papers) else {}
            hay = " ".join(
                str(paper.get(k) or "")
                for k in ("title", "doi", "arxiv_id", "canonical_id", "venue")
            ).lower()
            authors = paper.get("authors") or []
            if isinstance(authors, list):
                hay += " " + " ".join(str(a) for a in authors).lower()
            rel_ok = rel is None or paper.get("relation_to_seed") == rel
            text_ok = (not needle) or needle in hay
            self.table.setRowHidden(row, not (rel_ok and text_ok))

    def _selected_paper(self) -> dict | None:
        items = self.table.selectedItems()
        if not items:
            return None
        row = items[0].row()
        title_item = self.table.item(row, 0)
        if not title_item:
            return None
        idx = title_item.data(Qt.ItemDataRole.UserRole)
        if isinstance(idx, int) and 0 <= idx < len(self._papers):
            return self._papers[idx]
        return None

    def _on_row(self) -> None:
        paper = self._selected_paper()
        if not paper:
            self.detail.clear()
            self.open_pdf.setEnabled(False)
            self.open_note.setEnabled(False)
            return
        authors = paper.get("authors") or []
        author_line = ", ".join(str(a) for a in authors) if isinstance(authors, list) else str(authors)
        lines = [
            paper.get("title") or "(no title)",
            "",
            author_line,
            f"{paper.get('venue') or ''}  {paper.get('year') or ''}".strip(),
            "",
            f"id    {paper.get('canonical_id') or ''}",
            f"doi   {paper.get('doi') or '—'}",
            f"arxiv {paper.get('arxiv_id') or '—'}",
            f"s2    {paper.get('semantic_scholar_id') or '—'}",
            f"openalex {paper.get('openalex_id') or '—'}",
            f"relation  {paper.get('relation_label')}",
            f"status    {paper.get('manifest_status')}  fetch={paper.get('fetch_method') or '—'}",
            f"source    {paper.get('source_url') or '—'}",
        ]
        if paper.get("failure_reason"):
            lines.append(f"failure  {paper['failure_reason']}")
        abstract = (paper.get("abstract") or "").strip()
        if abstract:
            lines += ["", "Abstract", abstract]
        self.detail.setPlainText("\n".join(lines))
        self.open_pdf.setEnabled(bool(paper.get("pdf_path")))
        self.open_note.setEnabled(bool(paper.get("note_path")))

    def _open_selected_pdf(self) -> None:
        self._open_pdf()

    def _open_pdf(self, *_args) -> None:  # noqa: ANN001
        paper = self._selected_paper()
        if not paper or not paper.get("pdf_path"):
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(paper["pdf_path"]))
