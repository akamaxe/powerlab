"""Dock panels: component palette, property editor, variables and results."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.components import TYPE_REGISTRY, FieldSpec, field_specs
from ..core.errors import ExpressionError
from ..core.params import safe_eval
from . import style as st
from .commands import Connect, SetBinding, SetField, SetParameters
from .items import ITEM_CLASSES, make_item

PALETTE_ITEMS = [
    ("bus", "Bus", "Busbar or node. Everything connects to a bus."),
    ("source", "Source / Grid", "Slack voltage source, optionally behind Zth."),
    ("transformer", "Transformer", "Two-winding, series reactance."),
    ("line", "Line / Cable", "PI model, n identical lines in parallel."),
    ("load", "Load", "Power drawn at a nominal voltage (constant impedance)."),
    ("shunt", "Shunt", "Capacitor (Q > 0) or reactor (Q < 0)."),
    ("impedance", "Impedance", "Raw R + jX, series or to ground."),
]


# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #


def symbol_icon(type_name: str, size: int = 34) -> QIcon:
    """Render the real schematic symbol as the palette icon."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(size / 2, size / 2)
    scale = 0.42 if type_name != "bus" else 0.40
    painter.scale(scale, scale)
    painter.setPen(st.pen(st.INK, 3.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if type_name == "bus":
        painter.setPen(st.pen(st.INK, 7.0))
        painter.drawLine(QPointF(0, -32), QPointF(0, 32))
    elif type_name == "transformer":
        painter.drawEllipse(QPointF(-7, 0), 11, 11)
        painter.drawEllipse(QPointF(7, 0), 11, 11)
        painter.drawLine(QPointF(-30, 0), QPointF(-18, 0))
        painter.drawLine(QPointF(18, 0), QPointF(30, 0))
    elif type_name == "line":
        painter.drawLine(QPointF(-30, -4), QPointF(30, -4))
        painter.drawLine(QPointF(-30, 4), QPointF(30, 4))
        for sx in (-1, 1):
            x = sx * 16
            painter.drawLine(QPointF(x, 6), QPointF(x, 12))
            painter.drawLine(QPointF(x - 7, 12), QPointF(x + 7, 12))
            painter.drawLine(QPointF(x - 7, 17), QPointF(x + 7, 17))
    elif type_name == "load":
        painter.drawLine(QPointF(0, -30), QPointF(0, -6))
        painter.setBrush(st.INK)
        from PySide6.QtGui import QPolygonF
        painter.drawPolygon(QPolygonF(
            [QPointF(-13, -6), QPointF(13, -6), QPointF(0, 20)]))
    elif type_name == "shunt":
        painter.drawLine(QPointF(0, -30), QPointF(0, -8))
        painter.drawLine(QPointF(-14, -8), QPointF(14, -8))
        painter.drawLine(QPointF(-14, 0), QPointF(14, 0))
        painter.drawLine(QPointF(0, 0), QPointF(0, 8))
        for i, w in enumerate((14, 9, 4)):
            painter.drawLine(QPointF(-w, 8 + i * 6), QPointF(w, 8 + i * 6))
    elif type_name == "source":
        painter.drawEllipse(QPointF(0, 0), 18, 18)
        painter.drawLine(QPointF(18, 0), QPointF(32, 0))
        from PySide6.QtGui import QPainterPath
        path = QPainterPath(QPointF(-10, 0))
        path.cubicTo(QPointF(-5, -12), QPointF(0, 12), QPointF(0, 0))
        path.cubicTo(QPointF(0, -12), QPointF(5, 12), QPointF(10, 0))
        painter.drawPath(path)
    elif type_name == "impedance":
        painter.setBrush(Qt.GlobalColor.white)
        painter.drawRect(-16, -9, 32, 18)
        painter.drawLine(QPointF(-30, 0), QPointF(-16, 0))
        painter.drawLine(QPointF(16, 0), QPointF(30, 0))
    painter.end()
    return QIcon(pix)


class PaletteList(QListWidget):
    """Component library. Click to arm placement, or drag onto the canvas."""

    toolPicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIconSize(QSize(34, 34))
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for type_name, label, tip in PALETTE_ITEMS:
            item = QListWidgetItem(symbol_icon(type_name), label)
            item.setData(Qt.ItemDataRole.UserRole, type_name)
            item.setToolTip(f"{tip}\n\nClick to place, or drag onto the canvas. "
                            f"Hold Shift while placing to add several.")
            self.addItem(item)
        self.itemClicked.connect(
            lambda it: self.toolPicked.emit(it.data(Qt.ItemDataRole.UserRole)))

    def startDrag(self, actions):
        item = self.currentItem()
        if item is None:
            return
        mime = QMimeData()
        mime.setText(item.data(Qt.ItemDataRole.UserRole))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon().pixmap(34, 34))
        drag.exec(Qt.DropAction.CopyAction)

    def clear_selection(self) -> None:
        self.clearSelection()
        self.setCurrentItem(None)


# --------------------------------------------------------------------------- #
# Property editor
# --------------------------------------------------------------------------- #


class ParametricLineEdit(QLineEdit):
    """A numeric field that also accepts an expression.

    Type a number and it is stored as a number. Type anything else and it is
    stored as a parametric binding, with the evaluated value shown beside it,
    the way a spreadsheet cell behaves.
    """

    committed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editingFinished.connect(lambda: self.committed.emit(self.text()))

    def set_bound(self, bound: bool) -> None:
        self.setProperty("bound", "true" if bound else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_invalid(self, invalid: bool) -> None:
        self.setProperty("invalid", "true" if invalid else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class PropertyEditor(QScrollArea):
    """Form bound to the currently selected object, generated from FIELDS."""

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.obj_id: str | None = None
        self._building = False

        self.setWidgetResizable(True)
        self._body = QWidget()
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(6)
        self.setWidget(self._body)

        self._title = QLabel("Nothing selected")
        self._title.setObjectName("headline")
        self._title.setWordWrap(True)
        self._layout.addWidget(self._title)

        self._form_host = QWidget()
        self._form = QFormLayout(self._form_host)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(5)
        self._layout.addWidget(self._form_host)
        self._layout.addStretch(1)

        self._widgets: dict[str, QWidget] = {}
        self._value_hints: dict[str, QLabel] = {}

        document.componentChanged.connect(self._on_changed)
        document.connectivityChanged.connect(self._on_changed)
        document.structureChanged.connect(self.rebind)
        document.parametersChanged.connect(self.rebind)

    # -- binding ------------------------------------------------------- #

    def rebind(self) -> None:
        """Re-read whatever is currently selected."""
        self.bind(self.obj_id)

    def _on_changed(self, obj_id: str) -> None:
        if obj_id == self.obj_id:
            self.bind(obj_id)

    def bind(self, obj_id: str | None) -> None:
        self.obj_id = obj_id
        self._clear_form()
        if not obj_id or obj_id not in self.document.network:
            self._title.setText("Nothing selected")
            return
        obj = self.document.network.get(obj_id)
        self._title.setText(f"{obj.name or obj.id}   <small>({obj.TYPE})</small>")
        self._title.setTextFormat(Qt.TextFormat.RichText)

        self._building = True
        scope = self.document.params.values()
        for spec in field_specs(obj):
            widget, hint = self._make_widget(obj, spec, scope)
            if widget is None:
                continue
            self._widgets[spec.name] = widget
            label = spec.label + (f"  [{spec.unit}]" if spec.unit else "")
            if hint is not None:
                row = QWidget()
                box = QVBoxLayout(row)
                box.setContentsMargins(0, 0, 0, 0)
                box.setSpacing(1)
                box.addWidget(widget)
                box.addWidget(hint)
                self._value_hints[spec.name] = hint
                self._form.addRow(label, row)
            else:
                self._form.addRow(label, widget)
            if spec.tooltip:
                widget.setToolTip(spec.tooltip)
        # Identity is read-only but worth showing.
        ident = QLineEdit(obj.id)
        ident.setReadOnly(True)
        ident.setToolTip("Internal id, used by parametric bindings")
        self._form.addRow("id", ident)
        self._building = False

    def _clear_form(self) -> None:
        while self._form.count():
            item = self._form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._widgets.clear()
        self._value_hints.clear()

    # -- widget construction ------------------------------------------- #

    def _make_widget(self, obj, spec: FieldSpec, scope: dict):
        value = getattr(obj, spec.name, None)

        if spec.kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(value))
            w.toggled.connect(
                lambda checked, n=spec.name: self._commit(n, checked))
            return w, None

        if spec.kind == "choice":
            w = QComboBox()
            w.addItems(list(spec.choices))
            if value in spec.choices:
                w.setCurrentText(value)
            w.currentTextChanged.connect(
                lambda text, n=spec.name: self._commit(n, text))
            return w, None

        if spec.kind == "bus":
            w = QComboBox()
            w.addItem("(not connected)", "")
            for bus_id, label in self.document.bus_choices():
                w.addItem(label, bus_id)
            index = w.findData(value or "")
            w.setCurrentIndex(max(0, index))
            w.currentIndexChanged.connect(
                lambda _i, n=spec.name, ww=w: self._commit_bus(n, ww.currentData()))
            return w, None

        if spec.kind == "str":
            w = QLineEdit("" if value is None else str(value))
            w.editingFinished.connect(
                lambda n=spec.name, ww=w: self._commit(n, ww.text()))
            return w, None

        # Numeric, possibly parametric.
        expr = self.document.params.expression_for(obj.id, spec.name)
        w = ParametricLineEdit()
        w.setText(expr if expr else _num_text(value))
        w.set_bound(bool(expr))
        hint = None
        if spec.parametric:
            hint = QLabel("")
            hint.setStyleSheet("color:#5b666e; font-size:10px;")
            self._update_hint(hint, expr, value, scope)
            w.setToolTip((spec.tooltip + "\n\n" if spec.tooltip else "")
                         + "Type a number, or an expression using your project "
                           "variables, e.g. (217 + A)/1000")
        w.committed.connect(
            lambda text, n=spec.name, s=spec: self._commit_numeric(n, s, text))
        return w, hint

    def _update_hint(self, hint: QLabel, expr, value, scope) -> None:
        if not expr:
            hint.setText("")
            hint.setVisible(False)
            return
        hint.setVisible(True)
        try:
            evaluated = safe_eval(expr, scope)
            hint.setText(f"= {_num_text(evaluated)}")
            hint.setStyleSheet("color:#5b666e; font-size:10px;")
        except ExpressionError as exc:
            hint.setText(str(exc))
            hint.setStyleSheet("color:#c0392b; font-size:10px;")

    # -- commits ------------------------------------------------------- #

    def _commit(self, field: str, value) -> None:
        if self._building or not self.obj_id:
            return
        obj = self.document.network.get(self.obj_id)
        if getattr(obj, field) == value:
            return
        self.document.undo.push(SetField(self.document, self.obj_id, field, value))

    def _commit_bus(self, field: str, bus_id) -> None:
        if self._building or not self.obj_id:
            return
        obj = self.document.network.get(self.obj_id)
        new = bus_id or (None if field == "bus_to" else "")
        if getattr(obj, field) == new:
            return
        self.document.undo.push(Connect(self.document, self.obj_id, field, new))

    def _commit_numeric(self, field: str, spec: FieldSpec, text: str) -> None:
        if self._building or not self.obj_id:
            return
        widget = self._widgets.get(field)
        text = text.strip()
        doc = self.document
        obj = doc.network.get(self.obj_id)
        current_expr = doc.params.expression_for(self.obj_id, field)

        if text == "":
            if current_expr:
                doc.undo.push(SetBinding(doc, self.obj_id, field, None))
            if getattr(obj, field) is not None and spec.name in (
                    "u_base_kv", "u_nominal_kv"):
                doc.undo.push(SetField(doc, self.obj_id, field, None))
            if widget:
                widget.set_invalid(False)
            return

        # Focus leaving an untouched field fires editingFinished too. Comparing
        # the text, not the value, also avoids re-committing a rounded display.
        if text == (current_expr or _num_text(getattr(obj, field))):
            return

        plain = _try_complex(text) if spec.kind == "complex" else _try_float(text)
        if plain is not None:
            try:
                value = _coerce_numeric(spec, plain)
            except (TypeError, ValueError, OverflowError) as exc:
                if widget:
                    widget.set_invalid(True)
                    widget.setToolTip(str(exc))
                return
            if widget:
                widget.set_invalid(False)
                widget.set_bound(False)
            self._push_numeric(spec, field, bool(current_expr), None, value)
            return

        if not spec.parametric:
            if widget:
                widget.set_invalid(True)
            return

        try:
            value = safe_eval(text, doc.params.values(), target=field)
            value = _coerce_numeric(spec, value)
        except (ExpressionError, TypeError, ValueError, OverflowError) as exc:
            if widget:
                widget.set_invalid(True)
                widget.setToolTip(str(exc))
            return

        if widget:
            widget.set_invalid(False)
            widget.set_bound(True)
        self._push_numeric(spec, field, text != current_expr, text, value)

    def _push_numeric(self, spec: FieldSpec, field: str, binding_changes: bool,
                      expr: str | None, value) -> None:
        """One undo step for a binding and/or value change, or nothing at all."""
        doc = self.document
        obj_id = self.obj_id
        value_changes = getattr(doc.network.get(obj_id), field) != value
        if not binding_changes and not value_changes:
            return
        # The value is already converted, so nothing below can raise half-way
        # through; the finally still guarantees the macro is always closed.
        doc.undo.beginMacro(f"Set {spec.label}")
        try:
            if binding_changes:
                doc.undo.push(SetBinding(doc, obj_id, field, expr))
            if value_changes:
                doc.undo.push(SetField(doc, obj_id, field, value, spec.label))
        finally:
            doc.undo.endMacro()


def _num_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, complex):
        return f"{value.real:g}{value.imag:+g}j" if value.imag else f"{value.real:g}"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _try_float(text: str):
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _try_complex(text: str):
    """A literal like ``1.6+0.3j`` (or MATLAB's ``1.6+0.3i``), else ``None``."""
    compact = text.replace(" ", "").replace(",", ".")
    if compact.endswith("i"):
        compact = compact[:-1] + "j"
    try:
        return complex(compact)
    except ValueError:
        return None


def _coerce_numeric(spec: FieldSpec, value):
    """Convert an evaluated number to the field's type, or raise ``ValueError``."""
    if spec.kind == "complex":
        return complex(value)
    if isinstance(value, complex):
        if value.imag != 0:
            raise ValueError(f"{spec.label} takes a real number, got {value}.")
        value = value.real
    if spec.kind == "int":
        return int(round(float(value)))
    return float(value)


# --------------------------------------------------------------------------- #
# Variables panel
# --------------------------------------------------------------------------- #


class VariablesPanel(QWidget):
    """Project variables, plus variables loaded from the user's Python script."""

    loadScriptRequested = Signal()
    reloadScriptRequested = Signal()

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self._building = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)

        self.script_label = QLabel("No variable script linked")
        self.script_label.setWordWrap(True)
        self.script_label.setStyleSheet("color:#5b666e; font-size:11px;")
        layout.addWidget(self.script_label)

        buttons = QHBoxLayout()
        self.btn_load = QPushButton("Link script...")
        self.btn_load.setToolTip(
            "Choose a Python file that defines variables. It is executed as "
            "ordinary Python, so only link files you trust.")
        self.btn_reload = QPushButton("Reload")
        self.btn_load.clicked.connect(self.loadScriptRequested.emit)
        self.btn_reload.clicked.connect(self.reloadScriptRequested.emit)
        buttons.addWidget(self.btn_load)
        buttons.addWidget(self.btn_reload)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "Expression", "Value", "Source"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_edited)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_add = QPushButton("Add variable")
        self.btn_del = QPushButton("Remove")
        self.btn_add.clicked.connect(self._add_variable)
        self.btn_del.clicked.connect(self._remove_variable)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_del)
        row.addStretch(1)
        layout.addLayout(row)

        hint = QLabel("Bind a field to a variable by typing an expression into "
                      "it, e.g. <code>(217 + A)/1000</code>")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5b666e; font-size:11px;")
        layout.addWidget(hint)

        document.parametersChanged.connect(self.refresh)
        document.structureChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self._building = True
        params = self.document.params
        scope, issues = params.scope()
        problems = {i.ids[0]: i.message for i in issues if i.ids}

        self.script_label.setText(
            f"Script: {params.script_path}" if params.script_path
            else "No variable script linked")
        self.btn_reload.setEnabled(bool(params.script_path))

        rows = [(name, "", scope.get(name), "script")
                for name in sorted(params.script_vars)]
        rows += [(name, expr, scope.get(name), "project")
                 for name, expr in params.project_vars]

        self.table.setRowCount(len(rows))
        for r, (name, expr, value, source) in enumerate(rows):
            editable = source == "project"
            for c, text in enumerate((name, expr, _num_text(value), source)):
                cell = QTableWidgetItem(text)
                if c in (2, 3) or not editable:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if name in problems and c == 1:
                    cell.setToolTip(problems[name])
                    cell.setForeground(st.INVALID)
                self.table.setItem(r, c, cell)
        self._building = False

    def _on_edited(self, item: QTableWidgetItem) -> None:
        if self._building:
            return
        params = self.document.params
        project_start = len(params.script_vars)
        index = item.row() - project_start
        if index < 0 or index >= len(params.project_vars):
            return
        name, expr = params.project_vars[index]
        if item.column() == 0:
            new = list(params.project_vars)
            new[index] = (item.text().strip(), expr)
        elif item.column() == 1:
            new = list(params.project_vars)
            new[index] = (name, item.text().strip())
        else:
            return
        # SetParameters re-evaluates the bindings itself, so undo and redo keep
        # the network in step with the variables.
        self.document.undo.push(SetParameters(
            self.document, params.script_path, new, params.bindings))

    def _add_variable(self) -> None:
        params = self.document.params
        used = {n for n, _ in params.project_vars} | set(params.script_vars)
        base = "A" if "A" not in used else "var"
        name, i = base, 1
        while name in used:
            name = f"{base}{i}"
            i += 1
        new = list(params.project_vars) + [(name, "0")]
        self.document.undo.push(SetParameters(
            self.document, params.script_path, new, params.bindings))

    def _remove_variable(self) -> None:
        params = self.document.params
        index = self.table.currentRow() - len(params.script_vars)
        if index < 0 or index >= len(params.project_vars):
            return
        new = [v for i, v in enumerate(params.project_vars) if i != index]
        self.document.undo.push(SetParameters(
            self.document, params.script_path, new, params.bindings))


