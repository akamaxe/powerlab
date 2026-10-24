"""The schematic canvas: grid, snapping, connection state machine, result overlay."""

from __future__ import annotations

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QTransform
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

from ..core.components import TYPE_REGISTRY, buses_of, is_series
from . import style as st
from .commands import Connect, MoveItems, add_component_connected
from .items import (
    BusItem,
    ComponentItem,
    OverlayBadge,
    TerminalItem,
    WireItem,
    make_item,
)

SCENE_RECT = QRectF(-5000, -5000, 10000, 10000)


class SchematicScene(QGraphicsScene):
    """Holds the graphics items and keeps them in step with the document."""

    selectionInfoChanged = Signal(str)      # id of the last-pressed object
    statusMessage = Signal(str)
    requestProperties = Signal(str)
    studyAtBusRequested = Signal(str, str)  # study key, bus id
    contextActionRequested = Signal(str, str)   # action, element id

    def __init__(self, document, parent=None):
        # Parent the scene to the document (a QObject) when no parent is given.
        # Without an owner, Python may collect the scene while it still owns the
        # item objects in C++, and the next wrapper finalisation is a
        # use-after-free. Tying the two lifetimes together removes the hazard
        # for good, including when the main window swaps documents.
        super().__init__(parent if parent is not None else document)
        self.document = document
        self.grid = st.GRID
        self.setSceneRect(SCENE_RECT)          # explicit: an auto rect jitters
        self.setBackgroundBrush(st.SHEET)

        self._items: dict[str, ComponentItem] = {}
        self._wires: dict[str, WireItem] = {}
        self._wires_by_owner: dict[str, set[WireItem]] = {}
        self._overlay: list[OverlayBadge] = []
        self._deleted_keepalive: list = []

        self._connecting: TerminalItem | None = None
        self._preview: QGraphicsLineItem | None = None
        self._drag_start: dict[str, dict] | None = None
        self._last_pressed: str | None = None

        document.structureChanged.connect(self.rebuild)
        document.componentChanged.connect(self._on_component_changed)
        document.connectivityChanged.connect(self._on_connectivity_changed)
        document.layoutChanged.connect(self._on_layout_changed)
        document.parametersChanged.connect(self.refresh_labels)
        document.resultsInvalidated.connect(self.clear_overlay)
        self.rebuild()

    def detach(self) -> None:
        """Stop tracking the document. Call before discarding this scene."""
        try:
            self.document.structureChanged.disconnect(self.rebuild)
            self.document.componentChanged.disconnect(self._on_component_changed)
            self.document.connectivityChanged.disconnect(
                self._on_connectivity_changed)
            self.document.layoutChanged.disconnect(self._on_layout_changed)
            self.document.parametersChanged.disconnect(self.refresh_labels)
            self.document.resultsInvalidated.disconnect(self.clear_overlay)
        except TypeError:
            pass                     # already disconnected

    # ------------------------------------------------------------------ #
    # Building
    # ------------------------------------------------------------------ #

    def item_for(self, obj_id: str) -> ComponentItem | None:
        return self._items.get(obj_id)

    def rebuild(self) -> None:
        """Recreate every item from the document. Cheap at these sizes."""
        selected = {i.comp_id for i in self.selectedItems()
                    if isinstance(i, ComponentItem)}
        for item in list(self._items.values()):
            self.removeItem(item)
        for wire in list(self._wires.values()):
            self.removeItem(wire)
        self._items.clear()
        self._wires.clear()
        self._wires_by_owner.clear()
        self.clear_overlay()

        self.document.ensure_layout()
        layout = self.document.project.layout
        for obj in self.document.network:
            item = make_item(obj, self.document, layout.get(obj.id))
            item.doubleClicked.connect(self.requestProperties.emit)
            self.addItem(item)
            self._items[obj.id] = item
            if obj.id in selected:
                item.setSelected(True)

        self.rebuild_wires()

    def rebuild_wires(self) -> None:
        for wire in list(self._wires.values()):
            self.removeItem(wire)
        self._wires.clear()
        self._wires_by_owner.clear()

        for comp in self.document.network.components.values():
            item = self._items.get(comp.id)
            if item is None:
                continue
            for index, (_, _, field) in enumerate(item.TERMINALS):
                bus_id = getattr(comp, field, None)
                if not bus_id or bus_id not in self._items:
                    continue
                wire = WireItem(comp.id, index, bus_id, self)
                key = wire.key
                saved = self.document.project.wires.get(key)
                if saved:
                    wire.waypoints = [QPointF(p[0], p[1]) for p in saved]
                self.addItem(wire)
                self._wires[key] = wire
                self._wires_by_owner.setdefault(comp.id, set()).add(wire)
                self._wires_by_owner.setdefault(bus_id, set()).add(wire)
                wire.refresh()

    def refresh_labels(self) -> None:
        for item in self._items.values():
            item.refresh_from_model()

    # ------------------------------------------------------------------ #
    # Document signals
    # ------------------------------------------------------------------ #

    def _on_component_changed(self, obj_id: str) -> None:
        item = self._items.get(obj_id)
        if item is not None:
            item.refresh_from_model()
            for wire in self._wires_by_owner.get(obj_id, ()):
                wire.refresh()

    def _on_connectivity_changed(self, obj_id: str) -> None:
        self.rebuild_wires()
        item = self._items.get(obj_id)
        if item is not None:
            item.refresh_from_model()

    def _on_layout_changed(self, obj_id: str) -> None:
        item = self._items.get(obj_id)
        layout = self.document.project.layout.get(obj_id, {})
        if item is None:
            return
        item.setPos(float(layout.get("x", item.x())),
                    float(layout.get("y", item.y())))
        if isinstance(item, BusItem):
            item.set_orientation(layout.get("orientation", item.orientation))
            item.set_length(float(layout.get("length", item.length)))
        elif "rotation" in layout:
            item.setRotation(float(layout.get("rotation") or 0))
        self.notify_moved(item)

    # ------------------------------------------------------------------ #
    # Geometry sync
    # ------------------------------------------------------------------ #

    def notify_moved(self, item: ComponentItem) -> None:
        for wire in self._wires_by_owner.get(item.comp_id, ()):
            wire.refresh()
        for badge in self._overlay:
            if badge.target == item.comp_id:
                self._place_badge(badge, item)
        # A bus move also drags the attach points of everything tapping it.
        if isinstance(item, BusItem):
            for wire in list(self._wires.values()):
                if wire.bus_id == item.comp_id:
                    wire.refresh()

    def refresh_all_wires(self) -> None:
        for wire in self._wires.values():
            wire.refresh()

    # ------------------------------------------------------------------ #
    # Grid
    # ------------------------------------------------------------------ #

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, st.SHEET)
        scale = 1.0
        views = self.views()
        if views:
            scale = views[0].transform().m11()
        if scale < 0.4:
            return                              # a grid this dense is just noise

        g = self.grid
        left = int(rect.left()) - int(rect.left()) % g
        top = int(rect.top()) - int(rect.top()) % g

        minor, major = [], []
        x = left
        while x < rect.right():
            (major if x % (g * 5) == 0 else minor).append(
                QLineF(x, rect.top(), x, rect.bottom()))
            x += g
        y = top
        while y < rect.bottom():
            (major if y % (g * 5) == 0 else minor).append(
                QLineF(rect.left(), y, rect.right(), y))
            y += g

        if scale >= 0.75:
            painter.setPen(st.pen(st.GRID_MINOR, 1.0, cosmetic=True))
            painter.drawLines(minor)
        painter.setPen(st.pen(st.GRID_MAJOR, 1.0, cosmetic=True))
        painter.drawLines(major)

    def snap(self, p: QPointF) -> QPointF:
        return QPointF(round(p.x() / self.grid) * self.grid,
                       round(p.y() / self.grid) * self.grid)

    # ------------------------------------------------------------------ #
    # Connection state machine
    # ------------------------------------------------------------------ #

    def begin_connection(self, terminal: TerminalItem) -> None:
        self._connecting = terminal
        start = terminal.scenePos()
        self._preview = QGraphicsLineItem(QLineF(start, start))
        self._preview.setPen(st.pen(st.VALID_TARGET, 1.8, cosmetic=True,
                                    style=Qt.PenStyle.DashLine))
        self._preview.setZValue(st.Z_PREVIEW)
        self.addItem(self._preview)
        self._set_bus_highlight(True)
        self.statusMessage.emit(
            "Drag to a bus to connect. Release on empty space to create a new "
            "bus there. Esc cancels.")

    def _set_bus_highlight(self, on: bool) -> None:
        for item in self._items.values():
            if isinstance(item, BusItem):
                item.set_highlight(on)

    def _cancel_connection(self) -> None:
        if self._preview is not None:
            self.removeItem(self._preview)
            self._preview = None
        # The terminal grabbed the mouse on press, and the release is consumed
        # here without reaching Qt's default handler. Left grabbed, the next
        # click anywhere would start a new connection from that terminal.
        grabber = self.mouseGrabberItem()
        if grabber is not None:
            grabber.ungrabMouse()
        self._connecting = None
        self._set_bus_highlight(False)
        self.statusMessage.emit("")

    def _bus_under(self, scene_pos: QPointF) -> BusItem | None:
        for item in self.items(scene_pos):
            if isinstance(item, BusItem):
                return item
            parent = item.parentItem()
            if isinstance(parent, BusItem):
                return parent
        # Be forgiving: accept a near miss around the bar.
        for item in self.items(QRectF(scene_pos.x() - 10, scene_pos.y() - 10, 20, 20)):
            if isinstance(item, BusItem):
                return item
        return None

    def mouseMoveEvent(self, event):
        if self._connecting is not None and self._preview is not None:
            start = self._connecting.scenePos()
            self._preview.setLine(QLineF(start, event.scenePos()))
            target = self._bus_under(event.scenePos())
            for item in self._items.values():
                if isinstance(item, BusItem):
                    item.set_highlight("target" if item is target else "candidate")
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self._connecting is not None and event.button() == Qt.MouseButton.RightButton:
            self._cancel_connection()
            event.accept()
            return
        # Remember what was pressed: selectedItems() has no defined order, so
        # the property editor must not rely on it.
        item = self.itemAt(event.scenePos(), QTransform())
        owner = item
        while owner is not None and not isinstance(owner, ComponentItem):
            owner = owner.parentItem()
        self._last_pressed = owner.comp_id if isinstance(owner, ComponentItem) else None
        if self._last_pressed:
            self.selectionInfoChanged.emit(self._last_pressed)

        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self._connecting is None:
            self._drag_start = {
                i.comp_id: dict(self.document.project.layout.get(i.comp_id, {}))
                for i in self.selectedItems() if isinstance(i, ComponentItem)
            }

    def mouseReleaseEvent(self, event):
        if self._connecting is not None:
            terminal = self._connecting
            comp_item = terminal.parentItem()
            target = self._bus_under(event.scenePos())
            self._cancel_connection()
            if isinstance(comp_item, ComponentItem):
                if target is not None:
                    self.document.undo.push(Connect(
                        self.document, comp_item.comp_id,
                        terminal.field_name, target.comp_id))
                else:
                    self._create_bus_and_connect(
                        comp_item, terminal, event.scenePos())
            event.accept()
            return

        super().mouseReleaseEvent(event)
        self._commit_drag()

    def _commit_drag(self) -> None:
        if not self._drag_start:
            return
        moves = {}
        for obj_id, old in self._drag_start.items():
            item = self._items.get(obj_id)
            if item is None:
                continue
            new = dict(old)
            new["x"], new["y"] = item.x(), item.y()
            if abs(new["x"] - old.get("x", 0)) > 0.5 or \
               abs(new["y"] - old.get("y", 0)) > 0.5:
                moves[obj_id] = (old, new)
        self._drag_start = None
        if moves:
            self.document.undo.push(MoveItems(self.document, moves))

    def _create_bus_and_connect(self, comp_item, terminal, scene_pos) -> None:
        """Dropping a terminal on empty canvas creates the bus it needs."""
        doc = self.document
        bus_id = doc.new_id("bus")
        bus = TYPE_REGISTRY["bus"](bus_id, name=doc.new_name("bus", bus_id))
        pos = self.snap(scene_pos)
        layout = {"x": pos.x(), "y": pos.y(), "orientation": "V",
                  "length": st.BUS_LENGTH}
        doc.undo.beginMacro(f"Connect to new bus {bus.name}")
        from .commands import AddComponent
        doc.undo.push(AddComponent(doc, bus, layout))
        doc.undo.push(Connect(doc, comp_item.comp_id, terminal.field_name, bus_id))
        doc.undo.endMacro()

    def contextMenuEvent(self, event):
        item = self.itemAt(event.scenePos(), QTransform())
        while item is not None and not isinstance(item, ComponentItem):
            item = item.parentItem()
        if not isinstance(item, ComponentItem):
            super().contextMenuEvent(event)
            return
        if not item.isSelected():
            self.clearSelection()
            item.setSelected(True)
        self.selectionInfoChanged.emit(item.comp_id)
        self.build_context_menu(item).exec(event.screenPos())
        event.accept()

    def build_context_menu(self, item: ComponentItem) -> QMenu:
        """The right-click menu of one element (the selection it belongs to)."""
        menu = QMenu()
        obj_id = item.comp_id

        def act(label, action):
            a = menu.addAction(label)
            a.triggered.connect(
                lambda _=False: self.contextActionRequested.emit(action, obj_id))
            return a

        if isinstance(item, BusItem):
            for key, label in (("thevenin", "Thevenin equivalent here"),
                               ("short_circuit", "Three-phase short circuit here")):
                menu.addAction(label).triggered.connect(
                    lambda _=False, k=key: self.studyAtBusRequested.emit(k, obj_id))
            menu.addSeparator()
        act("Properties", "properties")
        menu.addSeparator()
        act("Cut", "cut")
        act("Copy", "copy")
        act("Duplicate", "duplicate")
        menu.addSeparator()
        if isinstance(item, BusItem):
            act("Switch vertical / horizontal", "flip")
        else:
            act("Rotate 90 deg", "rotate")
        in_service = act("In service", "toggle_service")
        in_service.setCheckable(True)
        in_service.setChecked(item.is_in_service())
        menu.addSeparator()
        act("Delete", "delete")
        return menu

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._connecting is not None:
            self._cancel_connection()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ #
    # Dropping new components from the palette
    # ------------------------------------------------------------------ #

    def place_component(self, type_name: str, scene_pos: QPointF) -> str:
        """Create a component of ``type_name`` at ``scene_pos``.

        A two-terminal branch dropped between two buses wires itself up; a
        shunt dropped on or just above a bus attaches to it.
        """
        doc = self.document
        pos = self.snap(scene_pos)
        cls = TYPE_REGISTRY[type_name]
        obj_id = doc.new_id(type_name)
        obj = cls(obj_id, name=doc.new_name(type_name, obj_id))
        _apply_defaults(obj)

        layout = {"x": pos.x(), "y": pos.y()}
        if type_name == "bus":
            layout.update({"orientation": "V", "length": st.BUS_LENGTH})
            from .commands import AddComponent
            doc.undo.push(AddComponent(doc, obj, layout))
            return obj_id

        layout.update({"rotation": 0, "mirrored": False})
        connections = self._auto_connect(obj, pos)
        add_component_connected(doc, obj, layout, connections)
        return obj_id

    #: How far a freshly placed component reaches to find a bus to attach to.
    SNAP_RADIUS = 95.0

    def _auto_connect(self, obj, pos: QPointF) -> dict[str, str]:
        """Attach the new component's terminals to the buses nearest each one.

        Each terminal takes its closest free bus, so dropping a transformer
        between two bars wires it up the way it looks.
        """
        from .items import ITEM_CLASSES
        terminals = ITEM_CLASSES[obj.TYPE].TERMINALS
        ranked = {
            field: self._buses_by_distance(
                QPointF(pos.x() + tx, pos.y() + ty), self.SNAP_RADIUS)
            for (tx, ty, field) in terminals
        }
        out: dict[str, str] = {}
        taken: set[str] = set()
        # Closest match first, so a terminal that is unambiguous claims its bus
        # before an ambiguous one can steal it.
        order = sorted(ranked, key=lambda f: ranked[f][0][1] if ranked[f] else 1e9)
        for field in order:
            for bus_id, _ in ranked[field]:
                if bus_id not in taken:
                    out[field] = bus_id
                    taken.add(bus_id)
                    break
        return out

    def _buses_by_distance(self, scene_pos: QPointF, max_dist: float
                           ) -> list[tuple[str, float]]:
        found = []
        for obj_id, item in self._items.items():
            if not isinstance(item, BusItem):
                continue
            p = item.attach_point(scene_pos)
            d = ((p.x() - scene_pos.x()) ** 2 + (p.y() - scene_pos.y()) ** 2) ** 0.5
            if d <= max_dist:
                found.append((obj_id, d))
        return sorted(found, key=lambda t: t[1])

    def _nearest_bus(self, scene_pos: QPointF, max_dist: float) -> str | None:
        found = self._buses_by_distance(scene_pos, max_dist)
        return found[0][0] if found else None

    # ------------------------------------------------------------------ #
    # Result overlay
    # ------------------------------------------------------------------ #

    def clear_overlay(self) -> None:
        for badge in self._overlay:
            self.removeItem(badge)
        self._overlay.clear()

    def show_annotations(self, annotations) -> None:
        """Pin each :class:`~psa.core.results.Annotation` next to its element.

        Bus badges sit above the bus, branch badges below the branch's own
        label lane, element badges to the right of the symbol, so each kind of
        text keeps its own lane on a dense diagram. Badges follow their element
        when it moves.
        """
        self.clear_overlay()
        colours = {"flow": st.RESULT_FLOW, "warn": st.RESULT_WARN}
        for ann in annotations:
            item = self._items.get(ann.target)
            if item is None:
                continue
            badge = OverlayBadge(list(ann.lines), QPointF(0, 0),
                                 colours.get(ann.colour))
            badge.target = ann.target
            badge.slot = ann.slot
            self.addItem(badge)
            self._overlay.append(badge)
            self._place_badge(badge, item)

    def _place_badge(self, badge: OverlayBadge, item: ComponentItem) -> None:
        w, h = badge.size()
        anchor = item.scenePos()
        if badge.slot == "bus":
            half = getattr(item, "length", 0.0) / 2.0
            badge.setPos(anchor.x() - w / 2.0, anchor.y() - half - 26 - h)
        elif badge.slot == "branch" and round(item.rotation()) % 180 == 90:
            # A vertical branch: beside it, clear of the buses above and below.
            badge.setPos(anchor.x() + 34.0, anchor.y() - h / 2.0)
        elif badge.slot == "branch":
            # Below the name AND the detail line, both of which live in the
            # branch's own label lane.
            lane = getattr(item, "LABEL_Y", 20.0) + 30.0
            badge.setPos(anchor.x() - w / 2.0, anchor.y() + lane)
        else:
            badge.setPos(anchor.x() + 24.0, anchor.y() - h / 2.0)


