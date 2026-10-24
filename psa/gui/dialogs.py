"""Dialogs: base settings, study options and the port-exclusion editor."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
)

from ..core.components import buses_of, display_name, is_node_shunt
from ..core.components import Source


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

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[float, float, str, float]:
        return (self.s_base.value(), self.f_hz.value(),
                self.ref_bus.currentData() or "", self.ref_u.value())


class StudyOptionsDialog(QDialog):
    """Options for one study, generated from its ``option_fields``."""

    def __init__(self, document, study, defaults: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(study.title)
        self.document = document
        self.study = study
        defaults = defaults or {}

        layout = QVBoxLayout(self)
        if study.description:
            desc = QLabel(study.description)
            desc.setWordWrap(True)
            desc.setStyleSheet("color:#5b666e;")
            layout.addWidget(desc)

        if study.key == "s1a":
            conv = QLabel("Convention:  [U₁; I₁] = F · [U₂; I₂]"
                          "\nPort 1 is the source side, port 2 the load side. "
                          "Swapping them gives a different matrix.")
            conv.setWordWrap(True)
            conv.setStyleSheet(
                "background:#eef4fb; border:1px solid #c6d9ee; border-radius:3px;"
                "padding:6px; color:#194a7a; font-size:11px;")
            layout.addWidget(conv)

        form = QFormLayout()
        self.widgets: dict[str, QComboBox] = {}
        for spec in study.option_fields:
            combo = QComboBox()
            if spec.kind == "bus":
                if spec.name == "far_bus":
                    combo.addItem("(none)", "")
                for bus_id, label in document.bus_choices():
                    combo.addItem(label, bus_id)
                index = combo.findData(defaults.get(spec.name, ""))
                combo.setCurrentIndex(max(0, index))
            if spec.tooltip:
                combo.setToolTip(spec.tooltip)
            self.widgets[spec.name] = combo
            form.addRow(spec.label, combo)
        layout.addLayout(form)

        self.excluded_box: QGroupBox | None = None
        self.exclusion_checks: dict[str, QCheckBox] = {}
        if study.key in ("s1a", "s1b"):
            self._build_exclusions(layout, defaults)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for combo in self.widgets.values():
            combo.currentIndexChanged.connect(self._refresh_exclusions)
        self._refresh_exclusions()

    # -- port exclusions ------------------------------------------------ #

    def _build_exclusions(self, layout, defaults) -> None:
        self.excluded_box = QGroupBox("Shunts at the ports")
        box = QVBoxLayout(self.excluded_box)
        note = QLabel(
            "A load or source sitting on a port bus is the termination, not "
            "part of the two-port, so it is excluded. Tick one to include it "
            "anyway. Line charging is always kept.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#5b666e; font-size:11px;")
        box.addWidget(note)
        self._exclusion_host = box
        layout.addWidget(self.excluded_box)

    def _ports(self) -> tuple[str, str]:
        if self.study.key == "s1a":
            return (self.widgets["bus_i"].currentData() or "",
                    self.widgets["bus_j"].currentData() or "")
        return (self.widgets["bus"].currentData() or "",
                self.widgets.get("far_bus").currentData() or ""
                if "far_bus" in self.widgets else "")

    def _refresh_exclusions(self) -> None:
        if self.excluded_box is None:
            return
        for check in self.exclusion_checks.values():
            self._exclusion_host.removeWidget(check)
            check.deleteLater()
        self.exclusion_checks.clear()

        bus_i, bus_j = self._ports()
        ports = {b for b in (bus_i, bus_j) if b}
        net = self.document.network
        found = False
        for comp in net.active_components():
            if not is_node_shunt(comp):
                continue
            bs = buses_of(comp)
            if not bs or bs[0] not in ports:
                continue
            found = True
            ideal_source = isinstance(comp, Source) and comp.is_ideal
            check = QCheckBox(
                f"Include {display_name(comp)} ({comp.TYPE}) at bus "
                f"{display_name(net.buses[bs[0]])}")
            check.setProperty("comp_id", comp.id)
            if ideal_source:
                check.setEnabled(False)
                check.setToolTip(
                    "An ideal voltage source at a port is a short circuit "
                    "across it, so it can never be included.")
            self.exclusion_checks[comp.id] = check
            self._exclusion_host.addWidget(check)
        self.excluded_box.setVisible(found)

    # -- result --------------------------------------------------------- #

    def options(self) -> dict:
        out = {name: (w.currentData() or "") for name, w in self.widgets.items()}
        includes = [cid for cid, check in self.exclusion_checks.items()
                    if check.isChecked() and check.isEnabled()]
        if includes:
            out["include_at_ports"] = includes
        return out
