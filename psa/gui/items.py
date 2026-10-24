"""Graphics items for the single-line diagram.

Connectivity is *not* stored here. An item knows only its component id; the
buses it attaches to come from the core network, so the drawing can never drift
out of step with the model. Geometry flows one way: an item moves, the scene is
told, the affected wires recompute their path.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QPainterPath, QPainterPathStroker, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsSimpleTextItem,
)

from . import style as st


# --------------------------------------------------------------------------- #
# Terminal
# --------------------------------------------------------------------------- #


class TerminalItem(QGraphicsItem):
    """A connection point on a component.

    It is a *child* item with movement disabled that accepts mouse presses, so
    pressing it never reaches the parent's drag handler. Qt's event propagation
    does the mode disambiguation for us: press the body to move, press a
    terminal to draw a connection. No modal tool state at all.
    """

    def __init__(self, parent: "ComponentItem", index: int, pos: QPointF,
                 field_name: str):
        super().__init__(parent)
        self.index = index
        self.field_name = field_name
        self._hover = False
        self.setPos(pos)
        self.setZValue(st.Z_TERMINAL)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)

    # -- geometry ------------------------------------------------------ #

    def radius(self) -> float:
        return st.TERMINAL_R_HOVER if self._hover else st.TERMINAL_R

    def boundingRect(self) -> QRectF:
        r = st.TERMINAL_R_HOVER + 2.0
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter, option, widget=None):
        parent = self.parentItem()
        connected = bool(parent and parent.terminal_bus(self.index))
        r = self.radius()
        painter.setPen(st.pen(st.TERMINAL_ACTIVE if self._hover else st.TERMINAL,
                              1.2, cosmetic=True))
        painter.setBrush(st.TERMINAL_ACTIVE if self._hover
                         else (st.INK if connected else Qt.GlobalColor.white))
        painter.drawEllipse(QPointF(0, 0), r, r)

    # -- interaction --------------------------------------------------- #

    def hoverEnterEvent(self, event):
        self.prepareGeometryChange()
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.prepareGeometryChange()
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            scene = self.scene()
            if scene is not None and hasattr(scene, "begin_connection"):
                scene.begin_connection(self)
                event.accept()
                return
        super().mousePressEvent(event)


# --------------------------------------------------------------------------- #
# Base component item
# --------------------------------------------------------------------------- #


class ComponentItem(QGraphicsObject):
    """Common behaviour: identity, terminals, label, snapping, move reporting."""

    geometryChanged = Signal(str)
    doubleClicked = Signal(str)

    #: (x, y, bus-field-name) for each terminal, in local coordinates.
    TERMINALS: tuple[tuple[float, float, str], ...] = ()

    def __init__(self, comp_id: str, document):
        super().__init__()
        self.comp_id = comp_id
        self.document = document
        self._hover = False
        self._label_offset = QPointF(0, -30)

        # Without ItemSendsGeometryChanges, itemChange never fires: items would
        # move and wires would silently stay behind. Set once, here, so no
        # subclass can forget it.
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(st.Z_SYMBOL)

        self.terminals: list[TerminalItem] = []
        for i, (tx, ty, field_name) in enumerate(self.TERMINALS):
            self.terminals.append(TerminalItem(self, i, QPointF(tx, ty), field_name))

        self.label = QGraphicsSimpleTextItem("", self)
        self.label.setFont(st.label_font())
        self.label.setZValue(st.Z_LABEL)
        self.sublabel = QGraphicsSimpleTextItem("", self)
        self.sublabel.setFont(st.detail_font())
        self.sublabel.setZValue(st.Z_LABEL)
        self.refresh_from_model()

    # -- model access -------------------------------------------------- #

    @property
    def component(self):
        # Broad on purpose: this is read from paint() and from wire routing,
        # and a half-torn-down document must degrade to "no data", never raise.
        try:
            return self.document.network.get(self.comp_id)
        except Exception:  # noqa: BLE001
            return None

    def terminal_bus(self, index: int) -> str | None:
        comp = self.component
        if comp is None or index >= len(self.TERMINALS):
            return None
        return getattr(comp, self.TERMINALS[index][2], None) or None

    def is_in_service(self) -> bool:
        comp = self.component
        return bool(comp is None or comp.in_service)

    def display_text(self) -> str:
        comp = self.component
        return (comp.name or comp.id) if comp is not None else self.comp_id

    def subtitle(self) -> str:
        return ""

    def refresh_from_model(self) -> None:
        """Re-read the component and update anything visual that depends on it."""
        self.label.setText(self.display_text())
        self.sublabel.setText(self.subtitle())
        ink = st.INK if self.is_in_service() else st.OUT_OF_SERVICE
        self.label.setBrush(ink)
        self.sublabel.setBrush(st.INK_LIGHT if self.is_in_service()
                               else st.OUT_OF_SERVICE)
        self._place_labels()
        self.update()

    def _place_labels(self) -> None:
        """Put the name and the detail line at the anchor, always upright.

        The anchor is designed for the unrotated symbol. Rotated, it turns with
        the symbol (so the text stays on the same side of it) but the text
        itself does not: it is counter-rotated and laid out in scene axes,
        centred under or over the anchor, or beside it when the anchor ends up
        to the left or right of the symbol.
        """
        rot = round(self.rotation()) % 360
        theta = math.radians(rot)
        c, s = math.cos(theta), math.sin(theta)
        a = self._label_offset
        ax, ay = a.x() * c - a.y() * s, a.x() * s + a.y() * c     # scene offset

        rect = self.label.boundingRect()
        sub = self.sublabel.boundingRect()
        has_sub = bool(self.sublabel.text())
        line = rect.height() - 2.0
        height = line + (sub.height() if has_sub else 0.0)
        if abs(ax) > abs(ay) + 1e-6:          # beside the symbol
            top = ay - height / 2.0
            if ax > 0:
                name_x, sub_x = ax, ax
            else:
                name_x, sub_x = ax - rect.width(), ax - sub.width()
        else:                                  # under or over it
            top = ay if ay >= 0 or rot == 0 else ay - height
            name_x, sub_x = ax - rect.width() / 2.0, ax - sub.width() / 2.0
        for item, x, y in ((self.label, name_x, top), (self.sublabel, sub_x, top + line)):
            item.setRotation(-rot)
            item.setPos(x * c + y * s, -x * s + y * c)            # back to local
        self.sublabel.setVisible(has_sub)

    def set_label_offset(self, dx: float, dy: float) -> None:
        """``(dx, dy)`` is where the *centre* of the name sits, in local units."""
        self._label_offset = QPointF(dx, dy)
        self._place_labels()

    # -- geometry ------------------------------------------------------ #

    def _set_geometry(self, **kwargs) -> None:
        """Mutate anything boundingRect() depends on, safely."""
        self.prepareGeometryChange()
        for k, v in kwargs.items():
            setattr(self, k, v)
        for i, (tx, ty, _) in enumerate(self.TERMINALS):
            if i < len(self.terminals):
                self.terminals[i].setPos(QPointF(tx, ty))
        self.update()
        self.geometryChanged.emit(self.comp_id)

    def attach_point(self, scene_pos: QPointF) -> QPointF:
        """Where a wire coming from ``scene_pos`` should meet this item."""
        return self.scenePos()

    def terminal_scene_pos(self, index: int) -> QPointF:
        if index < len(self.terminals):
            return self.terminals[index].scenePos()
        return self.scenePos()

    def terminal_direction(self, index: int) -> QPointF:
        """Unit vector, in scene axes, pointing out of terminal ``index``.

        Every terminal sits on the symbol's outline, so the direction from
        the symbol's origin to the terminal is the way a wire should leave.
        """
        if index >= len(self.TERMINALS):
            return QPointF(1.0, 0.0)
        tx, ty, _ = self.TERMINALS[index]
        origin = self.mapToScene(QPointF(0.0, 0.0))
        tip = self.mapToScene(QPointF(tx, ty))
        dx, dy = tip.x() - origin.x(), tip.y() - origin.y()
        norm = math.hypot(dx, dy) or 1.0
        return QPointF(dx / norm, dy / norm)

    # -- Qt hooks ------------------------------------------------------ #

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene() is not None:
            grid = getattr(self.scene(), "grid", st.GRID)
            return QPointF(round(value.x() / grid) * grid,
                           round(value.y() / grid) * grid)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            scene = self.scene()
            if scene is not None and hasattr(scene, "notify_moved"):
                scene.notify_moved(self)
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
        if change == QGraphicsItem.GraphicsItemChange.ItemRotationHasChanged:
            self._place_labels()
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit(self.comp_id)
        event.accept()

    # -- painting helpers ---------------------------------------------- #

    def current_pen(self):
        return st.symbol_pen(self.isSelected(), self._hover, self.is_in_service())


# --------------------------------------------------------------------------- #
# Bus
# --------------------------------------------------------------------------- #


class BusHandle(QGraphicsRectItem):
    """A grip at one end of a selected busbar; dragging it resizes the bar.

    The opposite end stays put, the dragged end follows the grid, and the
    length stays a whole number of double grid steps so the bar's centre
    remains on the grid.
    """

    SIZE = 8.0

    def __init__(self, bus: "BusItem", end: int):
        super().__init__(-self.SIZE / 2, -self.SIZE / 2, self.SIZE, self.SIZE, bus)
        self.bus = bus
        self.end = end                      # -1: first end, +1: second end
        self.setBrush(Qt.GlobalColor.white)
        self.setPen(st.pen(st.SELECTION, 1.4, cosmetic=True))
        self.setZValue(st.Z_HANDLE)
        self.setCursor(Qt.CursorShape.SizeVerCursor if bus.orientation == "V"
                       else Qt.CursorShape.SizeHorCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setVisible(False)
        self._start: dict | None = None

    def place(self) -> None:
        a, b = self.bus.endpoints()
        self.setPos(a if self.end < 0 else b)
        self.setCursor(Qt.CursorShape.SizeVerCursor if self.bus.orientation == "V"
                       else Qt.CursorShape.SizeHorCursor)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        doc = self.bus.document
        self._start = dict(doc.project.layout.get(self.bus.comp_id, {}))
        event.accept()

    def mouseMoveEvent(self, event):
        if self._start is None:
            return
        bus = self.bus
        grid = getattr(bus.scene(), "grid", st.GRID)
        a, b = (bus.mapToScene(p) for p in bus.endpoints())
        fixed = b if self.end < 0 else a
        pos = event.scenePos()
        vertical = bus.orientation == "V"
        dragged = round((pos.y() if vertical else pos.x()) / grid) * grid
        anchor = fixed.y() if vertical else fixed.x()
        step = 2 * grid
        length = max(step, round(abs(dragged - anchor) / step) * step)
        sign = 1.0 if dragged >= anchor else -1.0
        centre = anchor + sign * length / 2.0
        bus.set_length(length)
        bus.setPos(QPointF(fixed.x(), centre) if vertical else QPointF(centre, fixed.y()))
        if bus.scene() is not None and hasattr(bus.scene(), "notify_moved"):
            bus.scene().notify_moved(bus)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._start is not None and self.bus.scene() is not None:
            self.bus.scene().commit_bus_resize(self.bus, self._start)
        self._start = None
        event.accept()


class BusItem(ComponentItem):
    """A busbar: a thick bar that other components tap at any point along it."""

    TERMINALS = ()

    def __init__(self, comp_id: str, document, orientation: str = "V",
                 length: float = st.BUS_LENGTH):
        self.orientation = orientation
        self.length = float(length)
        self.highlight = ""                 # "" | "candidate" | "target"
        super().__init__(comp_id, document)
        self.setZValue(st.Z_BUS)
        self.handles = [BusHandle(self, -1), BusHandle(self, +1)]
        self._reposition_label()
        self._update_handles()

    def subtitle(self) -> str:
        comp = self.component
        if comp is not None and comp.u_base_kv:
            return f"{comp.u_base_kv:g} kV (pinned)"
        return ""

    def set_orientation(self, orientation: str) -> None:
        self._set_geometry(orientation=orientation)
        self._reposition_label()
        self._update_handles()

    def set_length(self, length: float) -> None:
        self._set_geometry(length=max(2 * st.GRID, float(length)))
        self._reposition_label()
        self._update_handles()

    def _update_handles(self) -> None:
        comp = self.component
        show = self.isSelected() and not (comp is not None and comp.kind == "junction")
        for handle in getattr(self, "handles", ()):
            handle.place()
            handle.setVisible(show)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._update_handles()
        return super().itemChange(change, value)

    def _reposition_label(self) -> None:
        if self.orientation == "V":
            self.set_label_offset(0, -self.length / 2 - 22)
        else:
            self.set_label_offset(0, -26)

    def endpoints(self) -> tuple[QPointF, QPointF]:
        half = self.length / 2.0
        if self.orientation == "V":
            return QPointF(0, -half), QPointF(0, half)
        return QPointF(-half, 0), QPointF(half, 0)

    def boundingRect(self) -> QRectF:
        half = self.length / 2.0
        pad = st.BUS_WIDTH / 2.0 + 4.0
        if self.orientation == "V":
            return QRectF(-pad, -half - pad, 2 * pad, self.length + 2 * pad)
        return QRectF(-half - pad, -pad, self.length + 2 * pad, 2 * pad)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def paint(self, painter, option, widget=None):
        a, b = self.endpoints()
        comp = self.component
        is_junction = comp is not None and comp.kind == "junction"
        if is_junction:
            painter.setPen(st.bus_pen(self.isSelected(), self._hover,
                                      self.is_in_service(), self.highlight))
            painter.setBrush(st.INK if self.is_in_service() else st.OUT_OF_SERVICE)
            painter.drawEllipse(QPointF(0, 0), 4.5, 4.5)
            if self.highlight == "target" or self.isSelected():
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(0, 0), 9.0, 9.0)
            return
        painter.setPen(st.bus_pen(self.isSelected(), self._hover,
                                  self.is_in_service(), self.highlight))
        painter.drawLine(a, b)

    def attach_point(self, scene_pos: QPointF) -> QPointF:
        """Project ``scene_pos`` onto the bar and clamp it to the ends.

        This is what makes several branches tap one busbar at different points,
        the way a real single-line diagram looks.
        """
        comp = self.component
        if comp is not None and comp.kind == "junction":
            return self.scenePos()
        a = self.mapToScene(self.endpoints()[0])
        b = self.mapToScene(self.endpoints()[1])
        ab = QPointF(b.x() - a.x(), b.y() - a.y())
        denom = ab.x() ** 2 + ab.y() ** 2
        if denom <= 0:
            return a
        ap = QPointF(scene_pos.x() - a.x(), scene_pos.y() - a.y())
        t = (ap.x() * ab.x() + ap.y() * ab.y()) / denom
        t = max(0.0, min(1.0, t))
        return QPointF(a.x() + t * ab.x(), a.y() + t * ab.y())

    def set_highlight(self, level) -> None:
        """``"target"``, ``"candidate"``, or falsy to clear (``True`` = candidate)."""
        level = "candidate" if level is True else (level or "")
        if self.highlight != level:
            self.highlight = level
            self.update()


# --------------------------------------------------------------------------- #
# Two-terminal branches
# --------------------------------------------------------------------------- #


class BranchItem(ComponentItem):
    """A series element drawn horizontally, terminals left and right."""

    HALF = st.BRANCH_SPAN / 2.0
    BODY = 16.0
    #: Text lane under the conductor. Bus voltage badges own the space above,
    #: so the two never overlap however dense the diagram gets.
    LABEL_Y = 22.0

    def __init__(self, comp_id, document):
        super().__init__(comp_id, document)
        self.set_label_offset(0, self.LABEL_Y)

    def boundingRect(self) -> QRectF:
        pad = st.SYMBOL_PEN + 4.0
        h = self.BODY + pad
        return QRectF(-self.HALF - pad, -h, 2 * (self.HALF + pad), 2 * h)

    def _leads(self, painter, inner: float) -> None:
        painter.drawLine(QPointF(-self.HALF, 0), QPointF(-inner, 0))
        painter.drawLine(QPointF(inner, 0), QPointF(self.HALF, 0))


class TransformerItem(BranchItem):
    """Two overlapping circles, the IEC two-winding symbol."""

    TERMINALS = ((-BranchItem.HALF, 0.0, "bus_hv"),
                 (BranchItem.HALF, 0.0, "bus_lv"))
    R = 11.0

    def subtitle(self) -> str:
        c = self.component
        if c is None:
            return ""
        return f"{c.sn_mva:g} MVA  {c.u1n_kv:g}/{c.u2n_kv:g} kV  {c.x_pct:g}%"

    def boundingRect(self) -> QRectF:
        pad = st.SYMBOL_PEN + 4.0
        return QRectF(-self.HALF - pad, -self.R - pad,
                      2 * (self.HALF + pad), 2 * (self.R + pad))

    def paint(self, painter, option, widget=None):
        painter.setPen(self.current_pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        off = self.R * 0.62
        painter.drawEllipse(QPointF(-off, 0), self.R, self.R)
        painter.drawEllipse(QPointF(off, 0), self.R, self.R)
        self._leads(painter, off + self.R)


class LineItem(BranchItem):
    """A PI line: the conductor plus a small capacitor stub at each end."""

    TERMINALS = ((-BranchItem.HALF, 0.0, "bus_from"),
                 (BranchItem.HALF, 0.0, "bus_to"))

    def subtitle(self) -> str:
        c = self.component
        if c is None:
            return ""
        n = f" x{c.n_parallel}" if c.n_parallel > 1 else ""
        return f"{c.length_km:g} km{n}"

    def boundingRect(self) -> QRectF:
        pad = st.SYMBOL_PEN + 4.0
        return QRectF(-self.HALF - pad, -14.0 - pad,
                      2 * (self.HALF + pad), 28.0 + 2 * pad)

    def paint(self, painter, option, widget=None):
        p = self.current_pen()
        painter.setPen(p)
        comp = self.component
        n = comp.n_parallel if comp is not None else 1

        if n > 1:
            # Two conductors drawn apart: the "2 identical lines" case reads at
            # a glance instead of hiding in a property field.
            painter.drawLine(QPointF(-self.HALF, -3), QPointF(self.HALF, -3))
            painter.drawLine(QPointF(-self.HALF, 3), QPointF(self.HALF, 3))
        else:
            painter.drawLine(QPointF(-self.HALF, 0), QPointF(self.HALF, 0))

        # Charging capacitors, one per end, drawn light.
        painter.setPen(st.pen(p.color(), st.LEAD_PEN))
        for sx in (-1, 1):
            x = sx * self.HALF * 0.62
            painter.drawLine(QPointF(x, 4 if n > 1 else 1), QPointF(x, 8))
            painter.drawLine(QPointF(x - 5, 8), QPointF(x + 5, 8))
            painter.drawLine(QPointF(x - 5, 11), QPointF(x + 5, 11))


class ImpedanceItem(BranchItem):
    """A plain rectangle, the IEC impedance symbol."""

    TERMINALS = ((-BranchItem.HALF, 0.0, "bus_from"),
                 (BranchItem.HALF, 0.0, "bus_to"))
    W, H = 30.0, 13.0

    def subtitle(self) -> str:
        c = self.component
        if c is None:
            return ""
        unit = "pu" if c.in_pu else "ohm"
        return f"{c.r:g} + j{c.x:g} {unit}"

    def boundingRect(self) -> QRectF:
        pad = st.SYMBOL_PEN + 4.0
        return QRectF(-self.HALF - pad, -self.H / 2 - pad,
                      2 * (self.HALF + pad), self.H + 2 * pad)

    def paint(self, painter, option, widget=None):
        painter.setPen(self.current_pen())
        painter.setBrush(Qt.GlobalColor.white)
        painter.drawRect(QRectF(-self.W / 2, -self.H / 2, self.W, self.H))
        self._leads(painter, self.W / 2)


# --------------------------------------------------------------------------- #
# One-terminal (shunt) components
# --------------------------------------------------------------------------- #


class ShuntItem(ComponentItem):
    """A component hanging below its bus, terminal at the top."""

    DROP = st.SHUNT_DROP
    TERMINALS = ((0.0, -st.SHUNT_DROP, "bus"),)

    def __init__(self, comp_id, document):
        super().__init__(comp_id, document)
        self.set_label_offset(0, 24)

    def boundingRect(self) -> QRectF:
        pad = st.SYMBOL_PEN + 5.0
        return QRectF(-16 - pad, -self.DROP - pad, 32 + 2 * pad,
                      self.DROP + 28 + 2 * pad)

    def _lead(self, painter, to_y: float) -> None:
        painter.drawLine(QPointF(0, -self.DROP), QPointF(0, to_y))


class LoadItem(ShuntItem):
    """A solid downward arrow, the standard load symbol."""

    def subtitle(self) -> str:
        c = self.component
        if c is None:
            return ""
        kw = c.p_mw * 1000.0
        p = f"{kw:g} kW" if abs(kw) < 1000 else f"{c.p_mw:g} MW"
        return f"{p}  {c.cos_phi:g} {c.pf_sign}"

    def paint(self, painter, option, widget=None):
        p = self.current_pen()
        painter.setPen(p)
        self._lead(painter, 4)
        tri = QPolygonF([QPointF(-9, 4), QPointF(9, 4), QPointF(0, 20)])
        painter.setBrush(p.color())
        painter.drawPolygon(tri)


class ShuntCapItem(ShuntItem):
    """Capacitor plates over an earth symbol; a reactor when Q is negative."""

    def subtitle(self) -> str:
        c = self.component
        if c is None:
            return ""
        return f"{c.q_mvar:g} MVAr"

    def paint(self, painter, option, widget=None):
        c = self.component
        inductive = c is not None and c.q_mvar < 0
        p = self.current_pen()
        painter.setPen(p)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if inductive:
            self._lead(painter, 2)
            path = QPainterPath(QPointF(0, 2))
            for k in range(3):
                path.arcTo(QRectF(-5, 2 + k * 6, 10, 8), 90, -180)
            painter.drawPath(path)
            base = 22
        else:
            self._lead(painter, 4)
            painter.drawLine(QPointF(-9, 4), QPointF(9, 4))
            painter.drawLine(QPointF(-9, 9), QPointF(9, 9))
            painter.drawLine(QPointF(0, 9), QPointF(0, 14))
            base = 14

        painter.setPen(st.pen(p.color(), st.LEAD_PEN))
        for i, w in enumerate((9, 6, 3)):
            painter.drawLine(QPointF(-w, base + i * 3), QPointF(w, base + i * 3))


class SourceItem(ComponentItem):
    """External grid: a circle with a sine, drawn to the left of its bus."""

    REACH = st.SHUNT_DROP
    TERMINALS = ((st.SHUNT_DROP, 0.0, "bus"),)
    R = 12.0

    def __init__(self, comp_id, document):
        super().__init__(comp_id, document)
        self.set_label_offset(0, -self.R - 42)

    def subtitle(self) -> str:
        c = self.component
        if c is None:
            return ""
        if c.z_mode == "ik":
            z = f"  Ik {c.ik_ka:g} kA"
        elif c.z_mode == "sk":
            z = f"  Sk {c.sk_mva:g} MVA"
        else:
            z = "" if c.z_th_ohm == 0 else "  Zth"
        return f"{c.u_pu:g} pu < {c.theta_deg:g} deg{z}"

    def boundingRect(self) -> QRectF:
        pad = st.SYMBOL_PEN + 4.0
        return QRectF(-self.R - pad, -self.R - pad,
                      self.REACH + self.R + 2 * pad, 2 * (self.R + pad))

    def paint(self, painter, option, widget=None):
        p = self.current_pen()
        painter.setPen(p)
        painter.setBrush(Qt.GlobalColor.white)
        painter.drawEllipse(QPointF(0, 0), self.R, self.R)
        painter.drawLine(QPointF(self.R, 0), QPointF(self.REACH, 0))

        painter.setPen(st.pen(p.color(), st.LEAD_PEN))
        path = QPainterPath(QPointF(-6.5, 0))
        path.cubicTo(QPointF(-3.2, -8), QPointF(0, 8), QPointF(0, 0))
        path.cubicTo(QPointF(0, -8), QPointF(3.2, 8), QPointF(6.5, 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


# --------------------------------------------------------------------------- #
# Wires
# --------------------------------------------------------------------------- #


def orthogonal_route(start: QPointF, horizontal_first: bool,
                     waypoints: list[QPointF], end: QPointF) -> list[QPointF]:
    """Corner points of an orthogonal polyline from ``start`` to ``end``.

    Each hop to the next waypoint (and finally to ``end``) is a straight line
    or an L; the first L leg runs along the axis the terminal points along,
    and every L turns the running axis.
    """
    points = [QPointF(start)]
    horizontal = horizontal_first
    current = QPointF(start)
    for target in list(waypoints) + [end]:
        if abs(current.x() - target.x()) > 0.5 and abs(current.y() - target.y()) > 0.5:
            corner = (QPointF(target.x(), current.y()) if horizontal
                      else QPointF(current.x(), target.y()))
            points.append(corner)
            horizontal = not horizontal
        elif abs(current.x() - target.x()) > 0.5:
            horizontal = False             # this hop was horizontal: turn next
        elif abs(current.y() - target.y()) > 0.5:
            horizontal = True
        points.append(QPointF(target))
        current = QPointF(target)
    return points


class WireHandle(QGraphicsRectItem):
    """A grip on one bend of a selected wire."""

    SIZE = 7.0

    def __init__(self, wire: "WireItem", index: int):
        super().__init__(-self.SIZE / 2, -self.SIZE / 2, self.SIZE, self.SIZE)
        self.wire = wire
        self.index = index
        self.setBrush(Qt.GlobalColor.white)
        self.setPen(st.pen(st.SELECTION, 1.3, cosmetic=True))
        self.setZValue(st.Z_HANDLE)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._dragging = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.wire.begin_edit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            scene = self.wire.scene()
            p = scene.snap(event.scenePos()) if hasattr(scene, "snap") else event.scenePos()
            self.wire.waypoints[self.index] = p
            self.wire.refresh()
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.wire.commit_edit()
            event.accept()


class WireItem(QGraphicsPathItem):
    """The lead from one component terminal to a bus.

    A scene-level item, never a child of either endpoint: parenting it to one
    side would make it follow that side and desynchronise from the other, and
    would make deletion order matter. It leaves the terminal the way the
    symbol points, runs orthogonally through the user's bends (``waypoints``,
    absolute scene points) and meets the bus at the projection of its last
    point.
    """

    PICK_WIDTH = 8.0

    def __init__(self, comp_id: str, terminal_index: int, bus_id: str,
                 scene_owner):
        super().__init__()
        self.comp_id = comp_id
        self.terminal_index = terminal_index
        self.bus_id = bus_id
        self._scene_owner = scene_owner
        self.waypoints: list[QPointF] = []
        self.handles: list[WireHandle] = []
        self._edit_start: list[list[float]] | None = None
        self._in_service = True
        self.setZValue(st.Z_WIRE)
        self.setPen(st.wire_pen())
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def key(self) -> str:
        return f"{self.comp_id}:{self.terminal_index}"

    def points(self) -> list[QPointF]:
        """Corner points of the drawn polyline (empty when an end is missing)."""
        owner = self._scene_owner
        comp_item = owner.item_for(self.comp_id)
        bus_item = owner.item_for(self.bus_id)
        if comp_item is None or bus_item is None:
            return []
        start = comp_item.terminal_scene_pos(self.terminal_index)
        direction = comp_item.terminal_direction(self.terminal_index)
        last = self.waypoints[-1] if self.waypoints else start
        end = bus_item.attach_point(last)
        return orthogonal_route(start, abs(direction.x()) >= abs(direction.y()),
                                self.waypoints, end)

    def refresh(self) -> None:
        pts = self.points()
        path = QPainterPath()
        if pts:
            path.moveTo(pts[0])
            for p in pts[1:]:
                path.lineTo(p)
        self.prepareGeometryChange()
        self.setPath(path)
        comp = None
        try:
            comp = self._scene_owner.document.network.get(self.comp_id)
        except KeyError:
            pass
        self._in_service = bool(comp is None or comp.in_service)
        self.setPen(st.wire_pen(self.isSelected(), self._in_service))
        self._place_handles()

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(self.PICK_WIDTH)
        return stroker.createStroke(self.path())

    # -- editing ------------------------------------------------------- #

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setPen(st.wire_pen(bool(value), self._in_service))
            self._place_handles()
        return super().itemChange(change, value)

    def _place_handles(self) -> None:
        scene = self.scene()
        want = len(self.waypoints) if self.isSelected() else 0
        while len(self.handles) > want:
            handle = self.handles.pop()
            if handle.scene() is not None:
                handle.scene().removeItem(handle)
        while len(self.handles) < want and scene is not None:
            handle = WireHandle(self, len(self.handles))
            scene.addItem(handle)
            self.handles.append(handle)
        for handle, p in zip(self.handles, self.waypoints):
            handle.setPos(p)

    def begin_edit(self) -> None:
        self._edit_start = [[p.x(), p.y()] for p in self.waypoints]

    def commit_edit(self) -> None:
        before, self._edit_start = self._edit_start, None
        after = [[p.x(), p.y()] for p in self.waypoints]
        if before != after and hasattr(self._scene_owner, "commit_wire"):
            self._scene_owner.commit_wire(self, before or [], after)

    def insertion_index(self, scene_pos: QPointF) -> int:
        """Where a bend clicked at ``scene_pos`` belongs among the waypoints."""
        pts = self.points()
        if len(pts) < 2:
            return len(self.waypoints)
        best, best_d = 0, float("inf")
        for k in range(len(pts) - 1):
            d = _distance_to_segment(scene_pos, pts[k], pts[k + 1])
            if d < best_d:
                best, best_d = k, d
        # Count the user's bends among the corners before that segment.
        user = {(round(p.x(), 3), round(p.y(), 3)) for p in self.waypoints}
        return sum(1 for p in pts[1:best + 1]
                   if (round(p.x(), 3), round(p.y(), 3)) in user)

    def mouseDoubleClickEvent(self, event):
        scene = self.scene()
        p = scene.snap(event.scenePos()) if hasattr(scene, "snap") else event.scenePos()
        self.begin_edit()
        self.waypoints.insert(self.insertion_index(event.scenePos()), p)
        self.commit_edit()
        event.accept()


def _distance_to_segment(p: QPointF, a: QPointF, b: QPointF) -> float:
    ax, ay, bx, by = a.x(), a.y(), b.x(), b.y()
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    t = 0.0 if denom == 0 else max(0.0, min(1.0, ((p.x() - ax) * dx + (p.y() - ay) * dy) / denom))
    return math.hypot(p.x() - (ax + t * dx), p.y() - (ay + t * dy))


# --------------------------------------------------------------------------- #
# Result overlay
# --------------------------------------------------------------------------- #


class OverlayBadge(QGraphicsItem):
    """A small annotation pinned near a bus or branch after a study runs."""

    def __init__(self, lines: list[str], anchor: QPointF, colour=None):
        super().__init__()
        self.lines = lines
        self.target = ""            # id of the element it annotates
        self.slot = "bus"           # bus | branch | element
        self.colour = colour or st.RESULT_TEXT
        self.setZValue(st.Z_OVERLAY)
        self.setPos(anchor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)
        self._rect = self._measure()

    def _measure(self) -> QRectF:
        metrics = QFontMetricsF(st.result_font())
        w = max((metrics.horizontalAdvance(s) for s in self.lines), default=0.0)
        line_h = metrics.height()
        return QRectF(0, 0, w + 8, len(self.lines) * line_h + 4)

    def size(self) -> tuple[float, float]:
        return self._rect.width(), self._rect.height()

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-1, -1, 1, 1)

    def paint(self, painter, option, widget=None):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(st.RESULT_BG)
        painter.drawRoundedRect(self._rect, 2, 2)
        painter.setPen(st.pen(self.colour, 1.0, cosmetic=True))
        font = st.result_font()
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        y = metrics.ascent() + 2.0
        for line in self.lines:
            painter.drawText(QPointF(4, y), line)
            y += metrics.height()


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

ITEM_CLASSES = {
    "bus": BusItem,
    "source": SourceItem,
    "transformer": TransformerItem,
    "line": LineItem,
    "load": LoadItem,
    "shunt": ShuntCapItem,
    "impedance": ImpedanceItem,
}


def make_item(comp, document, layout: dict | None = None) -> ComponentItem:
    """Build the graphics item for a component, applying its saved layout."""
    layout = layout or {}
    cls = ITEM_CLASSES[comp.TYPE]
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