def _apply_defaults(obj) -> None:
    """Give a freshly placed component values that already make sense."""
    t = obj.TYPE
    if t == "transformer":
        obj.sn_mva, obj.u1n_kv, obj.u2n_kv, obj.x_pct = 10.0, 40.0, 12.0, 7.0
    elif t == "line":
        obj.r_ohm_per_km, obj.x_ohm_per_km = 0.16, 0.08
        obj.b_s_per_km, obj.length_km = 3.0e-6, 1.0
    elif t == "load":
        obj.p_mw, obj.cos_phi, obj.pf_sign = 1.0, 0.9, "lag"
    elif t == "shunt":
        obj.q_mvar = 2.0
    elif t == "source":
        obj.u_pu, obj.theta_deg = 1.0, 0.0
    elif t == "impedance":
        obj.r, obj.x = 0.0, 1.0


# --------------------------------------------------------------------------- #
# View
# --------------------------------------------------------------------------- #


class SchematicView(QGraphicsView):
    """Pan, zoom and palette drops."""

    MIN_SCALE, MAX_SCALE = 0.12, 20.0

    def __init__(self, scene: SchematicScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        # Anchor under the mouse, or zooming drifts away from the cursor.
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self._panning = False
        self._space = False
        self._pan_from = None
        self._pending_tool: str | None = None

    # -- zoom ---------------------------------------------------------- #

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 ** (event.angleDelta().y() / 120.0)
            scale = self.transform().m11() * factor
            if self.MIN_SCALE <= scale <= self.MAX_SCALE:
                self.scale(factor, factor)
                self.viewport().update()      # the grid density changes
            event.accept()
            return
        super().wheelEvent(event)

    def zoom_by(self, factor: float) -> None:
        scale = self.transform().m11() * factor
        if self.MIN_SCALE <= scale <= self.MAX_SCALE:
            self.scale(factor, factor)
            self.viewport().update()

    def zoom_fit(self) -> None:
        items = self.scene().itemsBoundingRect()
        if items.isEmpty():
            self.resetTransform()
            self.centerOn(0, 0)
            return
        self.fitInView(items.adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)
        if self.transform().m11() > 1.6:
            self.resetTransform()
            self.centerOn(items.center())
        self.viewport().update()

    def zoom_reset(self) -> None:
        self.resetTransform()
        self.viewport().update()

    # -- pan ----------------------------------------------------------- #

    toolConsumed = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton or (
                self._space and event.button() == Qt.MouseButton.LeftButton):
            self._panning = True
            self._pan_from = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._pending_tool and event.button() == Qt.MouseButton.LeftButton:
            self.scene().place_component(
                self._pending_tool, self.mapToScene(event.position().toPoint()))
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.arm_tool(None)
                self.toolConsumed.emit()
            event.accept()
            return
        if self._pending_tool and event.button() == Qt.MouseButton.RightButton:
            self.arm_tool(None)
            self.toolConsumed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_from is not None:
            delta = event.position().toPoint() - self._pan_from
            self._pan_from = event.position().toPoint()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._panning = False
            self._pan_from = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._space else Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._pending_tool:
            self.arm_tool(None)
            self.toolConsumed.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    # -- palette placement --------------------------------------------- #

    def arm_tool(self, type_name: str | None) -> None:
        """Next left click places this component type."""
        self._pending_tool = type_name
        self.setCursor(Qt.CursorShape.CrossCursor if type_name else Qt.CursorShape.ArrowCursor)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText() and \
                event.mimeData().text() in TYPE_REGISTRY:
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText() and \
                event.mimeData().text() in TYPE_REGISTRY:
            event.acceptProposedAction()

    def dropEvent(self, event):
        text = event.mimeData().text()
        if text in TYPE_REGISTRY:
            self.scene().place_component(text, self.mapToScene(event.position().toPoint()))
            event.acceptProposedAction()
