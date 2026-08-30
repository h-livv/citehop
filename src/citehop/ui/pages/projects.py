"""Create and select extraction projects. Talks only to ClaimsAPI."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from citehop.claims.api import ClaimsAPI, ProjectError, SchemaError
from citehop.ui.pages import Page
from citehop.ui.widgets import card, muted


class ProjectsPage(Page):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.api = ClaimsAPI()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Project name")
        self.corpus = QComboBox()
        self.template = QComboBox()
        self.budget = QSpinBox()
        self.budget.setRange(1_000, 99_999_999)
        self.budget.setSingleStep(10_000)
        self.budget.setValue(500_000)
        self.budget.setGroupSeparatorShown(True)
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Corpus", self.corpus)
        form.addRow("Schema template", self.template)
        form.addRow("Token budget", self.budget)
        form_w = QWidget()
        form_w.setLayout(form)
        create = QPushButton("Create project")
        create.setObjectName("accent")
        create.clicked.connect(self._create)
        col = QVBoxLayout()
        col.addWidget(form_w)
        col.addWidget(create)
        wrap = QWidget()
        wrap.setLayout(col)
        root.addWidget(
            card(
                wrap,
                muted("A project binds one corpus to one claim schema. Templates are starter JSON you can edit next."),
                title="New project",
            )
        )

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("Project", "Corpus", "Token budget", "Schema", "Id"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload)
        open_schema = QPushButton("Edit schema")
        open_schema.clicked.connect(lambda: self.show_page.emit("Schema"))
        row = QHBoxLayout()
        row.addWidget(refresh)
        row.addWidget(open_schema)
        row.addStretch()
        btns = QWidget()
        btns.setLayout(row)
        root.addWidget(card(self.table, btns, title="Projects", expand=True), 1)
        self._hint = muted("Select a project to use it on Schema, Extract, and Review.")
        root.addWidget(self._hint)

    def on_show(self) -> None:
        self.reload()

    def reload(self) -> None:
        self._fill_corpora()
        self._fill_templates()
        projects = self.api.list_projects()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for proj in projects:
            r = self.table.rowCount()
            self.table.insertRow(r)
            schema = ""
            try:
                schema = self.api.get_schema(proj["project_id"]).get("schema_id") or ""
            except (SchemaError, OSError):
                schema = "(missing)"
            values = (
                proj.get("display_name") or proj["project_id"],
                proj.get("corpus_dir") or "",
                str(proj.get("token_budget") or ""),
                schema,
                proj["project_id"],
            )
            for c, val in enumerate(values):
                item = QTableWidgetItem(val)
                if c == 4:
                    item.setData(256, proj["project_id"])
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        if not projects:
            self._hint.setText("No projects yet. Create one above.")
        elif self._project_id:
            self._select_id(self._project_id)
            self._hint.setText(f"Active project: {self._project_id}")
        else:
            self._hint.setText("Select a project to use it on Schema, Extract, and Review.")

    def _fill_corpora(self) -> None:
        current = self.corpus.currentData()
        self.corpus.blockSignals(True)
        self.corpus.clear()
        items = [c for c in self.api.corpora() if c.get("paper_count", 0) > 0]
        if not items:
            self.corpus.addItem("No corpora yet — build one on Analyze", None)
        for item in items:
            self.corpus.addItem(f"{item['label']}  ({item['paper_count']} papers)", item["path"])
        idx = self.corpus.findData(current)
        if idx >= 0:
            self.corpus.setCurrentIndex(idx)
        self.corpus.blockSignals(False)

    def _fill_templates(self) -> None:
        current = self.template.currentData()
        self.template.blockSignals(True)
        self.template.clear()
        self.template.addItem("Empty schema (define types yourself)", None)
        for tmpl in self.api.templates():
            label = tmpl.get("project_domain_label") or tmpl["template_id"]
            self.template.addItem(
                f"{tmpl['template_id']} — {label} ({tmpl['claim_type_count']} types)",
                tmpl["template_id"],
            )
        idx = self.template.findData(current)
        if idx >= 0:
            self.template.setCurrentIndex(idx)
        self.template.blockSignals(False)

    def _create(self) -> None:
        name = self.name.text().strip()
        corpus = self.corpus.currentData()
        if not name:
            QMessageBox.warning(self, "Project", "Give the project a name.")
            return
        if not corpus:
            QMessageBox.warning(self, "Project", "Select a corpus with papers.")
            return
        try:
            proj = self.api.create_project(
                name,
                corpus,
                template_id=self.template.currentData(),
                token_budget=int(self.budget.value()),
            )
        except (ProjectError, SchemaError, OSError) as exc:
            QMessageBox.warning(self, "Project", str(exc))
            return
        self.name.clear()
        self.reload()
        self._select_id(proj["project_id"])
        self.project_changed.emit(proj["project_id"])
        self.show_page.emit("Schema")

    def _on_row(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        row = items[0].row()
        cell = self.table.item(row, 4)
        if not cell:
            return
        pid = cell.text()
        self.set_project(pid)
        self.project_changed.emit(pid)
        self._hint.setText(f"Active project: {pid}")

    def _select_id(self, project_id: str) -> None:
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 4)
            if cell and cell.text() == project_id:
                self.table.selectRow(r)
                return
