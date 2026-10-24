"""Presentation helpers shared by the studies: matrices the way MATLAB prints them.

MATLAB's ``format short`` gives a whole matrix one scale: when its largest
entry leaves the fixed-point window it prints a common factor first
(``1.0e+02 *``) and the entries divided by it, which is how the S2a admittance
matrix appears in the reports. :func:`fmt_matrix_scaled` reproduces that.
"""

from __future__ import annotations

import math

import numpy as np

from .results import LabeledMatrix, Table
from .units import fmt_matrix, fmt_rect

#: Fixed-point window of ``format short``: complex entries leave it at 100,
#: real ones at 1000 (see :mod:`psa.core.units`).
_LARGE_COMPLEX = 1e2
_LARGE_REAL = 1e3
_SMALL = 1e-3


def matrix_scale(m) -> int:
    """The power of ten MATLAB factors out of ``m`` (0 when it prints as is)."""
    arr = np.asarray(m)
    parts = np.concatenate([np.abs(arr.real).ravel(), np.abs(arr.imag).ravel()])
    nonzero = parts[parts > 0]
    if nonzero.size == 0:
        return 0
    hi = float(nonzero.max())
    limit = _LARGE_COMPLEX if np.iscomplexobj(arr) else _LARGE_REAL
    if _SMALL <= hi < limit:
        return 0
    return int(math.floor(math.log10(hi)))


def fmt_matrix_scaled(m) -> tuple[str, list[str]]:
    """``(factor_line, rows)``: the common factor (or ``""``) and the rows."""
    arr = np.asarray(m, dtype=complex)
    k = matrix_scale(arr)
    if k == 0:
        return "", fmt_matrix(arr, style="fix")
    return f"1.0e{k:+03d} *", fmt_matrix(arr / 10.0 ** k, style="fix")


def matrix_table(lm: LabeledMatrix) -> Table:
    """A labelled matrix as a table, with MATLAB's common factor in the title."""
    arr = np.asarray(lm.data, dtype=complex)
    k = matrix_scale(arr)
    factor = f"1.0e{k:+03d} *" if k else ""
    scaled = arr / 10.0 ** k if k else arr
    t = Table(lm.title + (f"   ({factor})" if factor else ""), [""] + list(lm.cols))
    for label, row in zip(lm.rows, scaled):
        t.add(label, *(fmt_rect(c, "fix") for c in row))
    if factor:
        t.note = f"every entry is multiplied by 1.0e{k:+03d}"
    return t