# --------------------------------------------------------------------------- #
# Results panel
# --------------------------------------------------------------------------- #


class ResultsPanel(QTabWidget):
    """One tab per study result, plus a log tab."""

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self._stale = False
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(st.result_font(9))
        self.addTab(self.log, "Log")
        document.resultsInvalidated.connect(self.mark_stale)

    # -- state --------------------------------------------------------- #

    def mark_stale(self) -> None:
        self._stale = True
        for i in range(self.count()):
            title = self.tabText(i)
            if title != "Log" and not title.endswith(" *"):
                self.setTabText(i, title + " *")
        self.setToolTip("Results are stale. Run the studies again.")

    def show_results(self, results) -> None:
        while self.count() > 1:
            self.removeTab(0)
        for res in reversed(results):
            self.insertTab(0, self._build_tab(res), _short_title(res.title))
        self._stale = False
        self.setToolTip("")
        self.setCurrentIndex(0)

    def append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _build_tab(self, res) -> QWidget:
        page = QScrollArea()
        page.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(9)

        if res.headline:
            head = QLabel(res.headline.replace("\n", "<br>"))
            head.setObjectName("headline")
            head.setTextFormat(Qt.TextFormat.RichText)
            head.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            head.setFont(st.result_font(11))
            layout.addWidget(head)

        for warning in res.warnings:
            banner = QLabel(warning)
            banner.setObjectName("banner")
            banner.setWordWrap(True)
            layout.addWidget(banner)

        for table in res.tables:
            layout.addWidget(_table_block(table))

        layout.addStretch(1)
        page.setWidget(body)
        return page


