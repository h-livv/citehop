from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from citehop.catalog import CorpusSummary
from citehop.extract import inspect_pdf
from citehop.seed import PRESETS
from citehop.ui.pages import Page
from citehop.ui.widgets import DropZone, Kpi, card, muted


class AnalyzePage(Page):
    pause_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pdf: Path | None = None
        self._preset: str | None = None
        self._run_state = "idle"
        self._last_payload: dict | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        kpis = QGridLayout()
        kpis.setSpacing(10)
        self.kpi_papers = Kpi("Papers")
        self.kpi_back = Kpi("Cited by seed")
        self.kpi_fwd = Kpi("Cites seed")
        self.kpi_text = Kpi("Full text")
        for i, w in enumerate((self.kpi_papers, self.kpi_back, self.kpi_fwd, self.kpi_text)):
            kpis.addWidget(w, 0, i)
        root.addLayout(kpis)

        self.drop = DropZone()
        self.drop.set_handler(self.load_pdf)
        choose = QPushButton("Choose PDF")
        choose.clicked.connect(self._choose_pdf)
        drop_row = QHBoxLayout()
        drop_row.addWidget(self.drop, 1)
        drop_col = QVBoxLayout()
        drop_col.addWidget(choose)
        drop_col.addStretch()
        drop_row.addLayout(drop_col)
        wrap = QWidget()
        wrap.setLayout(drop_row)
        self.start = QPushButton("Start analysis")
        self.start.setObjectName("accent")
        self.start.setMinimumHeight(40)
        self.start.clicked.connect(self._start)
        self.pause = QPushButton("Pause")
        self.pause.setMinimumHeight(40)
        self.pause.setEnabled(False)
        self.pause.clicked.connect(self._pause)
        self.resume = QPushButton("Resume")
        self.resume.setMinimumHeight(40)
        self.resume.setEnabled(False)
        self.resume.clicked.connect(self._resume)
        btns = QHBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.addWidget(self.start, 1)
        btns.addWidget(self.pause)
        btns.addWidget(self.resume)
        btn_w = QWidget()
        btn_w.setLayout(btns)
        root.addWidget(
            card(
                wrap,
                btn_w,
                title="Seed paper",
                subtitle="Upload a PDF, then start. Pause keeps progress in the corpus folder; resume or Start again with the same seed continues. Closing the app pauses the same way.",
            )
        )

        self.title = QLineEdit()
        self.title.setPlaceholderText("Title")
        self.doi = QLineEdit()
        self.doi.setPlaceholderText("10.xxxx/…")
        self.arxiv = QLineEdit()
        self.arxiv.setPlaceholderText("optional")
        self.author = QLineEdit()
        self.author.setPlaceholderText("Family name, e.g. Di Meglio")
        self.venue = QLineEdit()
        self.venue.setPlaceholderText("Optional venue")
        self.year = QLineEdit()
        self.year.setPlaceholderText("Optional year")

        def field_row(caption: str, widget: QWidget) -> QHBoxLayout:
            row = QHBoxLayout()
            lab = QLabel(caption)
            lab.setFixedWidth(56)
            widget.setMinimumHeight(32)
            row.addWidget(lab)
            row.addWidget(widget, 1)
            return row

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addLayout(field_row("Title", self.title))
        left.addLayout(field_row("DOI", self.doi))
        left.addLayout(field_row("Author", self.author))
        right = QVBoxLayout()
        right.setSpacing(6)
        right.addLayout(field_row("arXiv", self.arxiv))
        right.addLayout(field_row("Year", self.year))
        right.addLayout(field_row("Venue", self.venue))
        forms = QHBoxLayout()
        forms.setSpacing(16)
        forms.addLayout(left, 3)
        forms.addLayout(right, 2)
        form_wrap = QWidget()
        form_wrap.setLayout(forms)
        form_wrap.setMinimumHeight(120)

        self.sample_radio = QRadioButton("Sample checkpoint")
        self.full_radio = QRadioButton("Full 1-hop")
        self.sample_radio.setChecked(True)
        modes = QButtonGroup(self)
        modes.addButton(self.sample_radio)
        modes.addButton(self.full_radio)
        self.n_back = QSpinBox()
        self.n_back.setRange(1, 50)
        self.n_back.setValue(5)
        self.n_fwd = QSpinBox()
        self.n_fwd.setRange(1, 50)
        self.n_fwd.setValue(5)
        self.preset_btn = QPushButton("qc4hep")
        self.preset_btn.clicked.connect(self._load_qc4hep)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.sample_radio)
        mode_row.addWidget(self.full_radio)
        mode_row.addSpacing(12)
        mode_row.addWidget(QLabel("Backward"))
        mode_row.addWidget(self.n_back)
        mode_row.addWidget(QLabel("Forward"))
        mode_row.addWidget(self.n_fwd)
        mode_row.addStretch()
        mode_row.addWidget(muted("Named seed"))
        mode_row.addWidget(self.preset_btn)
        self.confirm = QCheckBox("I confirmed the sample looks right — run the full 1-hop corpus")
        self.confirm.hide()
        self.confirm.toggled.connect(self._refresh_start)
        self.sample_radio.toggled.connect(self._mode_changed)
        mode_wrap = QWidget()
        mode_l = QVBoxLayout(mode_wrap)
        mode_l.setContentsMargins(0, 0, 0, 0)
        mode_l.setSpacing(10)
        mode_l.addWidget(form_wrap)
        mode_l.addLayout(mode_row)
        mode_l.addWidget(self.confirm)
        id_card = card(mode_wrap, title="Identifiers and mode")
        id_card.setMinimumHeight(250)
        root.addWidget(id_card)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setPlaceholderText("Run log will appear here.")
        self.log.setMinimumHeight(72)
        root.addWidget(card(self.log, title="Log"), 1)

        for field in (self.title, self.doi, self.arxiv, self.author):
            field.textChanged.connect(self._refresh_start)

        self._refresh_start()

    def _mode_changed(self, sample_on: bool) -> None:
        self.n_back.setEnabled(sample_on)
        self.n_fwd.setEnabled(sample_on)
        self.confirm.setVisible(not sample_on)
        self._refresh_start()

    def _refresh_start(self) -> None:
        has_id = bool(
            self.doi.text().strip()
            or self.arxiv.text().strip()
            or self.title.text().strip()
        )
        full_ok = self.sample_radio.isChecked() or self.confirm.isChecked()
        idle = self._run_state == "idle"
        paused = self._run_state == "paused"
        running = self._run_state == "running"
        self.start.setEnabled(has_id and full_ok and idle)
        self.pause.setEnabled(running)
        self.resume.setEnabled(has_id and full_ok and paused and self._last_payload is not None)

    def _payload(self) -> dict:
        year_raw = self.year.text().strip()
        year = int(year_raw) if year_raw.isdigit() else None
        return {
            "pdf": str(self._pdf) if self._pdf else None,
            "doi": self.doi.text().strip() or None,
            "arxiv": self.arxiv.text().strip() or None,
            "title": self.title.text().strip() or None,
            "author": self.author.text().strip() or None,
            "venue": self.venue.text().strip() or None,
            "year": year,
            "preset": self._preset,
            "mode": "sample" if self.sample_radio.isChecked() else "full",
            "n_backward": int(self.n_back.value()),
            "n_forward": int(self.n_fwd.value()),
        }

    def _choose_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose seed PDF", str(Path.home()), "PDF (*.pdf)")
        if path:
            self.load_pdf(path)

    def load_pdf(self, path: str, *, keep_preset: bool = False) -> None:
        pdf = Path(path).expanduser().resolve()
        self._pdf = pdf
        if not keep_preset:
            self._preset = None
        self.drop.set_filename(pdf.name)
        try:
            info = inspect_pdf(pdf)
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"Could not read PDF: {exc}")
            self._refresh_start()
            return
        if info.get("title") and not self.title.text().strip():
            self.title.setText(str(info["title"]))
        if info.get("doi") and not self.doi.text().strip():
            self.doi.setText(str(info["doi"]))
        if info.get("arxiv_id") and not self.arxiv.text().strip():
            self.arxiv.setText(str(info["arxiv_id"]))
        if info.get("author_family") and not self.author.text().strip():
            self.author.setText(str(info["author_family"]))
        if info.get("year") and not self.year.text().strip():
            self.year.setText(str(info["year"]))
        bits = [x for x in (info.get("title"), info.get("doi"), info.get("arxiv_id")) if x]
        self.append_log("PDF loaded: " + ("; ".join(str(b) for b in bits) or pdf.name))
        self._refresh_start()

    def _load_qc4hep(self) -> None:
        q = PRESETS["qc4hep"]
        self._preset = "qc4hep"
        self.title.setText(q.title or "")
        self.author.setText(q.author or "")
        self.venue.setText(q.venue or "")
        self.year.setText(str(q.year or ""))
        self.doi.clear()
        self.arxiv.clear()
        if q.pdf and q.pdf.is_file():
            self.load_pdf(str(q.pdf), keep_preset=True)
        else:
            self._pdf = q.pdf if q.pdf and q.pdf.exists() else None
            self.drop.set_filename(q.pdf.name if q.pdf else "qc4hep (no local PDF)")
        self.append_log("Loaded preset qc4hep.")
        self._refresh_start()

    def _start(self) -> None:
        payload = self._payload()
        self._last_payload = payload
        self.start_requested.emit(payload)

    def _pause(self) -> None:
        self.pause_requested.emit()

    def _resume(self) -> None:
        payload = self._last_payload or self._payload()
        self._last_payload = payload
        self.start_requested.emit(payload)

    def set_busy(self, busy: bool) -> None:
        self.set_run_state("running" if busy else "idle")

    def set_run_state(self, state: str) -> None:
        self._run_state = state
        if state == "running":
            self.start.setText("Running…")
        else:
            self.start.setText("Start analysis")
        self._refresh_start()

    def append_log(self, text: str) -> None:
        self.log.appendPlainText(text.rstrip())
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def apply_summary(self, summary: CorpusSummary | None) -> None:
        if not summary:
            for kpi in (self.kpi_papers, self.kpi_back, self.kpi_fwd, self.kpi_text):
                kpi.set_value("—")
            return
        rel = summary.relation_counts
        st = summary.status_counts
        self.kpi_papers.set_value(str(summary.paper_count), summary.run_mode or "papers")
        self.kpi_back.set_value(str(rel.get("backward_reference", 0)), "references")
        self.kpi_fwd.set_value(str(rel.get("forward_citation", 0)), "citations")
        fetched = st.get("fetched", 0)
        pdfs = getattr(summary, "pdf_count", 0) or 0
        successful = getattr(summary, "success_count", None)
        if successful is None:
            successful = fetched
        self.kpi_text.set_value(str(successful), f"{pdfs} PDFs")
