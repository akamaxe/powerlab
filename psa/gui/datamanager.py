"""The data manager: one editable table per component type.

Every column comes from the type's ``FIELDS``, so a new field shows up by
itself. Cells take the same input as the property editor (numbers,
expressions over the project variables, which become bindings, an empty text
for "none") and every edit goes through an undo command. A filter box and the
column headers sort and narrow each table; double-clicking a row shows the
element on the diagram.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QStyledItemDelegate,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.components import TYPE_REGISTRY, FieldSpec, field_specs
from .commands import Connect
from .editing import (
    FieldInputError,
    apply_numeric,
    clear_numeric,
    num_text,
    parse_numeric,
    set_value,
)
from .registry import ELEMENT_UI

NUMERIC = ("float", "int", "complex")
BOUND = QColor("#e8f1fb")           # a cell whose value comes from an expression
HIDDEN = QColor("#9aa3a9")          # a field the element's mode does not use


class ComponentTableModel(QAbstractTableModel):
    """The objects of one type as rows, their fields as columns."""

    #: An edit was refused: the message says why.
    editRejected = Signal(str)

    def __init__(self, document, type_name: str, parent=None):
        super().__init__(parent)
        self.document = document
        self.type_name = type_name
        self.specs: list[FieldSpec] = list(field_specs(TYPE_REGISTRY[type_name]))
        self._ids: list[str] = []
        self.reload()

    # -- structure ------------------------------------------------------ #

    def reload(self) -> None:
        self.beginResetModel()
        self._ids = [o.id for o in self.document.network.of_type(self.type_name)]
        self.endResetModel()

    def obj_id(self, row: int) -> str:
        return self._ids[row]

    def row_of(self, obj_id: str) -> int:
        return self._ids.index(obj_id) if obj_id in self._ids else -1

    def refresh_object(self, obj_id: str) -> None:
        row = self.row_of(obj_id)
        if row >= 0:
            self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._ids)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.specs) + 1

    def spec(self, column: int) -> FieldSpec | None:
        return self.specs[column - 1] if column > 0 else None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        spec = self.spec(section)
        if role == Qt.ItemDataRole.DisplayRole:
            if spec is None:
                return "Id"
            return f"{spec.label} [{spec.unit}]" if spec.unit else spec.label
        if role == Qt.ItemDataRole.ToolTipRole and spec is not None:
            return spec.tooltip or spec.name
        return None

    # -- cells ---------------------------------------------------------- #

    def _obj(self, row: int):
        return self.document.network.get(self._ids[row])

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        obj = self._obj(index.row())
        spec = self.spec(index.column())
        if spec is None:
            return obj.id if role in (Qt.ItemDataRole.DisplayRole,
                                      Qt.ItemDataRole.EditRole) else None
        value = getattr(obj, spec.name)
        expr = self.document.params.expression_for(obj.id, spec.name)
        if role == Qt.ItemDataRole.CheckStateRole and spec.kind == "bool":
            return Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DisplayRole:
            if spec.kind == "bool":
                return None
            if spec.kind == "bus":
                bus = self.document.network.buses.get(value or "")
                return (bus.name or bus.id) if bus else ""
            return num_text(value)
        if role == Qt.ItemDataRole.EditRole:
            if spec.kind == "bus":
                return value or ""
            return expr if expr else num_text(value)
        if role == Qt.ItemDataRole.ToolTipRole:
            if expr:
                return f"{spec.name} = {expr}"
            return spec.tooltip or None
        if role == Qt.ItemDataRole.BackgroundRole and expr:
            return BOUND
        if role == Qt.ItemDataRole.ForegroundRole and not spec.is_visible(obj):
            return HIDDEN
        if role == Qt.ItemDataRole.TextAlignmentRole and spec.kind in NUMERIC:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        spec = self.spec(index.column())
        if spec is None:
            return base
        if spec.kind == "bool":
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base | Qt.ItemFlag.ItemIsEditable

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        spec = self.spec(index.column())
        if spec is None or not index.isValid():
            return False
        obj_id = self._ids[index.row()]
        doc = self.document
        if spec.kind == "bool":
            if role != Qt.ItemDataRole.CheckStateRole:
                return False
            checked = Qt.CheckState(value) == Qt.CheckState.Checked
            set_value(doc, [obj_id], spec, checked)
            return True
        if role != Qt.ItemDataRole.EditRole:
            return False
        text = "" if value is None else str(value).strip()
        if spec.kind == "bus":
            return self._connect(obj_id, spec, text)
        if spec.kind == "choice":
            if text not in spec.choices:
                self.editRejected.emit(f"{spec.label} is one of {', '.join(spec.choices)}.")
                return False
            set_value(doc, [obj_id], spec, text)
            return True
        if spec.kind in NUMERIC:
            try:
                if text == "":
                    clear_numeric(doc, [obj_id], spec)
                else:
                    apply_numeric(doc, [obj_id], spec, *parse_numeric(doc, spec, text))
            except FieldInputError as exc:
                self.editRejected.emit(str(exc))
                return False
            return True
        set_value(doc, [obj_id], spec, text)
        return True

    def _connect(self, obj_id: str, spec: FieldSpec, text: str) -> bool:
        net = self.document.network
        bus_id = text if text in net.buses else next(
            (b.id for b in net.buses.values() if b.name == text), None)
        if text and bus_id is None:
            self.editRejected.emit(f"There is no bus '{text}'.")
            return False
        new = bus_id or (None if spec.name == "bus_to" else "")
        if getattr(net.get(obj_id), spec.name) != new:
            self.document.undo.push(Connect(self.document, obj_id, spec.name, new))
        return True


class _ChoiceDelegate(QStyledItemDelegate):
    """A combo box for choice and bus columns, a line edit for the rest."""

    def __init__(self, model: ComponentTableModel, parent=None):
        super().__init__(parent)
        self.model = model

    def _spec(self, index):
        return self.model.spec(index.model().mapToSource(index).column()
                               if hasattr(index.model(), "mapToSource") else index.column())

    def createEditor(self, parent, option, index):
        spec = self._spec(index)
        if spec is not None and spec.kind in ("choice", "bus"):
            combo = QComboBox(parent)
            if spec.kind == "choice":
                for c in spec.choices:
                    combo.addItem(c, c)
            else:
                combo.addItem("(none)", "")
                for bus_id, label in self.model.document.bus_choices():
                    combo.addItem(label, bus_id)
            return combo
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            editor.setCurrentIndex(max(0, editor.findData(index.data(Qt.ItemDataRole.EditRole))))
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)
            return
        super().setModelData(editor, model, index)


class _TypePage(QWidget):
    """One component type: a filter box over a sortable table."""

    def __init__(self, model: ComponentTableModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter (any column)")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.proxy.setFilterFixedString)

        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(-1, Qt.SortOrder.AscendingOrder)   # network order
        self.view.setItemDelegate(_ChoiceDelegate(model, self.view))
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                  | QAbstractItemView.EditTrigger.EditKeyPressed
                                  | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(self.filter)
        layout.addWidget(self.view)

    def source_row(self, proxy_index) -> int:
        return self.proxy.mapToSource(proxy_index).row()


class DataManagerPanel(QWidget):
    """Tabs of component tables, in palette order, titled with their counts."""

    #: Show this element on the diagram.
    locateRequested = Signal(str)
    #: An edit was refused, for the status bar.
    message = Signal(str)

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.hint = QLabel("Double-click an id to show the element on the diagram; "
                           "numeric cells take expressions over the project variables.")
        self.hint.setStyleSheet("color:#5b666e; font-size:11px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        layout.addWidget(self.hint)
        self.pages: dict[str, _TypePage] = {}
        self.document = None
        self.set_document(document)

    def set_document(self, document) -> None:
        if self.document is not None:
            for signal, slot in self._connections():
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self.document = document
        self.tabs.clear()
        self.pages.clear()
        for type_name in ELEMENT_UI:
            model = ComponentTableModel(document, type_name, self)
            model.editRejected.connect(self.message)
            page = _TypePage(model)
            page.view.doubleClicked.connect(
                lambda index, p=page: self._on_double_click(p, index))
            self.pages[type_name] = page
            self.tabs.addTab(page, "")
        for signal, slot in self._connections():
            signal.connect(slot)
        self._retitle()

    def _connections(self):
        d = self.document
        return [(d.structureChanged, self._reload), (d.parametersChanged, self._reload),
                (d.componentChanged, self._refresh), (d.connectivityChanged, self._refresh)]

    def _retitle(self) -> None:
        for i, (type_name, page) in enumerate(self.pages.items()):
            self.tabs.setTabText(i, f"{ELEMENT_UI[type_name].label} ({page.model.rowCount()})")

    def _reload(self) -> None:
        for page in self.pages.values():
            page.model.reload()
        self._retitle()

    def _refresh(self, obj_id: str) -> None:
        for page in self.pages.values():
            page.model.refresh_object(obj_id)

    def _on_double_click(self, page: _TypePage, index) -> None:
        if index.column() == 0:
            self.locateRequested.emit(page.model.obj_id(page.source_row(index)))

    def show_type(self, type_name: str) -> _TypePage:
        page = self.pages[type_name]
        self.tabs.setCurrentWidget(page)
        return page
