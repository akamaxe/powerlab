"""Launch the PowerLab GUI.

    python run_gui.py                     # empty project
    python run_gui.py examples/s1_a80.psn # open a project
    python run_gui.py --example           # the validated Assignment S1 network
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import psa.gui  # noqa: E402,F401 - must come before PySide6, see its docstring
from PySide6.QtWidgets import QMessageBox                         # noqa: E402

from psa.gui.app import create_app, initial_document, install_excepthook  # noqa: E402
from psa.gui.mainwindow import MainWindow                       # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = create_app(argv)

    document, error = initial_document(argv)
    window = MainWindow(document)
    install_excepthook(window)
    window.show()
    if error:
        QMessageBox.critical(window, "Could not open the project", error)
    else:
        window.offer_linked_script()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
