from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget


class Page(QWidget):
    start_requested = Signal(dict)
    show_corpus = Signal(str)
    project_changed = Signal(str)
    show_page = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project_id: str | None = None

    def set_project(self, project_id: str | None) -> None:
        self._project_id = project_id

    def on_show(self) -> None:
        return
