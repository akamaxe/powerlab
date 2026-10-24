"""Dialogs: base settings and study cases."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.cases import Case
from ..core.components import display_name
from ..core.errors import ExpressionError
from ..core.params import safe_eval


class BaseSettingsDialog(QDialog):
    """S_base, frequency and the bus where the base voltage is defined."""

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Base settings")
        self.document = document
        net = document.network

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.s_base = QDoubleSpinBox()
        self.s_base.setRange(1e-6, 1e6)
        self.s_base.setDecimals(4)
        self.s_base.setValue(net.s_base_mva)
        self.s_base.setSuffix(" MVA")
        form.addRow("Base power", self.s_base)

        self.f_hz = QDoubleSpinBox()
        self.f_hz.setRange(0.01, 1000.0)
        self.f_hz.setDecimals(2)
        self.f_hz.setValue(net.f_hz)
        self.f_hz.setSuffix(" Hz")
        form.addRow("Frequency", self.f_hz)

        self.ref_bus = QComboBox()
        for bus_id, label in document.bus_choices():
            self.ref_bus.addItem(label, bus_id)
        index = self.ref_bus.findData(net.base_ref_bus)
        self.ref_bus.setCurrentIndex(max(0, index))
        form.addRow("Base reference bus", self.ref_bus)

        self.ref_u = QDoubleSpinBox()
        self.ref_u.setRange(1e-6, 1e6)
        self.ref_u.setDecimals(4)
        self.ref_u.setValue(net.base_ref_u_kv or 0.0)
        self.ref_u.setSuffix(" kV")
        form.addRow("Base voltage there", self.ref_u)

        layout.addLayout(form)
        note = QLabel(
            "Every other bus derives its base by propagation from here, "
            "multiplying by the rated ratio at each transformer.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#5b666e; font-size:11px;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[float, float, str, float]:
        return (self.s_base.value(), self.f_hz.value(),
                self.ref_bus.currentData() or "", self.ref_u.value())


class CaseDialog(QDialog):
    """One study case: the study's options plus an optional network variant.

    The option widgets come from the study's ``option_fields``; an
    ``elements`` option asks the study for its choices again whenever another
    option changes (S1a lists the shunts on whichever buses are the ports).
    """

    def __init__(self, document, study, case: Case, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{study.title}: {case.id}")
        self.setMinimumWidth(460)
        self.document = document
        self.study = study
        self.base_case = case
        net = document.network
        options = study.default_options(net)
        options.update(case.options)

        layout = QVBoxLayout(self)
        if study.description:
            desc = QLabel(study.description)
            desc.setWordWrap(True)
            desc.setStyleSheet("color:#5b666e;")
            layout.addWidget(desc)
        if study.note:
            note = QLabel(study.note)
            note.setWordWrap(True)
            note.setStyleSheet(
                "background:#eef4fb; border:1px solid #c6d9ee; border-radius:3px;"
                "padding:6px; color:#194a7a; font-size:11px;")
            layout.addWidget(note)

        # Two tabs keep the dialog short enough for a scaled laptop screen.
        self.tabs = QTabWidget()
        options_page = QWidget()
        page = QVBoxLayout(options_page)
        form = QFormLayout()
        self.title_edit = QLineEdit(case.title)
        self.title_edit.setPlaceholderText(study.title)
        form.addRow("Case title", self.title_edit)

        self.widgets: dict[str, QWidget] = {}
        self.choice_boxes: dict[str, QGroupBox] = {}
        self.choice_checks: dict[str, dict[str, QCheckBox]] = {}
        for spec in study.option_fields:
            if spec.kind == "elements":
                box = QGroupBox(spec.label)
                box.setLayout(QVBoxLayout())
                if spec.tooltip:
                    hint = QLabel(spec.tooltip)
                    hint.setWordWrap(True)
                    hint.setStyleSheet("color:#5b666e; font-size:11px;")
                    box.layout().addWidget(hint)
                self.choice_boxes[spec.name] = box
                self.choice_checks[spec.name] = {}
                continue
            widget = self._make_widget(spec, options.get(spec.name))
            if spec.tooltip:
                widget.setToolTip(spec.tooltip)
            self.widgets[spec.name] = widget
            form.addRow(spec.label + (f" [{spec.unit}]" if spec.unit else ""), widget)
        page.addLayout(form)
        for name, box in self.choice_boxes.items():
            page.addWidget(box)
        page.addStretch(1)
        self._selected = {name: set(options.get(name) or ())
                          for name in self.choice_boxes}

        self.tabs.addTab(options_page, "Options")
        variant = len(case.exclude) + len(case.overrides)
        self.tabs.addTab(self._build_variant(case),
                         "Network variant" + (f" ({variant})" if variant else ""))
        layout.addWidget(self.tabs)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color:#b42318;")
        self.error.setVisible(False)
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for widget in self.widgets.values():
            signal = getattr(widget, "currentIndexChanged", None)
            if signal is not None:
                signal.connect(self._refresh_choices)
        self._refresh_choices()

    # -- option widgets ------------------------------------------------- #

    def _make_widget(self, spec, value) -> QWidget:
        net = self.document.network
        if spec.kind in ("bus", "element"):
            combo = QComboBox()
            if getattr(spec, "optional", False):
                combo.addItem("(none)", "")
            if spec.kind == "bus":
                for bus_id, label in self.document.bus_choices():
                    combo.addItem(label, bus_id)
            else:
                types = getattr(spec, "element_types", ())
                for comp in net.components.values():
                    if not types or comp.TYPE in types:
                        combo.addItem(f"{display_name(comp)} ({comp.TYPE})", comp.id)
            combo.setCurrentIndex(max(0, combo.findData(value or "")))
            return combo
        if spec.kind == "choice":
            combo = QComboBox()
            for choice in spec.choices:
                combo.addItem(choice, choice)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            return combo
        if spec.kind == "bool":
            check = QCheckBox()
            check.setChecked(bool(value))
            return check
        edit = QLineEdit("" if value is None else str(value))
        return edit

    def _value(self, spec, widget):
        if isinstance(widget, QComboBox):
            return widget.currentData() or ""
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        text = widget.text().strip()
        if spec.kind == "str" or text == "":
            return text
        if spec.kind == "complex":
            text = re.sub(r"(?<=[0-9.])i\b", "j", text)    # MATLAB's 0.3i
        number = safe_eval(text, self.document.params.values(), target=spec.label)
        if spec.kind == "int":
            return int(round(float(number.real if isinstance(number, complex) else number)))
        if spec.kind == "complex":
            return complex(number)
        return float(number.real if isinstance(number, complex) else number)

    def _current_options(self) -> dict:
        out = {}
        for spec in self.study.option_fields:
            if spec.name in self.widgets:
                widget = self.widgets[spec.name]
                if isinstance(widget, QComboBox) or isinstance(widget, QCheckBox):
                    out[spec.name] = self._value(spec, widget)
        return out

    def _refresh_choices(self) -> None:
        net = self.document.network
        current = self._current_options()
        for name, box in self.choice_boxes.items():
            for check in self.choice_checks[name].values():
                self._selected[name].discard(check.property("value"))
                if check.isChecked():
                    self._selected[name].add(check.property("value"))
                box.layout().removeWidget(check)
                check.deleteLater()
            self.choice_checks[name].clear()
            choices = (self.study.dynamic_choices(net, current, name)
                       if self.study.dynamic_choices else [])
            for choice in choices:
                check = QCheckBox(choice.label)
                check.setProperty("value", choice.value)
                check.setEnabled(choice.enabled)
                check.setChecked(choice.enabled and choice.value in self._selected[name])
                if choice.tooltip:
                    check.setToolTip(choice.tooltip)
                box.layout().addWidget(check)
                self.choice_checks[name][choice.value] = check
            box.setVisible(bool(choices))

    # -- network variant ------------------------------------------------ #

    def _build_variant(self, case: Case) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        hint = QLabel(
            "Take elements out of service, or override a field with an "
            "expression (the project variables and value, the field's own "
            "value, are available: 0.5*value halves it). The drawn network is "
            "not changed.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5b666e; font-size:11px;")
        layout.addWidget(hint)

        layout.addWidget(QLabel("Out of service"))
        self.exclude_list = QListWidget()
        self.exclude_list.setFixedHeight(160)
        net = self.document.network
        for obj in list(net.buses.values()) + list(net.components.values()):
            kind = "bus" if obj.id in net.buses else obj.TYPE
            item = QListWidgetItem(f"{display_name(obj)} ({kind})")
            item.setData(Qt.ItemDataRole.UserRole, obj.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if obj.id in case.exclude
                               else Qt.CheckState.Unchecked)
            self.exclude_list.addItem(item)
        layout.addWidget(self.exclude_list)

        layout.addWidget(QLabel("Overrides"))
        self.override_table = QTableWidget(0, 2)
        self.override_table.setHorizontalHeaderLabels(["Component.field", "Expression"])
        self.override_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.override_table.verticalHeader().setVisible(False)
        self.override_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.override_table.setColumnWidth(0, 170)
        self.override_table.setFixedHeight(110)
        for key, expr in case.overrides.items():
            self._add_override_row(key, expr)
        layout.addWidget(self.override_table)
        row = QHBoxLayout()
        add = QPushButton("Add override")
        add.clicked.connect(lambda: self._add_override_row("", ""))
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_override_row)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _add_override_row(self, key: str, expr: str) -> None:
        r = self.override_table.rowCount()
        self.override_table.insertRow(r)
        self.override_table.setItem(r, 0, QTableWidgetItem(key))
        self.override_table.setItem(r, 1, QTableWidgetItem(expr))

    def _remove_override_row(self) -> None:
        rows = sorted({i.row() for i in self.override_table.selectedIndexes()},
                      reverse=True)
        for r in rows:
            self.override_table.removeRow(r)

    def _overrides(self) -> dict[str, str]:
        out = {}
        for r in range(self.override_table.rowCount()):
            key = (self.override_table.item(r, 0) or QTableWidgetItem("")).text().strip()
            expr = (self.override_table.item(r, 1) or QTableWidgetItem("")).text().strip()
            if key or expr:
                out[key] = expr
        return out

    def _check_overrides(self, overrides: dict[str, str]) -> None:
        net = self.document.network
        names = self.document.params.values()
        for key, expr in overrides.items():
            comp_id, _, field_name = key.partition(".")
            if comp_id not in net or not hasattr(net.get(comp_id), field_name):
                raise ExpressionError(
                    f"Override '{key}': write it as COMPONENT_ID.field, e.g. "
                    f"LD4.p_mw.", expr, key)
            safe_eval(expr, {**names, "value": getattr(net.get(comp_id), field_name)},
                      target=key)

    # -- result --------------------------------------------------------- #

    def _accept(self) -> None:
        try:
            self._result = self._build_case()
        except (ExpressionError, ValueError, TypeError) as exc:
            self.error.setText(str(exc))
            self.error.setVisible(True)
            return
        self.accept()

    def _build_case(self) -> Case:
        options = {}
        for spec in self.study.option_fields:
            if spec.name in self.widgets:
                options[spec.name] = self._value(spec, self.widgets[spec.name])
            elif spec.name in self.choice_checks:
                picked = [v for v, check in self.choice_checks[spec.name].items()
                          if check.isChecked() and check.isEnabled()]
                if picked:
                    options[spec.name] = picked
        overrides = self._overrides()
        self._check_overrides(overrides)
        exclude = []
        for i in range(self.exclude_list.count()):
            item = self.exclude_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                exclude.append(item.data(Qt.ItemDataRole.UserRole))
        return Case(id=self.base_case.id, study=self.study.key,
                    title=self.title_edit.text().strip(), options=options,
                    overrides=overrides, exclude=exclude)

    def case(self) -> Case:
        """The edited case (call after the dialog was accepted)."""
        return getattr(self, "_result", None) or self._build_case()
