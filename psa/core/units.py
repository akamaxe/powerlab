"""Number formatting following the EG2100 reporting convention.

The course requires:

* impedance and complex power in **rectangular** form,
* voltage and current in **polar** form with the angle in **degrees**,
* values shown the way MATLAB's ``format short`` shows them (4 decimals, or
  scientific notation when the magnitude leaves the fixed-point window).

:func:`fmt_rect` and :func:`fmt_polar` implement exactly that, and
:data:`QUANTITY_STYLE` maps a physical quantity onto the right one.
"""

from __future__ import annotations

import cmath
import math

# --------------------------------------------------------------------------- #
# format-short style selection
# --------------------------------------------------------------------------- #

#: Below this magnitude everything is shown in scientific notation.
_SMALL = 1e-3
#: At or above these magnitudes a value leaves the fixed-point window.
#: MATLAB's ``format short`` uses a tighter limit for complex quantities.
_LARGE_REAL = 1e3
_LARGE_COMPLEX = 1e2


def short_style(*values: float, complex_group: bool = False) -> str:
    """Pick ``'int'``, ``'fix'`` or ``'exp'`` for a group of numbers.

    A group shares one style, the way MATLAB gives a whole matrix one format,
    and the style is decided by the group's **largest** element -- small
    entries are simply printed as ``0.0004``, they do not force scientific
    notation on their neighbours.
    """
    vals = [v for v in values if v is not None and not math.isnan(v)]
    mags = [abs(v) for v in vals]
    nonzero = [m for m in mags if m != 0.0]
    if not nonzero:
        return "int"
    limit = _LARGE_COMPLEX if complex_group else _LARGE_REAL
    hi = max(nonzero)
    if all(float(v).is_integer() for v in vals) and hi < limit:
        return "int"
    if hi >= limit or hi < _SMALL:
        return "exp"
    return "fix"


def fmt_real(x: float, style: str | None = None) -> str:
    """Format one real number in the chosen (or auto-detected) style."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    style = style or short_style(x)
    if x == 0.0:
        return "0" if style == "int" else ("0.0000" if style == "fix" else "0.0000e+00")
    if style == "int":
        return f"{x:.0f}"
    if style == "exp":
        return f"{x:.4e}"
    return f"{x:.4f}"


def fmt_rect(z: complex, style: str | None = None, unit: str = "") -> str:
    """Rectangular form, e.g. ``22.5232 + 11.1298i pu``. For Z and S."""
    z = complex(z)
    style = style or short_style(z.real, z.imag, complex_group=True)
    re = fmt_real(z.real, style)
    sign = "-" if z.imag < 0 else "+"
    im = fmt_real(abs(z.imag), style)
    tail = f" {unit}" if unit else ""
    return f"{re} {sign} {im}i{tail}"


def fmt_polar(z: complex, unit: str = "", mag_style: str | None = None) -> str:
    """Polar form with the angle in degrees, e.g. ``0.9952 < -0.5345 deg``.

    For voltage and current.
    """
    z = complex(z)
    mag, ang = cmath.polar(z)
    tail = f" {unit}" if unit else ""
    return f"{fmt_real(mag, mag_style)}{tail} < {math.degrees(ang):.4f} deg"


def fmt_matrix(m, style: str | None = None) -> list[str]:
    """Format a 2-D complex array as a list of aligned row strings."""
    rows = [list(r) for r in m]
    flat = [c for row in rows for c in row]
    style = style or short_style(
        *[v for c in flat for v in (c.real, c.imag)], complex_group=True
    )
    cells = [[fmt_rect(c, style) for c in row] for row in rows]
    width = max(len(c) for row in cells for c in row)
    return ["  ".join(c.rjust(width) for c in row) for row in cells]


# --------------------------------------------------------------------------- #
# quantity registry
# --------------------------------------------------------------------------- #

#: quantity name -> ("rect" | "polar" | "real", default unit)
QUANTITY_STYLE: dict[str, tuple[str, str]] = {
    "impedance_pu": ("rect", "pu"),
    "impedance_ohm": ("rect", "ohm"),
    "admittance_pu": ("rect", "pu"),
    "power_mva": ("rect", "MVA"),
    "power_pu": ("rect", "pu"),
    "voltage_pu": ("polar", "pu"),
    "voltage_kv": ("polar", "kV"),
    "current_pu": ("polar", "pu"),
    "current_ka": ("polar", "kA"),
    "ratio": ("real", ""),
    "percent": ("real", "%"),
}


def fmt_quantity(value, quantity: str) -> str:
    """Format ``value`` according to the course convention for ``quantity``."""
    style, unit = QUANTITY_STYLE.get(quantity, ("rect", ""))
    if style == "polar":
        return fmt_polar(value, unit)
    if style == "real":
        txt = fmt_real(float(value))
        return f"{txt} {unit}".strip()
    return fmt_rect(value, unit=unit)


def deg(z: complex) -> float:
    """Angle of ``z`` in degrees."""
    return math.degrees(cmath.phase(complex(z)))


def polar_pair(z: complex) -> tuple[float, float]:
    """``(magnitude, angle_in_degrees)`` of ``z``."""
    mag, ang = cmath.polar(complex(z))
    return mag, math.degrees(ang)
