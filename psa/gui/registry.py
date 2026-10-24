"""What the GUI knows about each component type, in one entry per type.

The palette (label, tooltip, icon), the symbol class drawn on the canvas, the
id prefix of new objects and the values a freshly placed object starts with
all come from :data:`ELEMENT_UI`, in palette order. Adding a component type to
the GUI means adding one :class:`ElementUI` here (plus its symbol class in
:mod:`psa.gui.items`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPolygonF

from . import style as st
from .items import (
    BusItem,
    ComponentItem,
    ImpedanceItem,
    LineItem,
    LoadItem,
    ShuntCapItem,
    SourceItem,
    TransformerItem,
)


@dataclass(frozen=True)
class ElementUI:
    type: str
    label: str
    tooltip: str
    id_prefix: str
    item_class: type
    #: Gives a freshly placed object values that already make sense.
    defaults: Callable[[Any], None] = lambda obj: None
    #: Draws the palette icon around the origin, in a frame about 64 units wide.
    draw_icon: Callable[[QPainter], None] = lambda painter: None


# --------------------------------------------------------------------------- #
# Icons (the symbols, simplified for 34 px)
# --------------------------------------------------------------------------- #


def _icon_bus(p: QPainter) -> None:
    p.setPen(st.pen(st.INK, 7.0))
    p.drawLine(QPointF(0, -32), QPointF(0, 32))


def _icon_transformer(p: QPainter) -> None:
    p.drawEllipse(QPointF(-7, 0), 11, 11)
    p.drawEllipse(QPointF(7, 0), 11, 11)
    p.drawLine(QPointF(-30, 0), QPointF(-18, 0))
    p.drawLine(QPointF(18, 0), QPointF(30, 0))


def _icon_line(p: QPainter) -> None:
    p.drawLine(QPointF(-30, -4), QPointF(30, -4))
    p.drawLine(QPointF(-30, 4), QPointF(30, 4))
    for sx in (-1, 1):
        x = sx * 16
        p.drawLine(QPointF(x, 6), QPointF(x, 12))
        p.drawLine(QPointF(x - 7, 12), QPointF(x + 7, 12))
        p.drawLine(QPointF(x - 7, 17), QPointF(x + 7, 17))


def _icon_load(p: QPainter) -> None:
    p.drawLine(QPointF(0, -30), QPointF(0, -6))
    p.setBrush(st.INK)
    p.drawPolygon(QPolygonF([QPointF(-13, -6), QPointF(13, -6), QPointF(0, 20)]))


def _icon_shunt(p: QPainter) -> None:
    p.drawLine(QPointF(0, -30), QPointF(0, -8))
    p.drawLine(QPointF(-14, -8), QPointF(14, -8))
    p.drawLine(QPointF(-14, 0), QPointF(14, 0))
    p.drawLine(QPointF(0, 0), QPointF(0, 8))
    for i, w in enumerate((14, 9, 4)):
        p.drawLine(QPointF(-w, 8 + i * 6), QPointF(w, 8 + i * 6))


def _icon_source(p: QPainter) -> None:
    p.drawEllipse(QPointF(0, 0), 18, 18)
    p.drawLine(QPointF(18, 0), QPointF(32, 0))
    path = QPainterPath(QPointF(-10, 0))
    path.cubicTo(QPointF(-5, -12), QPointF(0, 12), QPointF(0, 0))
    path.cubicTo(QPointF(0, -12), QPointF(5, 12), QPointF(10, 0))
    p.drawPath(path)


def _icon_impedance(p: QPainter) -> None:
    p.setBrush(Qt.GlobalColor.white)
    p.drawRect(-16, -9, 32, 18)
    p.drawLine(QPointF(-30, 0), QPointF(-16, 0))
    p.drawLine(QPointF(16, 0), QPointF(30, 0))


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #


def _defaults_transformer(obj) -> None:
    obj.sn_mva, obj.u1n_kv, obj.u2n_kv, obj.x_pct = 10.0, 40.0, 12.0, 7.0


def _defaults_line(obj) -> None:
    obj.r_ohm_per_km, obj.x_ohm_per_km = 0.16, 0.08
    obj.b_s_per_km, obj.length_km = 3.0e-6, 1.0


def _defaults_load(obj) -> None:
    obj.p_mw, obj.cos_phi, obj.pf_sign = 1.0, 0.9, "lag"


def _defaults_shunt(obj) -> None:
    obj.q_mvar = 2.0


def _defaults_source(obj) -> None:
    obj.u_pu, obj.theta_deg = 1.0, 0.0


def _defaults_impedance(obj) -> None:
    obj.r, obj.x = 0.0, 1.0


ELEMENT_UI: dict[str, ElementUI] = {e.type: e for e in (
    ElementUI("bus", "Bus", "Busbar or node. Everything connects to a bus.",
              "bus", BusItem, draw_icon=_icon_bus),
    ElementUI("source", "External grid",
              "Voltage source behind a Thevenin impedance, given in ohm or by its "
              "fault level (Ik or Sk).",
              "SRC", SourceItem, _defaults_source, _icon_source),
    ElementUI("transformer", "Transformer", "Two-winding, series reactance.",
              "T", TransformerItem, _defaults_transformer, _icon_transformer),
    ElementUI("line", "Line / Cable", "PI model, n identical lines in parallel.",
              "LI", LineItem, _defaults_line, _icon_line),
    ElementUI("load", "Load", "Power drawn at a nominal voltage (constant impedance).",
              "LD", LoadItem, _defaults_load, _icon_load),
    ElementUI("shunt", "Shunt", "Capacitor (Q > 0) or reactor (Q < 0).",
              "SH", ShuntCapItem, _defaults_shunt, _icon_shunt),
    ElementUI("impedance", "Impedance", "Raw R + jX, series or to ground.",
              "Z", ImpedanceItem, _defaults_impedance, _icon_impedance),
)}


def item_class(type_name: str) -> type:
    return ELEMENT_UI[type_name].item_class


def id_prefix(type_name: str) -> str:
    entry = ELEMENT_UI.get(type_name)
    return entry.id_prefix if entry else type_name[:3].upper()


def apply_defaults(obj) -> None:
    entry = ELEMENT_UI.get(obj.TYPE)
    if entry is not None:
        entry.defaults(obj)


def make_item(comp, document, layout: dict | None = None) -> ComponentItem:
    """Build the graphics item for a component, applying its saved layout."""
    layout = layout or {}
    cls = item_class(comp.TYPE)
    if cls is BusItem:
        item = BusItem(comp.id, document,
                       orientation=layout.get("orientation", "V"),
                       length=float(layout.get("length", st.BUS_LENGTH)))
    else:
        item = cls(comp.id, document)
        rotation = float(layout.get("rotation", 0) or 0)
        if rotation:
            item.setRotation(rotation)
    item.setPos(float(layout.get("x", 0)), float(layout.get("y", 0)))
    off = layout.get("label_offset")
    if off:
        item.set_label_offset(float(off[0]), float(off[1]))
    return item
