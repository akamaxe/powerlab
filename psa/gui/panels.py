"""Dock panels: component palette, property editor, variables and results."""

from __future__ import annotations

import html

from PySide6.QtCore import QMimeData, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
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
from .registry import ELEMENT_UI

def symbol_icon(type_name: str, size: int = 34) -> QIcon:
    """The palette icon: the type's symbol drawn by its registry entry."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(size / 2, size / 2)
    scale = 0.42 if type_name != "bus" else 0.40
    painter.scale(scale, scale)
    painter.setPen(st.pen(st.INK, 3.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    entry = ELEMENT_UI.get(type_name)
    if entry is not None:
        entry.draw_icon(painter)
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
        for entry in ELEMENT_UI.values():
            item = QListWidgetItem(symbol_icon(entry.type), entry.label)
            item.setData(Qt.ItemDataRole.UserRole, entry.type)
            item.setToolTip(f"{entry.tooltip}\n\nClick to place, or drag onto the "
                            f"canvas. Hold Shift while placing to add several.")
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
    """Form bound to the selected object(s), generated from their FIELDS.

    Changes to the model update the widgets in place, so the field being
    edited keeps the keyboard focus; the form is only rebuilt when the set of
    fields changes (another object, a mode that shows other fields, a new
    bus). With several objects of one type selected, a commit applies to all
    of them in one undo step; fields whose values differ show empty.
    """

    #: Placeholder of a field whose value differs across the selection.
    SEVERAL = "(several values)"

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.obj_ids: list[str] = []
        self._building = False
        self._shape = None

        self.setWidgetResizable(True)
        self._body = QWidget()
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(6)
        self.setWidget(self._body)

        self._title = QLabel("Nothing selected")
        self._title.setObjectName("headline")
        self._title.setWordWrap(True)
        self._title.setTextFormat(Qt.TextFormat.RichText)
        self._layout.addWidget(self._title)

        self._form_host = QWidget()
        self._host_layout = QVBoxLayout(self._form_host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self._host_layout.setSpacing(8)
        self._layout.addWidget(self._form_host)
        self._layout.addStretch(1)

        self._widgets: dict[str, QWidget] = {}
        self._value_hints: dict[str, QLabel] = {}
        self._specs: dict[str, FieldSpec] = {}

        document.componentChanged.connect(self._on_changed)
        document.connectivityChanged.connect(self._on_changed)
        document.structureChanged.connect(self.rebind)
        document.parametersChanged.connect(self.rebind)

    # -- binding ------------------------------------------------------- #

    @property
    def obj_id(self) -> str | None:
        """The bound object when exactly one is bound."""
        return self.obj_ids[0] if len(self.obj_ids) == 1 else None

    def _objects(self) -> list:
        net = self.document.network
        return [net.get(i) for i in self.obj_ids if i in net]

    def rebind(self) -> None:
        """Re-read whatever is bound: in place when the fields are the same."""
        self.bind_many(self.obj_ids)

    def _on_changed(self, obj_id: str) -> None:
        if obj_id in self.obj_ids:
            self.bind_many(self.obj_ids)

    def bind(self, obj_id: str | None) -> None:
        self.bind_many([obj_id] if obj_id else [])

    def bind_many(self, obj_ids: list[str]) -> None:
        net = self.document.network
        ids = [i for i in obj_ids if i in net]
        objs = [net.get(i) for i in ids]
        if len({type(o) for o in objs}) > 1:
            self.obj_ids = ids
            self._show_message(f"{len(ids)} objects of different types selected. "
                               f"Select objects of one type to edit them together.")
            return
        if not objs:
            self.obj_ids = []
            self._show_message("Nothing selected")
            return
        specs = [s for s in field_specs(objs[0])
                 if all(s.is_visible(o) for o in objs)
                 and (len(objs) == 1 or (s.kind != "bus" and s.name != "name"))]
        shape = (tuple(ids), tuple(s.name for s in specs),
                 tuple(self.document.bus_choices()))
        self.obj_ids = ids
        first = objs[0]
        if len(objs) == 1:
            self._title.setText(f"{first.name or first.id}   <small>({first.TYPE})</small>")
        else:
            self._title.setText(f"{len(objs)} objects   <small>({first.TYPE})</small>")
        if shape != self._shape:
            self._build(objs, specs)
            self._shape = shape
        else:
            self._refresh(objs)

    def _show_message(self, text: str) -> None:
        self._clear_form()
        self._shape = None
        self._title.setText(text)

    def _group_of(self, spec: FieldSpec) -> str:
        if spec.name in ("name", "in_service"):
            return "General"
        if spec.kind == "bus":
            return "Connection"
        return spec.group if spec.group != "General" else "Parameters"

    def _build(self, objs: list, specs: list[FieldSpec]) -> None:
        self._clear_form()
        self._building = True
        scope = self.document.params.values()
        forms: dict[str, QFormLayout] = {}
        for spec in specs:
            group = self._group_of(spec)
            if group not in forms:
                box = QGroupBox(group)
                form = QFormLayout(box)
                form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignVCenter)
                form.setSpacing(5)
                self._host_layout.addWidget(box)
                forms[group] = form
            widget, hint = self._make_widget(spec)
            self._widgets[spec.name] = widget
            self._specs[spec.name] = spec
            if hint is not None:
                self._value_hints[spec.name] = hint
            label = spec.label + (f"  [{spec.unit}]" if spec.unit else "")
            if hint is not None:
                row = QWidget()
                box_layout = QVBoxLayout(row)
                box_layout.setContentsMargins(0, 0, 0, 0)
                box_layout.setSpacing(1)
                box_layout.addWidget(widget)
                box_layout.addWidget(hint)
                forms[group].addRow(label, row)
            else:
                forms[group].addRow(label, widget)
            if spec.tooltip:
                widget.setToolTip(spec.tooltip)
        if len(objs) == 1:
            ident = QLineEdit(objs[0].id)
            ident.setReadOnly(True)
            ident.setToolTip("Internal id, used by parametric bindings")
            forms.setdefault("General", QFormLayout()).addRow("id", ident)
        self._refresh(objs, scope)
        self._building = False

    def _clear_form(self) -> None:
        while self._host_layout.count():
            item = self._host_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._widgets.clear()
        self._value_hints.clear()
        self._specs.clear()

    # -- values -------------------------------------------------------- #

    def _common(self, objs: list, name: str):
        """``(value, same)``: the first object's value and whether all agree."""
        values = [getattr(o, name, None) for o in objs]
        return values[0], all(v == values[0] for v in values[1:])

    def _refresh(self, objs: list | None = None, scope: dict | None = None) -> None:
        """Push the model's values into the existing widgets, without signals."""
        objs = objs if objs is not None else self._objects()
        if not objs:
            return
        scope = scope if scope is not None else self.document.params.values()
        params = self.document.params
        for name, widget in self._widgets.items():
            spec = self._specs[name]
            value, same = self._common(objs, name)
            widget.blockSignals(True)
            try:
                if isinstance(widget, QCheckBox):
                    widget.setTristate(not same)
                    if same:
                        widget.setChecked(bool(value))
                    else:
                        widget.setCheckState(Qt.CheckState.PartiallyChecked)
                elif isinstance(widget, QComboBox):
                    index = (widget.findData(value or "") if spec.kind == "bus"
                             else widget.findText(str(value)))
                    widget.setCurrentIndex(index if same and index >= 0 else -1)
                elif isinstance(widget, QLineEdit) and not widget.hasFocus():
                    exprs = {params.expression_for(o.id, name) for o in objs}
                    expr = next(iter(exprs)) if len(exprs) == 1 else None
                    if not same and not expr:
                        widget.setText("")
                        widget.setPlaceholderText(self.SEVERAL)
                    elif spec.kind == "str":
                        widget.setText("" if value is None else str(value))
                    else:
                        widget.setText(expr if expr else _num_text(value))
                    if isinstance(widget, ParametricLineEdit):
                        widget.set_bound(bool(expr))
                    hint = self._value_hints.get(name)
                    if hint is not None:
                        self._update_hint(hint, expr, value, scope)
            finally:
                widget.blockSignals(False)

    # -- widget construction ------------------------------------------- #

    def _make_widget(self, spec: FieldSpec):
        if spec.kind == "bool":
            w = QCheckBox()
            w.toggled.connect(lambda checked, n=spec.name: self._commit(n, checked))
            return w, None

        if spec.kind == "choice":
            w = QComboBox()
            w.addItems(list(spec.choices))
            w.currentTextChanged.connect(
                lambda text, n=spec.name: self._commit(n, text) if text else None)
            return w, None

        if spec.kind == "bus":
            w = QComboBox()
            w.addItem("(not connected)", "")
            for bus_id, label in self.document.bus_choices():
                w.addItem(label, bus_id)
            w.currentIndexChanged.connect(
                lambda _i, n=spec.name, ww=w: self._commit_bus(n, ww.currentData()))
            return w, None

        if spec.kind == "str":
            w = QLineEdit()
            w.editingFinished.connect(
                lambda n=spec.name, ww=w: self._commit(n, ww.text()))
            return w, None

        # Numeric, possibly parametric.
        w = ParametricLineEdit()
        hint = None
        if spec.parametric:
            hint = QLabel("")
            hint.setStyleSheet("color:#5b666e; font-size:10px;")
            hint.setVisible(False)
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

    def _targets(self, field: str, value) -> list[str]:
        """The bound objects whose ``field`` differs from ``value``."""
        net = self.document.network
        return [i for i in self.obj_ids if i in net and getattr(net.get(i), field) != value]

    def _commit(self, field: str, value) -> None:
        if self._building or not self.obj_ids:
            return
        targets = self._targets(field, value)
        if not targets:
            return
        doc = self.document
        spec = self._specs.get(field)
        label = spec.label if spec else field
        doc.undo.beginMacro(f"Set {label}")
        try:
            for obj_id in targets:
                doc.undo.push(SetField(doc, obj_id, field, value, label))
        finally:
            doc.undo.endMacro()

    def _commit_bus(self, field: str, bus_id) -> None:
        if self._building or not self.obj_id:
            return
        obj = self.document.network.get(self.obj_id)
        new = bus_id or (None if field == "bus_to" else "")
        if getattr(obj, field) == new:
            return
        self.document.undo.push(Connect(self.document, self.obj_id, field, new))

    def _invalid(self, widget, message: str) -> None:
        if widget is not None:
            widget.set_invalid(True)
            widget.setToolTip(message)

    def _commit_numeric(self, field: str, spec: FieldSpec, text: str) -> None:
        if self._building or not self.obj_ids:
            return
        widget = self._widgets.get(field)
        text = text.strip()
        doc = self.document
        net = doc.network
        objs = self._objects()
        exprs = {doc.params.expression_for(o.id, field) for o in objs}
        current_expr = next(iter(exprs)) if len(exprs) == 1 else None
        value0, same = self._common(objs, field)

        if text == "":
            if len(objs) > 1 and not same:
                return                          # left "(several values)" alone
            doc.undo.beginMacro(f"Clear {spec.label}")
            try:
                for o in objs:
                    if doc.params.expression_for(o.id, field):
                        doc.undo.push(SetBinding(doc, o.id, field, None))
                    if getattr(o, field) is not None and spec.name in (
                            "u_base_kv", "u_nominal_kv", "un_kv"):
                        doc.undo.push(SetField(doc, o.id, field, None))
            finally:
                doc.undo.endMacro()
            if widget:
                widget.set_invalid(False)
            return

        # Focus leaving an untouched field fires editingFinished too. Comparing
        # the text, not the value, also avoids re-committing a rounded display.
        if same and text == (current_expr or _num_text(value0)):
            return

        plain = _try_complex(text) if spec.kind == "complex" else _try_float(text)
        expr = None
        try:
            if plain is not None:
                value = _coerce_numeric(spec, plain)
            elif spec.parametric:
                value = _coerce_numeric(spec, safe_eval(text, doc.params.values(),
                                                        target=field))
                expr = text
            else:
                self._invalid(widget, f"{spec.label} takes a number.")
                return
        except (ExpressionError, TypeError, ValueError, OverflowError) as exc:
            self._invalid(widget, str(exc))
            return
        if not isinstance(value, complex):
            if spec.minimum is not None and value < spec.minimum:
                self._invalid(widget, f"{spec.label} must be at least {spec.minimum:g}.")
                return
            if spec.maximum is not None and value > spec.maximum:
                self._invalid(widget, f"{spec.label} must be at most {spec.maximum:g}.")
                return

        if widget:
            widget.set_invalid(False)
            widget.set_bound(expr is not None)
            widget.setToolTip(spec.tooltip or "")
        # One undo step for every binding and value change, or nothing at all.
        changes = []
        for o in objs:
            binding_changes = doc.params.expression_for(o.id, field) != expr
            value_changes = getattr(net.get(o.id), field) != value
            if binding_changes or value_changes:
                changes.append((o.id, binding_changes, value_changes))
        if not changes:
            return
        doc.undo.beginMacro(f"Set {spec.label}")
        try:
            for obj_id, binding_changes, value_changes in changes:
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


