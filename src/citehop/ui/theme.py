"""Offprint theme on dark stock. Oxblood is a quiet undertone, not a highlight color."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

BG = "#151311"
BG_ELEVATED = "#1C1916"
BG_CARD = "#221E1A"
BG_CARD_HOVER = "#2A2622"
BG_INPUT = "#181512"
BORDER = "#3C3630"
BORDER_STRONG = "#524C44"
TEXT = "#EDE6D9"
TEXT_DIM = "#A89F91"
TEXT_MUTED = "#7A7368"
ACCENT = "#C94B56"
ACCENT_DIM = "#6E3338"
HIGHLIGHT = "#5C4A1E"
HIGHLIGHT_TEXT = "#F3E2A0"
WARN = "#C4A35A"
DANGER = "#D46A6A"
INFO = "#8A9BB3"
INK_ON_ACCENT = "#EDE6D9"

_SANS = '"Adwaita Sans", "Cantarell", "Noto Sans", sans-serif'
_SERIF = '"STIX Two Text", "Noto Serif", "Liberation Serif", serif'
_MONO = '"Adwaita Mono", "Noto Sans Mono", "Liberation Mono", monospace'

QSS = f"""
QMainWindow, QWidget#root {{
    background: {BG};
    color: {TEXT};
    font-family: {_SANS};
    font-size: 13px;
}}
QWidget#sidebar {{
    background: {BG_ELEVATED};
    border-right: 1px solid {BORDER};
    border-left: 2px solid {ACCENT_DIM};
}}
QLabel#brand {{
    color: {TEXT};
    font-family: {_SERIF};
    font-size: 22px;
    font-weight: 600;
    letter-spacing: 0.14em;
}}
QLabel#brandSub {{
    color: {TEXT_MUTED};
    font-size: 11px;
    letter-spacing: 0.04em;
}}
QLabel#section {{
    color: {TEXT};
    font-family: {_SERIF};
    font-size: 16px;
    font-weight: 600;
}}
QLabel#muted, QLabel[muted="true"] {{
    color: {TEXT_DIM};
}}
QLabel#kpiTitle {{
    color: {TEXT_DIM};
    font-size: 11px;
    font-weight: 500;
}}
QLabel#kpiValue {{
    font-family: {_SERIF};
    font-size: 26px;
    font-weight: 600;
    color: {TEXT};
}}
QPushButton {{
    background: {BG_CARD};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 2px;
    padding: 7px 14px;
    font-family: {_SANS};
}}
QPushButton:hover {{
    background: {BG_CARD_HOVER};
    border-color: {TEXT_MUTED};
}}
QPushButton:pressed {{
    background: {BG_ELEVATED};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    background: {BG};
    border-color: {BORDER};
}}
QPushButton#accent {{
    background: {ACCENT};
    color: {INK_ON_ACCENT};
    border: 1px solid {ACCENT};
    font-weight: 600;
}}
QPushButton#accent:hover {{
    background: {ACCENT_DIM};
    border-color: {ACCENT_DIM};
}}
QPushButton#accent:disabled {{
    background: {BORDER_STRONG};
    border-color: {BORDER_STRONG};
    color: {BG_CARD};
}}
QPushButton#nav {{
    text-align: left;
    padding: 8px 12px;
    border: none;
    border-left: 2px solid transparent;
    background: transparent;
    border-radius: 0;
    color: {TEXT_DIM};
    font-size: 13px;
    font-family: {_SANS};
}}
QPushButton#nav:hover {{
    background: {BG_CARD};
    color: {TEXT};
}}
QPushButton#nav:checked {{
    background: transparent;
    color: {TEXT};
    border-left: 2px solid {ACCENT_DIM};
    font-weight: 600;
}}
QFrame#card {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 2px;
}}
QFrame#dropZone {{
    background: {BG_INPUT};
    border: 1px dashed {BORDER_STRONG};
    border-radius: 2px;
}}
QFrame#dropZone[active="true"] {{
    border: 1px dashed {ACCENT_DIM};
    background: {BG_CARD};
}}
QFrame#banner {{
    background: #2A2418;
    border: 1px solid #4A4030;
    border-radius: 2px;
}}
QFrame#banner[level="critical"] {{
    background: #2A1818;
    border: 1px solid {ACCENT_DIM};
}}
QFrame#banner[level="ok"] {{
    background: #1A201C;
    border: 1px solid #3A443C;
}}
QComboBox, QLineEdit, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 2px;
    padding: 6px 10px;
    color: {TEXT};
    min-height: 28px;
    font-family: {_SANS};
}}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {BORDER_STRONG};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QCheckBox, QRadioButton {{
    color: {TEXT};
    spacing: 8px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER_STRONG};
    background: {BG_INPUT};
}}
QCheckBox::indicator {{
    border-radius: 1px;
}}
QRadioButton::indicator {{
    border-radius: 7px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT_DIM};
    border-color: {ACCENT_DIM};
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    border-radius: 2px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_MUTED};
}}
QTableWidget {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 2px;
    gridline-color: {BORDER};
    color: {TEXT};
    selection-background-color: {HIGHLIGHT};
    selection-color: {HIGHLIGHT_TEXT};
    font-family: {_SANS};
}}
QHeaderView::section {{
    background: {BG_ELEVATED};
    color: {TEXT_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px;
    font-weight: 600;
    font-family: {_SANS};
}}
QPlainTextEdit {{
    background: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 2px;
    padding: 8px;
    font-family: {_MONO};
    font-size: 12px;
}}
QTextEdit {{
    background: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 2px;
    padding: 10px 14px;
    font-family: {_SERIF};
    font-size: 14px;
}}
QLabel#navSection {{
    color: {TEXT_MUTED};
    font-family: {_SERIF};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.16em;
    padding: 16px 8px 4px 8px;
}}
QStatusBar {{
    background: {BG_ELEVATED};
    color: {TEXT_DIM};
    border-top: 1px solid {BORDER};
}}
QListWidget {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 2px;
    color: {TEXT};
    outline: none;
    font-family: {_SANS};
}}
QListWidget::item {{
    padding: 8px 10px;
}}
QListWidget::item:selected {{
    background: {HIGHLIGHT};
    color: {HIGHLIGHT_TEXT};
}}
QSplitter::handle {{
    background: {BORDER};
    width: 1px;
}}
QDialog {{
    background: {BG};
    color: {TEXT};
    font-family: {_SANS};
}}
QDialogButtonBox QPushButton {{
    min-width: 80px;
}}
QComboBox QAbstractItemView {{
    background: {BG_CARD};
    color: {TEXT};
    selection-background-color: {HIGHLIGHT};
    selection-color: {HIGHLIGHT_TEXT};
    border: 1px solid {BORDER};
}}
QToolTip {{
    background: {BG_CARD};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    padding: 6px 8px;
}}
QMenu {{
    background: {BG_CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
}}
QMenu::item:selected {{
    background: {HIGHLIGHT};
    color: {HIGHLIGHT_TEXT};
}}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(["Adwaita Sans", "Cantarell", "Noto Sans", "Sans Serif"])
    font.setPointSize(10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Base, QColor(BG_INPUT))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_CARD))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(BG_CARD))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(HIGHLIGHT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(HIGHLIGHT_TEXT))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(BG_CARD))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    pal.setColor(QPalette.ColorRole.Link, QColor(ACCENT_DIM))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
