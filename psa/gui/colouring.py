"""Colour the diagram by voltage level or by the voltages of a result.

Each function returns ``(tints, legend)``: a colour per element id (buses,
and for voltage levels also the one-zone elements and the wires hanging from
them) and the legend rows ``(colour, text)`` the canvas shows in a corner.
"""

from __future__ import annotations

import math

from PySide6.QtGui import QColor

from ..core.bases import propagate_bases
from ..core.components import buses_of
from ..core.errors import PsaError

#: Highest voltage first, like the usual transmission-to-distribution palettes.
LEVEL_PALETTE = ("#c0392b", "#1f5fa8", "#1e8449", "#b9770e", "#7d3c98", "#117a65",
                 "#a04000", "#2e4053")

#: Upper bound of each voltage band (pu), its colour and its legend text.
VOLTAGE_BANDS = (
    (0.90, "#1a3f8f", "below 0.90 pu"),
    (0.95, "#3b7dd8", "0.90 - 0.95 pu"),
    (0.98, "#4aa3a3", "0.95 - 0.98 pu"),
    (1.02, "#2e8b57", "0.98 - 1.02 pu"),
    (1.05, "#e08e0b", "1.02 - 1.05 pu"),
    (math.inf, "#c0392b", "above 1.05 pu"),
)


def voltage_level_tints(net) -> tuple[dict[str, QColor], list[tuple[QColor, str]]]:
    """One colour per base voltage: buses, one-zone elements, and their wires."""
    try:
        bases = propagate_bases(net)
    except PsaError:
        return {}, []
    u = {b: round(v, 6) for b, v in bases.u_base_kv.items()}
    levels = sorted(set(u.values()), reverse=True)
    colour = {lvl: QColor(LEVEL_PALETTE[i % len(LEVEL_PALETTE)]) for i, lvl in enumerate(levels)}
    tints = {b: colour[lvl] for b, lvl in u.items()}
    for comp in net.components.values():
        zones = {u.get(b) for b in buses_of(comp)}
        if len(zones) == 1 and None not in zones:
            tints[comp.id] = colour[zones.pop()]
    return tints, [(colour[lvl], f"{lvl:g} kV") for lvl in levels]


def bus_voltage_magnitude(quantities: dict) -> float | None:
    """|U| of a bus in a result: the voltage, or the lowest phase during a fault."""
    if "u" in quantities and quantities["u"].kind == "voltage_pu":
        return abs(quantities["u"].value)
    phases = [abs(quantities[k].value) for k in ("u_a", "u_b", "u_c") if k in quantities]
    return min(phases) if phases else None


def voltage_band(mag: float) -> tuple[QColor, str]:
    for upper, hex_colour, text in VOLTAGE_BANDS:
        if mag < upper:
            return QColor(hex_colour), text
    return QColor(VOLTAGE_BANDS[-1][1]), VOLTAGE_BANDS[-1][2]


def voltage_result_tints(res) -> tuple[dict[str, QColor], list[tuple[QColor, str]]]:
    """Buses coloured by the band their voltage magnitude falls in."""
    tints = {}
    if res is not None:
        for bus, q in res.buses.items():
            mag = bus_voltage_magnitude(q)
            if mag is not None:
                tints[bus] = voltage_band(mag)[0]
    if not tints:
        return {}, []
    return tints, [(QColor(c), text) for _, c, text in VOLTAGE_BANDS]


def legend_html(title: str, rows: list[tuple[QColor, str]]) -> str:
    if not rows:
        return ""
    lines = "".join(
        f'<tr><td style="color:{c.name()}; font-size:15px;">&#9632;</td>'
        f'<td style="padding-left:4px;">{text}</td></tr>' for c, text in rows)
    return f"<b>{title}</b><table cellspacing='0' cellpadding='0'>{lines}</table>"