class CasesPanel(QWidget):
    """The project's study cases: what F5 runs, and how each one is set up."""

    newRequested = Signal(str)              # study key
    editRequested = Signal(str)             # case id
    duplicateRequested = Signal(str)
    deleteRequested = Signal(str)
    runRequested = Signal(list)             # case ids
    runAllRequested = Signal()

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.placeholder = QLabel(
            "No cases yet: F5 runs every study with its default options. "
            "Save one with New... or from the Calculate menu.")
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet("color:#5b666e; font-size:11px;")
        layout.addWidget(self.placeholder)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(
            lambda item: self.editRequested.emit(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.btn_new = QPushButton("New...")
        self.btn_new.setMenu(self._study_menu())
        self.btn_edit = QPushButton("Edit...")
        self.btn_edit.clicked.connect(lambda: self._emit_current(self.editRequested))
        self.btn_dup = QPushButton("Duplicate")
        self.btn_dup.clicked.connect(lambda: self._emit_current(self.duplicateRequested))
        self.btn_del = QPushButton("Delete")
        self.btn_del.clicked.connect(lambda: self._emit_current(self.deleteRequested))
        for b in (self.btn_new, self.btn_edit, self.btn_dup, self.btn_del):
            row.addWidget(b)
        layout.addLayout(row)

        row = QHBoxLayout()
        self.btn_run = QPushButton("Run selected")
        self.btn_run.clicked.connect(
            lambda: self.runRequested.emit(self.selected_ids()))
        self.btn_run_all = QPushButton("Run all (F5)")
        self.btn_run_all.clicked.connect(self.runAllRequested.emit)
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_run_all)
        row.addStretch(1)
        layout.addLayout(row)

        self.list.itemSelectionChanged.connect(self._update_buttons)
        document.casesChanged.connect(self.refresh)
        self.refresh()

    def _study_menu(self) -> QMenu:
        from ..core.study import studies_by_group

        menu = QMenu(self)
        for group, studies in studies_by_group().items():
            sub = menu.addMenu(group)
            for study in studies:
                sub.addAction(study.title).triggered.connect(
                    lambda _=False, k=study.key: self.newRequested.emit(k))
        return menu

    def _emit_current(self, signal) -> None:
        ids = self.selected_ids()
        if ids:
            signal.emit(ids[0])

    def selected_ids(self) -> list[str]:
        return [i.data(Qt.ItemDataRole.UserRole) for i in self.list.selectedItems()]

    def set_document(self, document) -> None:
        self.document = document
        document.casesChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        from ..core.study import STUDY_REGISTRY

        keep = set(self.selected_ids())
        self.list.clear()
        for case in self.document.project.cases:
            study = STUDY_REGISTRY.get(case.study)
            label = case.title or (study.title if study else case.study)
            extra = []
            if case.exclude:
                extra.append(f"{len(case.exclude)} out of service")
            if case.overrides:
                extra.append(f"{len(case.overrides)} override(s)")
            item = QListWidgetItem(label + (f"   [{', '.join(extra)}]" if extra else ""))
            item.setData(Qt.ItemDataRole.UserRole, case.id)
            item.setToolTip(f"id: {case.id}\nstudy: {case.study}"
                            + ("" if study else "  (unknown study)"))
            self.list.addItem(item)
            if case.id in keep:
                item.setSelected(True)
        self.placeholder.setVisible(not self.document.project.cases)
        self._update_buttons()

    def _update_buttons(self) -> None:
        has = bool(self.selected_ids())
        for b in (self.btn_edit, self.btn_dup, self.btn_del, self.btn_run):
            b.setEnabled(has)


class ResultsPanel(QTabWidget):
    """One tab per study result, plus a log tab."""

    #: The result shown by the current tab (``None`` for the log).
    resultActivated = Signal(object)

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self._stale = False
        self._results: list = []
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(st.result_font(9))
        self.addTab(self.log, "Log")
        document.resultsInvalidated.connect(self.mark_stale)
        self.currentChanged.connect(self._on_current_changed)

    # -- state --------------------------------------------------------- #

    def mark_stale(self) -> None:
        self._stale = True
        for i in range(self.count()):
            title = self.tabText(i)
            if title != "Log" and not title.endswith(" *"):
                self.setTabText(i, title + " *")
        self.setToolTip("Results are stale. Run the studies again.")

    def show_results(self, results) -> None:
        self.blockSignals(True)
        while self.count() > 1:
            self.removeTab(0)
        self._results = list(results)
        for res in reversed(results):
            self.insertTab(0, self._build_tab(res), _short_title(res.title))
        self._stale = False
        self.setToolTip("")
        self.setCurrentIndex(0)
        self.blockSignals(False)

    def result_at(self, index: int):
        return self._results[index] if 0 <= index < len(self._results) else None

    def _on_current_changed(self, index: int) -> None:
        self.resultActivated.emit(self.result_at(index))

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
            # Escaped: "0.9952 pu < -0.5345 deg" would otherwise read as a tag.
            head = QLabel(html.escape(res.headline).replace("\n", "<br>"))
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
    widget = _CopyTable(len(table.rows), max(1, cols))
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


class _CopyTable(QTableWidget):
    """A read-only result table whose cells copy as tab-separated text."""

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Copy", self.copy_selection)
        menu.addAction("Copy table", self.copy_all)
        menu.exec(event.globalPos())

    def copy_selection(self) -> None:
        cells = self.selectedIndexes()
        if not cells:
            return
        rows = sorted({c.row() for c in cells})
        cols = sorted({c.column() for c in cells})
        QGuiApplication.clipboard().setText(self._text(rows, cols, header=False))

    def copy_all(self) -> None:
        QGuiApplication.clipboard().setText(
            self._text(range(self.rowCount()), range(self.columnCount()), header=True))

    def _text(self, rows, cols, header: bool) -> str:
        lines = []
        if header and self.horizontalHeader().isVisible():
            lines.append("\t".join(
                (self.horizontalHeaderItem(c).text() if self.horizontalHeaderItem(c) else "")
                for c in cols))
        for r in rows:
            lines.append("\t".join(
                (self.item(r, c).text() if self.item(r, c) else "") for c in cols))
        return "\n".join(lines)


def _looks_numeric(text: str) -> bool:
    stripped = str(text).strip()
    if not stripped:
        return False
    return stripped[0].isdigit() or stripped[0] in "+-." or "e" in stripped[:2]


def _short_title(title: str) -> str:
    return title.split(":")[0].split(" seen from")[0].strip()
