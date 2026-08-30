"""Minimal model picker: Ollama tags and FreeToken weights. Extraction uses this."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from citehop.claims.api import ClaimsAPI, LLMError
from citehop.ui.pages import Page
from citehop.ui.widgets import card, muted


def _fmt_bytes(n: int | None) -> str:
    if not n:
        return "—"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return "—"


def _gpu_cell(row: dict[str, Any]) -> str:
    if row.get("backend") != "ollama":
        return "—"
    layers = row.get("num_gpu")
    total = row.get("num_gpu_total")
    if layers is None:
        return "Machina auto"
    if total:
        return f"{layers}/{total}"
    return str(layers)


class LoadWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, row: dict[str, Any]) -> None:
        super().__init__()
        self.row = row

    def run(self) -> None:
        try:
            result = ClaimsAPI().use_extraction_model(self.row)
            self.done.emit(result)
        except (LLMError, RuntimeError, ValueError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class UnloadWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            result = ClaimsAPI().unload_extraction_models()
            self.done.emit(result)
        except (LLMError, RuntimeError, ValueError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ModelsPage(Page):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.api = ClaimsAPI()
        self._rows: list[dict[str, Any]] = []
        self._worker: QThread | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self.status = muted("Refresh, then select a model and use it for extraction.")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("Model", "Source", "GPU layers", "Size", "Loaded"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._use)

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload)
        use = QPushButton("Use for extraction")
        use.setObjectName("accent")
        use.clicked.connect(self._use)
        unload = QPushButton("Unload from VRAM")
        unload.setToolTip(
            "Abort any in-flight generation and stop the FreeToken engine so GPU memory is freed. "
            "Extract Pause only cancels the request; the weights stay loaded until you unload."
        )
        unload.clicked.connect(self._unload)
        btns = QHBoxLayout()
        btns.addWidget(refresh)
        btns.addWidget(use)
        btns.addWidget(unload)
        btns.addStretch()
        btn_w = QWidget()
        btn_w.setLayout(btns)

        root.addWidget(self.status)
        root.addWidget(
            card(
                self.table,
                btn_w,
                title="Models",
                subtitle="Ollama tags on this machine, plus FreeToken weights on Vault. "
                "Ollama loads with Machina's saved max GPU layers. No sampling knobs. "
                "Unload from VRAM stops FreeToken's engine (and any Ollama tag) so the GPU is free. "
                "Extract Pause only aborts the current request; it does not unload weights.",
                expand=True,
            ),
            1,
        )

    def on_show(self) -> None:
        self.reload()

    def reload(self) -> None:
        self._rows = self.api.extraction_models()
        current = (self.api.extraction_model().get("settings") or {})
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for row in self._rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = (
                row.get("model") or "",
                row.get("backend") or "",
                _gpu_cell(row),
                _fmt_bytes(row.get("size_b")),
                "yes" if row.get("loaded") else "",
            )
            for c, val in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(str(val)))
            if (
                current.get("backend") == row.get("backend")
                and current.get("model") == row.get("model")
            ):
                self.table.selectRow(r)
        info = self.api.extraction_model()["label"]
        extra = []
        if not any(r.get("backend") == "ollama" for r in self._rows):
            extra.append("Ollama not listing models")
        if not any(r.get("backend") == "freetoken" for r in self._rows):
            extra.append("no FreeToken weights (Vault/freetoken)")
        suffix = f"  ·  {'; '.join(extra)}" if extra else ""
        self.status.setText(f"{info}{suffix}")

    def _selected_row(self) -> dict[str, Any] | None:
        items = self.table.selectedItems()
        if not items:
            return None
        idx = items[0].row()
        if 0 <= idx < len(self._rows):
            return self._rows[idx]
        return None

    def _use(self) -> None:
        row = self._selected_row()
        if not row:
            QMessageBox.information(self, "Models", "Select a model first.")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Models", "A load or unload is already running.")
            return
        self.status.setText(f"Loading {row.get('backend')} {row.get('model')}…")
        worker = LoadWorker(row)
        self._worker = worker
        worker.done.connect(self._on_loaded)
        worker.failed.connect(self._on_fail)
        worker.start()

    def _unload(self) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Models", "A load or unload is already running.")
            return
        self.status.setText("Unloading model from VRAM…")
        worker = UnloadWorker()
        self._worker = worker
        worker.done.connect(self._on_unloaded)
        worker.failed.connect(self._on_fail)
        worker.start()

    @Slot(object)
    def _on_unloaded(self, result: object) -> None:
        payload = result if isinstance(result, dict) else {}
        msg = str(payload.get("message") or "Unloaded.")
        self.status.setText(msg)
        self.reload()

    @Slot(object)
    def _on_loaded(self, result: object) -> None:
        payload = result if isinstance(result, dict) else {}
        msg = str(payload.get("message") or "Model ready.")
        self.status.setText(msg)
        self.reload()

    @Slot(str)
    def _on_fail(self, message: str) -> None:
        self.status.setText(message)
        QMessageBox.warning(self, "Models", message)
        self.reload()
