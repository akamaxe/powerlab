"""Application bootstrap."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMessageBox

from . import style as st
from .document import NetworkDocument, blank_document

#: The validated Assignment S1 network shipped with the app.
EXAMPLE_PROJECT = Path(__file__).resolve().parents[2] / "examples" / "s1_a80.psn"


def create_app(argv: list[str] | None = None) -> QApplication:
    """Build the QApplication with High-DPI enabled.

    These attributes must be set *before* the QApplication exists, or the
    diagram renders blurry on a scaled Windows display.
    """
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("PowerLab")
    app.setOrganizationName("PowerLab")
    app.setStyleSheet(st.STYLESHEET)
    return app


def initial_document(argv: list[str]) -> tuple[NetworkDocument, str | None]:
    """The document to start with, from the command line.

    ``argv[1:]`` may name a ``.psn`` file (which wins) or contain ``--example``;
    the example opens as an untitled copy so saving cannot overwrite it. A file
    that cannot be opened gives a blank document plus the reason, so the window
    still comes up and can tell the user what went wrong.
    """
    paths = [a for a in argv[1:] if not a.startswith("-")]
    try:
        if paths:
            return NetworkDocument.open(paths[0]), None
        if "--example" in argv:
            return NetworkDocument.open(EXAMPLE_PROJECT, as_copy=True), None
    except Exception as exc:  # noqa: BLE001 - reported in a dialog by the caller
        target = paths[0] if paths else str(EXAMPLE_PROJECT)
        return blank_document(), f"{target}\n\n{type(exc).__name__}: {exc}"
    return blank_document(), None


def install_excepthook(parent=None) -> None:
    """Show unexpected errors in a dialog instead of dying silently."""

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(text)
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("Unexpected error")
        box.setText(str(exc) or exc_type.__name__)
        box.setDetailedText(text)
        box.exec_()

    sys.excepthook = hook