def _table_block(table) -> QWidget:
    box = QFrame()
    box.setFrameShape(QFrame.Shape.NoFrame)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)

    title = QLabel(table.title)
    title.setStyleSheet("font-weight:600; color:#141719;")
    layout.addWidget(title)

    cols = max([len(table.headers)] + [len(r) for r in table.rows] or [1])
    widget = QTableWidget(len(table.rows), max(1, cols))
    if table.headers:
        widget.setHorizontalHeaderLabels(
            table.headers + [""] * (cols - len(table.headers)))
    else:
        widget.horizontalHeader().setVisible(False)
    widget.verticalHeader().setVisible(False)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
    widget.setAlternatingRowColors(True)
    widget.setFont(st.result_font(9))

    for r, row in enumerate(table.rows):
        for c, cell in enumerate(row):
            item = QTableWidgetItem(str(cell))
            if _looks_numeric(cell):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            widget.setItem(r, c, item)

    widget.resizeColumnsToContents()
    header = widget.horizontalHeader()
    if cols:
        header.setSectionResizeMode(cols - 1, QHeaderView.ResizeMode.Stretch)
    height = widget.horizontalHeader().height() * (1 if table.headers else 0)
    for r in range(widget.rowCount()):
        height += widget.rowHeight(r)
    widget.setFixedHeight(height + 6)
    widget.setSizeAdjustPolicy(QTableWidget.SizeAdjustPolicy.AdjustToContents)
    layout.addWidget(widget)

    if table.note:
        note = QLabel(table.note)
        note.setWordWrap(True)
        note.setStyleSheet("color:#5b666e; font-size:11px;")
        layout.addWidget(note)
    return box


def _looks_numeric(text: str) -> bool:
    stripped = str(text).strip()
    if not stripped:
        return False
    return stripped[0].isdigit() or stripped[0] in "+-." or "e" in stripped[:2]


def _short_title(title: str) -> str:
    return title.split(":")[0].split(" seen from")[0].strip()
