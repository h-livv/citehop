"""Window that opens a paper at a claim's quoted span and highlights it."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
)

from citehop.claims.locate import clamp_span
from citehop.claims.pdf_locate import copy_pdf_with_quote_highlight
from citehop.ui.theme import HIGHLIGHT, HIGHLIGHT_TEXT
from citehop.ui.widgets import muted

try:
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView

    _HAS_QTPDF = True
except ImportError:
    QPdfDocument = None  # type: ignore[misc, assignment]
    QPdfView = None  # type: ignore[misc, assignment]
    _HAS_QTPDF = False


class PaperQuoteWindow(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Paper")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(920, 980)
        self._doc = None
        self._tmp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._jump_page = 0

        self.status = muted("")
        self.stack = QStackedWidget()
        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        self.stack.addWidget(self.text_view)
        self.pdf_view = None
        if _HAS_QTPDF:
            self.pdf_view = QPdfView()
            self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
            self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            self.stack.addWidget(self.pdf_view)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.stack, 1)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._cleanup_tmp()
        super().closeEvent(event)

    def show_quote(self, source: dict[str, Any], claim: dict[str, Any]) -> None:
        title = source.get("title") or claim.get("paper_canonical_id") or "Paper"
        self.setWindowTitle(str(title))
        text = source.get("text") or ""
        quote = (claim.get("quoted_source_span") or "").strip()
        start, end = claim.get("source_char_offset") or [0, 0]
        start, end, _ = clamp_span(text, int(start or 0), int(end or 0))
        if not quote and text:
            quote = text[start:end]
        pdf = source.get("pdf_path")
        if pdf and Path(pdf).is_file() and self._open_pdf(Path(pdf), quote, start):
            return
        self._show_text(text, start, end, pdf_missing=not (pdf and Path(str(pdf)).is_file()))

    def _open_pdf(self, pdf: Path, quote: str, char_start: int) -> bool:
        if not _HAS_QTPDF or self.pdf_view is None:
            return False
        self._cleanup_tmp()
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="citehop-paper-")
        dest = Path(self._tmp_dir.name) / "quoted.pdf"
        try:
            hit = copy_pdf_with_quote_highlight(pdf, dest, quote, char_start=char_start)
        except (OSError, RuntimeError, ValueError) as exc:
            self._cleanup_tmp()
            self.status.setText(f"Could not annotate the PDF ({exc}). Showing extracted text.")
            return False
        if not dest.is_file() or not self._load_pdf(dest, hit.page_index):
            self._cleanup_tmp()
            return False
        page_no = hit.page_index + 1
        if hit.highlighted:
            self.status.setText(f"Page {page_no} · quoted span highlighted.")
        else:
            self.status.setText(
                f"Page {page_no}. The quoted wording was not found in the PDF text layer, "
                "so nothing is highlighted."
            )
        return True

    def _load_pdf(self, path: Path, page_index: int) -> bool:
        assert self.pdf_view is not None
        doc = QPdfDocument(self)
        err = doc.load(str(path))
        ready = doc.status() == QPdfDocument.Status.Ready
        ok_err = err == QPdfDocument.Error.None_
        if not ready and not ok_err:
            doc.deleteLater()
            return False
        old = self._doc
        self._doc = doc
        self.pdf_view.setDocument(doc)
        if old is not None:
            old.deleteLater()
        self._jump_page = max(0, page_index)
        self.stack.setCurrentWidget(self.pdf_view)
        QTimer.singleShot(0, self._jump)
        QTimer.singleShot(120, self._jump)
        return True

    def _jump(self) -> None:
        if self.pdf_view is None or self._doc is None:
            return
        if self._doc.status() != QPdfDocument.Status.Ready:
            return
        pages = self._doc.pageCount()
        page = min(self._jump_page, max(0, pages - 1))
        self.pdf_view.pageNavigator().jump(page, QPointF(0, 0))

    def _show_text(self, text: str, start: int, end: int, *, pdf_missing: bool) -> None:
        self.stack.setCurrentWidget(self.text_view)
        self.text_view.setPlainText(text)
        if text:
            _highlight(self.text_view, start, end)
        if pdf_missing:
            self.status.setText("No PDF on disk — showing extracted text at the quoted span.")
        else:
            self.status.setText("Showing extracted text at the quoted span.")

    def _cleanup_tmp(self) -> None:
        if self._tmp_dir is not None:
            self._tmp_dir.cleanup()
            self._tmp_dir = None


def _highlight(view: QTextEdit, start: int, end: int) -> None:
    text = view.toPlainText()
    start, end, _ = clamp_span(text, start, end)
    cursor = view.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    fmt = QTextCharFormat()
    fmt.setBackground(QColor(HIGHLIGHT))
    fmt.setForeground(QColor(HIGHLIGHT_TEXT))
    sel = QTextEdit.ExtraSelection()
    sel.cursor = cursor
    sel.format = fmt
    view.setExtraSelections([sel])
    view.setTextCursor(cursor)
    view.ensureCursorVisible()
