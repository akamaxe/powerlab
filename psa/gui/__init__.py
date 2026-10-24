"""PySide6 (Qt 6) front end. Imports Qt; the core never does.

Import this package before anything imports PySide6: on Windows it makes Qt
load the ICU that ships with the system. Anaconda puts its ``Library\\bin``
folder on the DLL search path, and the ``icuuc.dll`` there only exports
version-suffixed symbols (``ucnv_open_78``), so ``Qt6Core.dll`` would fail with
"the specified procedure could not be found". Once the system DLL is loaded by
absolute path, the loader reuses it for Qt.
"""

from __future__ import annotations

import os
import sys


def _prefer_system_icu() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    system_icu = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "icuuc.dll")
    if os.path.exists(system_icu):
        try:
            ctypes.WinDLL(system_icu)
        except OSError:
            pass


_prefer_system_icu()
