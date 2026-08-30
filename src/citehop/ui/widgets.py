from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from citehop.config import storage_warning


def card(
    *widgets: QWidget,
    title: str | None = None,
    subtitle: str | None = None,
    expand: bool = False,
) -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    if expand:
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    if title:
        label = QLabel(title)
        label.setObjectName("section")
        layout.addWidget(label)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        layout.addWidget(sub)
    for widget in widgets:
        layout.addWidget(widget)
    return frame


def muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


class StorageBanner(QFrame):
    """Vault/corpus warning. Only shown on pages that need LLM storage."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("banner")
        self.setProperty("level", "critical")
        layout = QHBoxLayout(self)
        self._label = QLabel("")
        self._label.setWordWrap(True)
        layout.addWidget(self._label)
        self.refresh()

    def refresh(self) -> None:
        msg = storage_warning()
        self._label.setText(msg)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setVisible(bool(msg.strip()))


class Kpi(QFrame):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        self.value_lbl = QLabel("—")
        self.value_lbl.setObjectName("kpiValue")
        self.meta = QLabel(label)
        self.meta.setObjectName("muted")
        layout.addWidget(self.value_lbl)
        layout.addWidget(self.meta)

    def set_value(self, value: str, meta: str | None = None) -> None:
        self.value_lbl.setText(value)
        if meta is not None:
            self.meta.setText(meta)


class DropZone(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        self.label = QLabel("Drop a PDF here, or choose a file")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setObjectName("muted")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.setMaximumHeight(92)
        self._on_path = None

    def set_handler(self, handler) -> None:  # noqa: ANN001
        self._on_path = handler

    def set_filename(self, name: str) -> None:
        self.label.setText(name)

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("active", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001
        event.accept()
        self.setProperty("active", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event) -> None:  # noqa: ANN001
        self.setProperty("active", False)
        self.style().unpolish(self)
        self.style().polish(self)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".pdf") and self._on_path:
                self._on_path(path)
                break
        event.acceptProposedAction()
