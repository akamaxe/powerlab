"""Visual language of the schematic: a light CAD look, as in ETAP or PowerFactory.

White sheet, faint grid, heavy black symbols, blue selection, coloured result
annotations. Everything the items draw comes from here so the diagram stays
consistent and can be restyled in one place.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPen

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #

SHEET = QColor("#ffffff")
GRID_MINOR = QColor("#eceff1")
GRID_MAJOR = QColor("#d8dee3")
INK = QColor("#141719")
INK_LIGHT = QColor("#5b666e")
SELECTION = QColor("#0a66c2")
HOVER = QColor("#3b8ede")
VALID_TARGET = QColor("#1f9d55")
INVALID = QColor("#c0392b")
TERMINAL = QColor("#7a848c")
TERMINAL_ACTIVE = QColor("#0a66c2")
WIRE = QColor("#141719")

RESULT_TEXT = QColor("#0b5f3a")
RESULT_BG = QColor(255, 255, 255, 225)
RESULT_FLOW = QColor("#8a4b08")
RESULT_WARN = QColor("#b42318")
OUT_OF_SERVICE = QColor("#9aa4ac")

# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #

GRID = 10                 # scene units
BUS_LENGTH = 60           # default bar length
BUS_WIDTH = 6.0           # bar thickness (semantic, so it scales with zoom)
BRANCH_SPAN = 60.0        # terminal-to-terminal distance of a branch symbol
SHUNT_DROP = 30.0         # terminal-to-body distance of a shunt symbol
TERMINAL_R = 3.0
TERMINAL_R_HOVER = 5.5
SYMBOL_PEN = 2.0
LEAD_PEN = 1.6

# --------------------------------------------------------------------------- #
# Z order. Fixed constants, never computed.
# --------------------------------------------------------------------------- #

Z_WIRE = 0
Z_BUS = 1
Z_SYMBOL = 2
Z_TERMINAL = 3
Z_LABEL = 4
Z_HANDLE = 5
Z_OVERLAY = 10
Z_PREVIEW = 20


# --------------------------------------------------------------------------- #
# Pens and fonts
# --------------------------------------------------------------------------- #


def pen(color: QColor, width: float = SYMBOL_PEN, cosmetic: bool = False,
        style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> QPen:
    p = QPen(color, width, style)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setCosmetic(cosmetic)
    return p


def symbol_pen(selected: bool = False, hovered: bool = False,
               in_service: bool = True) -> QPen:
    if selected:
        return pen(SELECTION, SYMBOL_PEN + 0.8)
    if hovered:
        return pen(HOVER, SYMBOL_PEN + 0.4)
    if not in_service:
        return pen(OUT_OF_SERVICE, SYMBOL_PEN, style=Qt.PenStyle.DashLine)
    return pen(INK, SYMBOL_PEN)


def bus_pen(selected: bool = False, hovered: bool = False,
            in_service: bool = True, highlight: bool = False) -> QPen:
    if highlight:
        return pen(VALID_TARGET, BUS_WIDTH + 3.0)
    if selected:
        return pen(SELECTION, BUS_WIDTH)
    if hovered:
        return pen(HOVER, BUS_WIDTH)
    if not in_service:
        return pen(OUT_OF_SERVICE, BUS_WIDTH, style=Qt.PenStyle.DashLine)
    return pen(INK, BUS_WIDTH)


def wire_pen(selected: bool = False, in_service: bool = True) -> QPen:
    if selected:
        return pen(SELECTION, 2.0, cosmetic=True)
    if not in_service:
        return pen(OUT_OF_SERVICE, 1.6, cosmetic=True, style=Qt.PenStyle.DashLine)
    return pen(WIRE, 1.6, cosmetic=True)


def label_font(bold: bool = True, size: int = 9) -> QFont:
    f = QFont("Segoe UI", size)
    f.setBold(bold)
    return f


def detail_font(size: int = 7) -> QFont:
    """Smaller, lighter face for the parameter line under a symbol."""
    return QFont("Segoe UI", size)


def result_font(size: int = 8) -> QFont:
    f = QFont("Consolas", size)
    return f


# --------------------------------------------------------------------------- #
# Application stylesheet
# --------------------------------------------------------------------------- #

STYLESHEET = """
QMainWindow, QDockWidget, QWidget { background: #f4f6f7; color: #141719; }
QDockWidget::title {
    background: #e4e8eb; padding: 5px 8px; font-weight: 600;
    border-bottom: 1px solid #cfd6db;
}
QGroupBox {
    border: 1px solid #cfd6db; border-radius: 3px; margin-top: 14px;
    padding-top: 6px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QToolBar { background: #e9edf0; border-bottom: 1px solid #cfd6db; spacing: 3px; }
QToolButton { padding: 4px 7px; border-radius: 3px; }
QToolButton:hover { background: #d6dee4; }
QToolButton:checked { background: #c3d4e4; }
QTableWidget, QListWidget {
    background: #ffffff; gridline-color: #e4e8eb;
    alternate-background-color: #f4f7f9;
    selection-background-color: #cfe2f5; selection-color: #141719;
}
QHeaderView::section {
    background: #e9edf0; padding: 4px 6px; border: 0;
    border-right: 1px solid #dde3e7; border-bottom: 1px solid #cfd6db;
    font-weight: 600;
}
QTabWidget::pane { border: 1px solid #cfd6db; background: #ffffff; }
QTabBar::tab {
    background: #e4e8eb; padding: 5px 12px; border: 1px solid #cfd6db;
    border-bottom: 0; margin-right: 2px;
}
QTabBar::tab:selected { background: #ffffff; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: #ffffff; border: 1px solid #c3ccd2; border-radius: 3px;
    padding: 3px 5px;
}
QLineEdit:focus, QComboBox:focus { border-color: #0a66c2; }
QLineEdit[bound="true"] { background: #fff8e6; }
QLineEdit[invalid="true"] { border-color: #c0392b; background: #fdeceb; }
QPushButton {
    background: #e4e8eb; border: 1px solid #c3ccd2; border-radius: 3px;
    padding: 4px 12px;
}
QPushButton:hover { background: #d6dee4; }
QPushButton:default { border-color: #0a66c2; font-weight: 600; }
QLabel#banner {
    background: #fff3cd; border: 1px solid #e0c986; border-radius: 3px;
    padding: 5px 9px; color: #6b5310;
}
QLabel#headline { font-size: 12px; font-weight: 600; padding: 4px 2px; }
QListWidget { background: #ffffff; border: 1px solid #cfd6db; }
QListWidget::item { padding: 5px 6px; }
QListWidget::item:selected { background: #cfe2f5; color: #141719; }
QStatusBar { background: #e9edf0; border-top: 1px solid #cfd6db; }
QScrollBar:vertical { background: #eef1f3; width: 12px; margin: 0; border: 0; }
QScrollBar:horizontal { background: #eef1f3; height: 12px; margin: 0; border: 0; }
QScrollBar::handle:vertical {
    background: #c3ccd2; min-height: 24px; border-radius: 4px; margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #c3ccd2; min-width: 24px; border-radius: 4px; margin: 2px;
}
QScrollBar::handle:hover { background: #a9b4bb; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: 0; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
"""
