"""Full-precision reference values from the MATLAB solutions of the assignments.

``tests/refs/refs_A<A>.json`` is written by ``tools/refs/export_refs.m``: every
numeric variable of each MATLAB script (sections ``S1``, ``S2``, ``S3``,
``LF`` and ``LF_q_rated``), with 17 significant digits so the values
round-trip exactly. Variable names are the ones used in the MATLAB scripts.

Complex values are stored as ``{"re": ..., "im": ...}``; matrices as lists of
rows. :func:`ref` returns Python scalars or numpy arrays.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

REFS_DIR = Path(__file__).resolve().parent / "refs"

#: A-numbers with a fixture file.
A_NUMBERS = (80, 0)


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"re", "im"}:
        arr = (np.asarray(value["re"], dtype=float)
               + 1j * np.asarray(value["im"], dtype=float))
    elif isinstance(value, list):
        arr = np.asarray(value, dtype=float)
    else:
        return value
    return arr.item() if arr.ndim == 0 else arr


@lru_cache(maxsize=None)
def refs(a_number: int) -> dict[str, dict[str, Any]]:
    """All sections of one fixture file, decoded."""
    raw = json.loads((REFS_DIR / f"refs_A{a_number}.json").read_text(encoding="utf-8"))
    return {section: (values if section == "meta"
                      else {k: _decode(v) for k, v in values.items()})
            for section, values in raw.items()}


def ref(a_number: int, script: str, name: str) -> Any:
    """One MATLAB variable, e.g. ``ref(80, "S2", "Zth7_pu")``."""
    return refs(a_number)[script][name]
