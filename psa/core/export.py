"""Export study results: text, CSV, LaTeX, JSON, and matrices as MATLAB literals.

Text, CSV and LaTeX carry the formatted tables, exactly as the GUI shows them
(MATLAB ``format short``). JSON carries the numbers behind them at full
precision (complex values as ``{"re": ..., "im": ...}``, as in the reference
fixtures and the ``.psn`` files), so a script can pick them up. A matrix
copied "as MATLAB" round-trips every digit.
"""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .results import LabeledMatrix, Quantity, Result, Table

#: File extension -> format name.
FORMATS = {".txt": "text", ".csv": "csv", ".tex": "latex", ".json": "json"}


# --------------------------------------------------------------------------- #
# Text and CSV
# --------------------------------------------------------------------------- #


def to_text(results: Iterable[Result]) -> str:
    return "\n\n\n".join(r.text() for r in results) + "\n"


def to_csv(results: Iterable[Result]) -> str:
    """Every table as a block: its title, the header row, the rows, a blank line."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for res in results:
        w.writerow([res.title])
        if res.headline:
            w.writerow([res.headline.replace("\n", "   ")])
        for warning in res.warnings:
            w.writerow([f"Warning: {warning}"])
        w.writerow([])
        for t in res.tables:
            w.writerow([t.title])
            if t.headers:
                w.writerow(t.headers)
            w.writerows(t.rows)
            if t.note:
                w.writerow([f"({t.note})"])
            w.writerow([])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# LaTeX
# --------------------------------------------------------------------------- #

_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def latex_escape(text: str) -> str:
    """Escape LaTeX specials; the polar ``<`` becomes ``\\angle`` and ``°`` a degree sign."""
    out = "".join(_LATEX_SPECIAL.get(ch, ch) for ch in str(text))
    out = out.replace(" < ", r" $\angle$ ").replace("\N{DEGREE SIGN}", r"$^\circ$")
    return out


def table_to_latex(t: Table) -> str:
    """One table as a ``booktabs`` tabular, numbers right-aligned."""
    cols = max([len(t.headers)] + [len(r) for r in t.rows] or [1])
    spec = "l" + "r" * (cols - 1)
    lines = [f"% {t.title}", r"\begin{tabular}{" + spec + "}", r"\toprule"]
    if t.headers:
        lines.append(" & ".join(latex_escape(h) for h in t.headers) + r" \\")
        lines.append(r"\midrule")
    for row in t.rows:
        cells = list(row) + [""] * (cols - len(row))
        lines.append(" & ".join(latex_escape(c) for c in cells) + r" \\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    if t.note:
        lines.append(r"\par\smallskip{\footnotesize " + latex_escape(t.note) + "}")
    return "\n".join(lines)


def to_latex(results: Iterable[Result]) -> str:
    """Every table of every result, each under a ``\\paragraph`` with its title.

    Needs ``booktabs`` in the preamble.
    """
    parts = ["% Exported by PowerLab. Needs \\usepackage{booktabs}."]
    for res in results:
        parts.append(r"\subsection*{" + latex_escape(res.title) + "}")
        if res.headline:
            parts.append(latex_escape(res.headline).replace("\n", r"\\") + "\n")
        for t in res.tables:
            parts.append(r"\paragraph{" + latex_escape(t.title) + "}")
            parts.append(table_to_latex(t))
    return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #


def _number(value: Any) -> Any:
    if isinstance(value, (complex, np.complexfloating)):
        return {"re": float(value.real), "im": float(value.imag)}
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _quantity(q: Quantity) -> dict:
    return {"value": _number(q.value), "kind": q.kind, "label": q.label}


def _matrix(m: LabeledMatrix) -> dict:
    arr = np.asarray(m.data)
    out = {"title": m.title, "kind": m.kind, "rows": list(m.rows), "cols": list(m.cols)}
    if np.iscomplexobj(arr):
        out["re"] = arr.real.tolist()
        out["im"] = arr.imag.tolist()
    else:
        out["data"] = arr.astype(float).tolist()
    return out


def result_to_dict(res: Result) -> dict:
    return {
        "key": res.key,
        "case_id": res.case_id,
        "title": res.title,
        "headline": res.headline,
        "warnings": list(res.warnings),
        "scalars": {k: _quantity(q) for k, q in res.scalars.items()},
        "buses": {b: {k: _quantity(q) for k, q in qs.items()} for b, qs in res.buses.items()},
        "elements": {c: {k: _quantity(q) for k, q in qs.items()}
                     for c, qs in res.elements.items()},
        "matrices": {k: _matrix(m) for k, m in res.matrices.items()},
        "info": dict(res.info),
        "tables": [{"title": t.title, "headers": list(t.headers), "rows": t.rows,
                    "note": t.note} for t in res.tables],
    }


def to_json(results: Iterable[Result]) -> str:
    return json.dumps({"format": "psa-results", "version": 1,
                       "results": [result_to_dict(r) for r in results]}, indent=2) + "\n"


# --------------------------------------------------------------------------- #
# MATLAB
# --------------------------------------------------------------------------- #


def _matlab_real(x: float) -> str:
    if x == 0:
        return "0"
    if not math.isfinite(x):
        return "NaN" if math.isnan(x) else ("Inf" if x > 0 else "-Inf")
    return repr(float(x))


def _matlab_number(z: complex | float) -> str:
    z = complex(z)
    if z.imag == 0:
        return _matlab_real(z.real)
    im = _matlab_real(abs(z.imag))
    if z.real == 0:
        return f"{'-' if z.imag < 0 else ''}{im}i"
    return f"{_matlab_real(z.real)} {'-' if z.imag < 0 else '+'} {im}i"


def matlab_literal(data, name: str = "") -> str:
    """A matrix as MATLAB source, every digit kept: ``Y = [1 + 2i, 3; ...];``."""
    arr = np.atleast_2d(np.asarray(data))
    rows = [", ".join(_matlab_number(x) for x in row) for row in arr]
    body = "[" + "; ...\n     ".join(rows) + "]" if len(rows) > 1 else "[" + rows[0] + "]"
    return f"{name} = {body};" if name else body


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #


def format_for(path: str | Path) -> str:
    """The format a file name asks for, from its extension."""
    fmt = FORMATS.get(Path(path).suffix.lower())
    if fmt is None:
        raise ValueError(f"Unknown export format for '{path}': use one of "
                         f"{', '.join(sorted(FORMATS))}.")
    return fmt


def render(results: Iterable[Result], fmt: str) -> str:
    results = list(results)
    return {"text": to_text, "csv": to_csv, "latex": to_latex, "json": to_json}[fmt](results)


def export_results(results: Iterable[Result], path: str | Path, fmt: str | None = None) -> Path:
    """Write ``results`` to ``path`` in ``fmt`` (by default, from its extension)."""
    path = Path(path)
    text = render(results, fmt or format_for(path))
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
