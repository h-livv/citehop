"""Review extracted claims: filters, provenance highlight, confirm/reject/edit."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from citehop.claims.api import ClaimsAPI, ProjectError, SchemaError
from citehop.claims.locate import clamp_span
from citehop.ui.pages import Page
from citehop.ui.widgets import card, muted
from citehop.ui.theme import ACCENT


class ReviewPage(Page):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.api = ClaimsAPI()
        self._claims: list[dict[str, Any]] = []
        self._schema: dict[str, Any] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self.project_lbl = muted("No project selected.")
        filters = QHBoxLayout()
        self.f_type = QComboBox()
        self.f_agree = QComboBox()
        self.f_verify = QComboBox()
        self.f_agree.addItem("All agreement", None)
        for val in ("disagreement", "single_pass_only", "partial_match", "match"):
            self.f_agree.addItem(val, val)
        self.f_verify.addItem("All verification", None)
        for val in ("unverified_by_human", "human_confirmed", "human_rejected", "human_edited"):
            self.f_verify.addItem(val, val)
        for box in (self.f_type, self.f_agree, self.f_verify):
            box.currentIndexChanged.connect(self._reload_claims)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._reload_claims)
        filters.addWidget(self.f_type, 1)
        filters.addWidget(self.f_agree)
        filters.addWidget(self.f_verify)
        filters.addWidget(refresh)
        filt_w = QWidget()
        filt_w.setLayout(filters)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ("Agreement", "Type", "Claim", "Verification", "Paper", "Id")
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnHidden(5, True)
        self.table.itemSelectionChanged.connect(self._on_row)

        self.empty_lbl = muted("Select a project on the Projects tab.")
        self.source = QTextEdit()
        self.source.setReadOnly(True)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)

        confirm = QPushButton("Confirm")
        confirm.setObjectName("accent")
        reject = QPushButton("Reject")
        edit = QPushButton("Edit…")
        confirm.clicked.connect(lambda: self._review("confirm"))
        reject.clicked.connect(lambda: self._review("reject"))
        edit.clicked.connect(self._edit)
        actions = QHBoxLayout()
        actions.addWidget(confirm)
        actions.addWidget(reject)
        actions.addWidget(edit)
        actions.addStretch()
        act_w = QWidget()
        act_w.setLayout(actions)

        split = QSplitter()
        split.addWidget(self.table)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("Provenance"))
        rl.addWidget(self.source, 2)
        rl.addWidget(QLabel("Claim"))
        rl.addWidget(self.detail, 1)
        rl.addWidget(act_w)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        root.addWidget(self.project_lbl)
        root.addWidget(
            card(
                filt_w,
                muted("Disagreement and single-pass claims are listed first — that is where review pays off."),
                title="Claims",
            )
        )
        root.addWidget(self.empty_lbl)
        root.addWidget(split, 1)

    def on_show(self) -> None:
        self._reload_all()

    def set_project(self, project_id: str | None) -> None:
        super().set_project(project_id)
        if self.isVisible():
            self._reload_all()

    def _reload_all(self) -> None:
        self._fill_types()
        self._reload_claims()
        if not self._project_id:
            self.project_lbl.setText("Select a project on the Projects tab.")
            return
        try:
            proj = self.api.get_project(self._project_id)
            self.project_lbl.setText(proj.get("display_name") or proj["project_id"])
        except ProjectError as exc:
            self.project_lbl.setText(str(exc))

    def _fill_types(self) -> None:
        current = self.f_type.currentData()
        self.f_type.blockSignals(True)
        self.f_type.clear()
        self.f_type.addItem("All claim types", None)
        self._schema = None
        if self._project_id:
            try:
                self._schema = self.api.get_schema(self._project_id)
                for item in self._schema.get("claim_types") or []:
                    self.f_type.addItem(
                        f"{item.get('display_name')} ({item.get('type_id')})",
                        item.get("type_id"),
                    )
            except (SchemaError, ProjectError, OSError):
                pass
        idx = self.f_type.findData(current)
        self.f_type.setCurrentIndex(idx if idx >= 0 else 0)
        self.f_type.blockSignals(False)

    def _reload_claims(self) -> None:
        self.table.setRowCount(0)
        self._claims = []
        if not self._project_id:
            self.empty_lbl.setText("Select a project on the Projects tab.")
            self.empty_lbl.show()
            return
        try:
            self._claims = self.api.list_claims(
                self._project_id,
                claim_type=self.f_type.currentData(),
                agreement=self.f_agree.currentData(),
                verification_status=self.f_verify.currentData(),
            )
        except (ProjectError, OSError) as exc:
            QMessageBox.warning(self, "Review", str(exc))
            self.empty_lbl.setText(str(exc))
            self.empty_lbl.show()
            return
        self.table.setSortingEnabled(False)
        for claim in self._claims:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = (
                claim.get("agreement") or "",
                claim.get("claim_type") or "",
                claim.get("claim_text") or "",
                claim.get("verification_status") or "",
                claim.get("paper_canonical_id") or "",
                claim.get("claim_id") or "",
            )
            for c, val in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(str(val)))
        self.table.setSortingEnabled(False)
        if self.table.rowCount():
            self.empty_lbl.hide()
            self.table.selectRow(0)
        else:
            agree = self.f_agree.currentData()
            if agree:
                self.empty_lbl.setText(
                    f"No claims with agreement={agree!r} in this project."
                )
            else:
                self.empty_lbl.setText(
                    "No claims yet. Run extraction on the Extract tab."
                )
            self.empty_lbl.show()

    def _selected_claim(self) -> dict[str, Any] | None:
        items = self.table.selectedItems()
        if not items:
            return None
        cid = self.table.item(items[0].row(), 5)
        if not cid:
            return None
        for claim in self._claims:
            if claim["claim_id"] == cid.text():
                return claim
        return None

    def _on_row(self) -> None:
        claim = self._selected_claim()
        if not claim:
            self.source.clear()
            self.detail.clear()
            return
        lines = [
            f"type: {claim.get('claim_type')}",
            f"agreement: {claim.get('agreement')}",
            f"verification: {claim.get('verification_status')}",
            f"confidence: {claim.get('confidence_self_reported')}",
            f"pass A: {claim.get('present_in_pass_a')}  pass B: {claim.get('present_in_pass_b')}",
            f"offset: {claim.get('source_char_offset')}",
            f"fields: {claim.get('structured_fields')}",
        ]
        if claim.get("disagreement_notes"):
            lines.append(str(claim["disagreement_notes"]))
        if claim.get("human_edit"):
            lines.append(f"human_edit: {claim['human_edit']}")
        try:
            src = self.api.paper_source(claim["project_id"], claim["paper_canonical_id"])
        except (KeyError, ProjectError, OSError) as exc:
            self.source.setPlainText(str(exc))
            self.detail.setPlainText("\n".join(lines) + "\n\n" + (claim.get("claim_text") or ""))
            return
        text = src.get("text") or ""
        start, end = claim.get("source_char_offset") or [0, 0]
        start, end, oob = clamp_span(text, int(start), int(end))
        if oob:
            lines.append(
                "Provenance highlight skipped: stored offsets are outside the current paper text."
            )
        self.detail.setPlainText("\n".join(lines) + "\n\n" + (claim.get("claim_text") or ""))
        self.source.setPlainText(text)
        if text:
            _highlight(self.source, start, end)

    def _review(self, action: str) -> None:
        claim = self._selected_claim()
        if not claim or not self._project_id:
            return
        try:
            self.api.review_claim(self._project_id, claim["claim_id"], action)
        except (ValueError, KeyError, SchemaError, ProjectError) as exc:
            QMessageBox.warning(self, "Review", str(exc))
            return
        self._reload_claims()

    def _edit(self) -> None:
        claim = self._selected_claim()
        if not claim or not self._project_id:
            return
        dlg = EditClaimDialog(claim, self._schema or {"claim_types": []}, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.api.review_claim(self._project_id, claim["claim_id"], "edit", edit=dlg.payload())
        except (ValueError, SchemaError, ProjectError, KeyError) as exc:
            QMessageBox.warning(self, "Review", str(exc))
            return
        self._reload_claims()


class EditClaimDialog(QDialog):
    def __init__(self, claim: dict[str, Any], schema: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit claim")
        self._claim = claim
        self._widgets: dict[str, QWidget] = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.claim_text = QLineEdit(claim.get("claim_text") or "")
        form.addRow("claim_text", self.claim_text)
        type_ids = [ct["type_id"] for ct in schema.get("claim_types") or []]
        self.claim_type = QComboBox()
        self.claim_type.addItems(type_ids or [claim.get("claim_type") or ""])
        idx = self.claim_type.findText(claim.get("claim_type") or "")
        if idx >= 0:
            self.claim_type.setCurrentIndex(idx)
        form.addRow("claim_type", self.claim_type)
        fields_def = []
        for ct in schema.get("claim_types") or []:
            if ct["type_id"] == claim.get("claim_type"):
                fields_def = ct.get("structured_fields") or []
                break
        values = claim.get("structured_fields") or {}
        if not fields_def:
            for key, val in values.items():
                w = QLineEdit("" if val is None else str(val))
                self._widgets[key] = w
                form.addRow(key, w)
        else:
            for field in fields_def:
                key = field["key"]
                self._widgets[key] = _field_widget(field, values.get(key))
                form.addRow(key, self._widgets[key])
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def payload(self) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for key, widget in self._widgets.items():
            fields[key] = _widget_value(widget)
        return {
            "claim_text": self.claim_text.text().strip(),
            "claim_type": self.claim_type.currentText().strip(),
            "structured_fields": fields,
        }


def _field_widget(field: dict[str, Any], value: Any) -> QWidget:
    ftype = field.get("type")
    if ftype == "boolean":
        w = QCheckBox()
        w.setChecked(bool(value))
        return w
    if ftype == "number":
        w = QDoubleSpinBox()
        w.setRange(-1e12, 1e12)
        w.setDecimals(4)
        try:
            w.setValue(float(value) if value is not None else 0.0)
        except (TypeError, ValueError):
            w.setValue(0.0)
        return w
    if ftype == "enum":
        w = QComboBox()
        w.addItems(list(field.get("enum_values") or []))
        if value is not None:
            i = w.findText(str(value))
            if i >= 0:
                w.setCurrentIndex(i)
        return w
    w = QLineEdit("" if value is None else str(value))
    return w


def _widget_value(widget: QWidget) -> Any:
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QDoubleSpinBox):
        return float(widget.value())
    if isinstance(widget, QComboBox):
        return widget.currentText()
    if isinstance(widget, QLineEdit):
        return widget.text()
    return None


def _highlight(view: QTextEdit, start: int, end: int) -> None:
    text = view.toPlainText()
    start, end, _ = clamp_span(text, start, end)
    cursor = view.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    fmt = QTextCharFormat()
    fmt.setBackground(QColor(ACCENT))
    fmt.setForeground(QColor("#06281d"))
    sel = QTextEdit.ExtraSelection()
    sel.cursor = cursor
    sel.format = fmt
    view.setExtraSelections([sel])
    view.setTextCursor(cursor)
    view.ensureCursorVisible()
