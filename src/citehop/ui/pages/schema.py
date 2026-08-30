"""Schema authoring form. Writes project schema.json through ClaimsAPI."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from citehop.claims.api import ClaimsAPI, ProjectError, SchemaError
from citehop.ui.pages import Page
from citehop.ui.widgets import card, muted

FIELD_TYPES = ("string", "number", "boolean", "enum")


class SchemaPage(Page):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.api = ClaimsAPI()
        self._schema: dict[str, Any] = {
            "schema_id": "",
            "project_domain_label": "",
            "claim_types": [],
        }
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self.schema_id = QLineEdit()
        self.domain_label = QLineEdit()
        self.domain_label.setPlaceholderText("Display only — never used as logic")
        meta = QFormLayout()
        meta.addRow("Schema id", self.schema_id)
        meta.addRow("Domain label", self.domain_label)
        meta_w = QWidget()
        meta_w.setLayout(meta)

        self.template = QComboBox()
        clone = QPushButton("Clone template into this project")
        clone.clicked.connect(self._clone_template)
        save = QPushButton("Save schema")
        save.setObjectName("accent")
        save.clicked.connect(self._save)
        top_btns = QHBoxLayout()
        top_btns.addWidget(self.template, 1)
        top_btns.addWidget(clone)
        top_btns.addWidget(save)
        top_wrap = QWidget()
        top_l = QVBoxLayout(top_wrap)
        top_l.setContentsMargins(0, 0, 0, 0)
        top_l.addWidget(meta_w)
        top_l.addLayout(top_btns)
        root.addWidget(
            card(
                top_wrap,
                muted("Claim types and fields are project data. The extractor iterates this JSON; it has no built-in taxonomy."),
                title="Claim schema",
            )
        )

        self.banner = muted("Select a project on the Projects tab to author a schema.")
        root.addWidget(self.banner)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.types = QListWidget()
        self.types.currentRowChanged.connect(self._on_type_row)
        add_t = QPushButton("Add claim type")
        add_t.clicked.connect(self._add_type)
        rm_t = QPushButton("Remove type")
        rm_t.clicked.connect(self._remove_type)
        left.addWidget(QLabel("Claim types"))
        left.addWidget(self.types, 1)
        left.addWidget(add_t)
        left.addWidget(rm_t)
        left_w = QWidget()
        left_w.setLayout(left)

        self.type_id = QLineEdit()
        self.display_name = QLineEdit()
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("Shown to the model for this type")
        self.description.setMaximumHeight(120)
        type_form = QFormLayout()
        type_form.addRow("type_id", self.type_id)
        type_form.addRow("Display name", self.display_name)
        type_form.addRow("Description", self.description)

        self.fields = QTableWidget(0, 3)
        self.fields.setHorizontalHeaderLabels(("key", "type", "enum_values"))
        self.fields.verticalHeader().setVisible(False)
        self.fields.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.fields.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.fields.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        add_f = QPushButton("Add field")
        add_f.clicked.connect(self._add_field)
        rm_f = QPushButton("Remove field")
        rm_f.clicked.connect(self._remove_field)
        frow = QHBoxLayout()
        frow.addWidget(add_f)
        frow.addWidget(rm_f)
        frow.addStretch()
        right = QVBoxLayout()
        right.addLayout(type_form)
        right.addWidget(QLabel("Structured fields"))
        right.addWidget(self.fields, 1)
        right.addLayout(frow)
        right_w = QWidget()
        right_w.setLayout(right)
        body.addWidget(left_w, 1)
        body.addWidget(right_w, 2)
        body_w = QWidget()
        body_w.setLayout(body)
        root.addWidget(card(body_w, expand=True), 1)

        for widget in (self.schema_id, self.domain_label, self.type_id, self.display_name):
            widget.textEdited.connect(self._store_current_type)
        self.description.textChanged.connect(self._store_current_type)
        self.fields.itemChanged.connect(self._store_current_type)

    def on_show(self) -> None:
        self._fill_templates()
        self._load()

    def set_project(self, project_id: str | None) -> None:
        super().set_project(project_id)
        if self.isVisible():
            self._load()

    def _fill_templates(self) -> None:
        self.template.blockSignals(True)
        self.template.clear()
        for tmpl in self.api.templates():
            label = tmpl.get("project_domain_label") or tmpl["template_id"]
            self.template.addItem(f"{tmpl['template_id']} — {label}", tmpl["template_id"])
        self.template.blockSignals(False)

    def _load(self) -> None:
        if not self._project_id:
            self._schema = {"schema_id": "", "project_domain_label": "", "claim_types": []}
            self.banner.setText("Select a project on the Projects tab to author a schema.")
            self.banner.show()
            self._render()
            return
        self.banner.hide()
        try:
            self._schema = self.api.get_schema(self._project_id)
        except (ProjectError, SchemaError, OSError) as exc:
            QMessageBox.warning(self, "Schema", str(exc))
            self.banner.setText(str(exc))
            self.banner.show()
            return
        if not (self._schema.get("claim_types") or []):
            self.banner.setText(
                "No claim types yet. Add one, or clone a template. "
                "A type with zero structured fields is allowed."
            )
            self.banner.show()
        else:
            self.banner.hide()
        self._render()

    def _render(self) -> None:
        self._loading = True
        self.schema_id.setText(self._schema.get("schema_id") or "")
        self.domain_label.setText(self._schema.get("project_domain_label") or "")
        self.types.clear()
        for item in self._schema.get("claim_types") or []:
            QListWidgetItem(f"{item.get('type_id')} — {item.get('display_name')}", self.types)
        self._loading = False
        if self.types.count():
            self.types.setCurrentRow(0)
        else:
            self._fill_type_editor(None)

    def _current_type(self) -> dict[str, Any] | None:
        row = self.types.currentRow()
        types = self._schema.get("claim_types") or []
        if 0 <= row < len(types):
            return types[row]
        return None

    def _on_type_row(self, row: int) -> None:
        types = self._schema.get("claim_types") or []
        self._fill_type_editor(types[row] if 0 <= row < len(types) else None)

    def _fill_type_editor(self, item: dict[str, Any] | None) -> None:
        self._loading = True
        self.fields.blockSignals(True)
        if not item:
            self.type_id.clear()
            self.display_name.clear()
            self.description.clear()
            self.fields.setRowCount(0)
            self.fields.blockSignals(False)
            self._loading = False
            return
        self.type_id.setText(item.get("type_id") or "")
        self.display_name.setText(item.get("display_name") or "")
        self.description.setPlainText(item.get("description") or "")
        self.fields.setRowCount(0)
        for field in item.get("structured_fields") or []:
            self._append_field_row(field.get("key") or "", field.get("type") or "string", field.get("enum_values"))
        self.fields.blockSignals(False)
        self._loading = False

    def _append_field_row(self, key: str, ftype: str, enum_values: list | None) -> None:
        r = self.fields.rowCount()
        self.fields.insertRow(r)
        self.fields.setItem(r, 0, QTableWidgetItem(key))
        combo = QComboBox()
        combo.addItems(list(FIELD_TYPES))
        idx = combo.findText(ftype if ftype in FIELD_TYPES else "string")
        combo.setCurrentIndex(max(0, idx))
        combo.currentTextChanged.connect(lambda _t: self._store_current_type())
        self.fields.setCellWidget(r, 1, combo)
        enums = ", ".join(enum_values) if enum_values else ""
        self.fields.setItem(r, 2, QTableWidgetItem(enums))

    def _store_current_type(self) -> None:
        if self._loading:
            return
        item = self._current_type()
        if item is None:
            return
        item["type_id"] = self.type_id.text().strip()
        item["display_name"] = self.display_name.text().strip()
        item["description"] = self.description.toPlainText().strip()
        fields = []
        for r in range(self.fields.rowCount()):
            key_item = self.fields.item(r, 0)
            key = key_item.text().strip() if key_item else ""
            combo = self.fields.cellWidget(r, 1)
            ftype = combo.currentText() if isinstance(combo, QComboBox) else "string"
            enum_item = self.fields.item(r, 2)
            raw_enum = enum_item.text().strip() if enum_item else ""
            field: dict[str, Any] = {"key": key, "type": ftype}
            if ftype == "enum":
                field["enum_values"] = [p.strip() for p in raw_enum.split(",") if p.strip()]
            fields.append(field)
        item["structured_fields"] = fields
        row = self.types.currentRow()
        if 0 <= row < self.types.count():
            self.types.item(row).setText(f"{item.get('type_id')} — {item.get('display_name')}")

    def _add_type(self) -> None:
        self._store_current_type()
        n = len(self._schema.get("claim_types") or []) + 1
        self._schema.setdefault("claim_types", []).append(
            {
                "type_id": f"claim_type_{n}",
                "display_name": f"Claim type {n}",
                "description": "Describe the claims this type should capture.",
                "structured_fields": [],
            }
        )
        self._render()
        self.types.setCurrentRow(self.types.count() - 1)

    def _remove_type(self) -> None:
        row = self.types.currentRow()
        types = self._schema.get("claim_types") or []
        if 0 <= row < len(types):
            types.pop(row)
            self._render()

    def _add_field(self) -> None:
        if self._current_type() is None:
            return
        self.fields.blockSignals(True)
        self._append_field_row("field_key", "string", None)
        self.fields.blockSignals(False)
        self._store_current_type()

    def _remove_field(self) -> None:
        row = self.fields.currentRow()
        if row >= 0:
            self.fields.removeRow(row)
            self._store_current_type()

    def _payload(self) -> dict[str, Any]:
        self._store_current_type()
        return {
            "schema_id": self.schema_id.text().strip(),
            "project_domain_label": self.domain_label.text(),
            "claim_types": self._schema.get("claim_types") or [],
        }

    def _save(self) -> None:
        if not self._project_id:
            QMessageBox.information(self, "Schema", "Select a project first.")
            return
        try:
            saved = self.api.update_schema(self._project_id, self._payload())
        except (SchemaError, ProjectError) as exc:
            QMessageBox.warning(self, "Schema invalid", str(exc))
            return
        self._schema = saved
        self._render()
        QMessageBox.information(self, "Schema", "Saved.")

    def _clone_template(self) -> None:
        if not self._project_id:
            QMessageBox.information(self, "Schema", "Select a project first.")
            return
        template_id = self.template.currentData()
        if not template_id:
            return
        go = QMessageBox.question(
            self,
            "Clone template",
            f"Replace this project's schema with template {template_id!r}?",
        )
        if go != QMessageBox.StandardButton.Yes:
            return
        try:
            saved = self.api.apply_template(self._project_id, template_id)
        except (SchemaError, ProjectError) as exc:
            QMessageBox.warning(self, "Schema", str(exc))
            return
        self._schema = saved
        self._render()
