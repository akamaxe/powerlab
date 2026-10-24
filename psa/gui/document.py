"""The editing document: the project, the undo stack and the change signals.

**Nothing outside :mod:`psa.gui.commands` mutates the network.** Every edit goes
through a :class:`~PySide6.QtGui.QUndoCommand` that calls one of the private
``_apply_*`` methods here. That single rule is what makes undo/redo nearly free
and keeps the results-are-stale bookkeeping in one place.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoStack

from ..core.components import TYPE_REGISTRY, buses_of
from ..core.errors import Issue
from ..core.network import Network
from ..core.params import Parameters
from ..core.serialize import Project, auto_layout, load, save


@dataclass
class Selection:
    """What the property editor is currently bound to."""

    obj_id: str | None = None


class NetworkDocument(QObject):
    """Owns the project and broadcasts every change."""

    structureChanged = Signal()             # objects added or removed
    componentChanged = Signal(str)          # one object's data changed
    connectivityChanged = Signal(str)       # a terminal was re-attached
    layoutChanged = Signal(str)             # an item moved or resized
    wireChanged = Signal(str)               # a wire's bends, by "COMP:terminal"
    resultsInvalidated = Signal()
    dirtyChanged = Signal(bool)
    parametersChanged = Signal()
    casesChanged = Signal()

    def __init__(self, project: Project | None = None, parent=None):
        super().__init__(parent)
        self.project = project or Project()
        self.undo = QUndoStack(self)
        self.undo.cleanChanged.connect(self._on_clean_changed)
        self._results_valid = False
        self._next_id = 1

    def _on_clean_changed(self, clean: bool) -> None:
        """Re-broadcast the undo stack's clean flag as "dirty".

        A bound slot rather than a lambda on purpose: a closure capturing
        ``self`` makes a document -> undo stack -> closure -> document cycle,
        and breaking that cycle in the garbage collector emits signals on a
        half-destroyed C++ object.
        """
        self.dirtyChanged.emit(not clean)

    # ------------------------------------------------------------------ #
    # Convenience accessors
    # ------------------------------------------------------------------ #

    @property
    def network(self) -> Network:
        return self.project.network

    @property
    def params(self) -> Parameters:
        return self.project.params

    @property
    def layout(self) -> dict:
        return self.project.layout

    @property
    def path(self) -> str | None:
        return self.project.path

    @property
    def is_dirty(self) -> bool:
        return not self.undo.isClean()

    @property
    def results_valid(self) -> bool:
        return self._results_valid

    def title(self) -> str:
        if self.project.path:
            return Path(self.project.path).name
        return self.project.meta.get("title") or "Untitled"

    # ------------------------------------------------------------------ #
    # File operations
    # ------------------------------------------------------------------ #

    @classmethod
    def open(cls, path: str | Path, as_copy: bool = False) -> "NetworkDocument":
        """Open a project. The linked variable script is not run here.

        ``as_copy`` opens it untitled, so saving asks for a new path instead of
        overwriting the original (used for the bundled example).
        """
        proj = load(path)
        proj.ensure_layout()
        if as_copy:
            proj.path = None
        doc = cls(proj)
        doc.undo.setClean()
        return doc

    def save_to(self, path: str | Path) -> Path:
        self.project.ensure_layout()
        p = save(self.project, path)
        self.undo.setClean()
        return p

    # ------------------------------------------------------------------ #
    # Ids
    # ------------------------------------------------------------------ #

    def new_id(self, type_name: str) -> str:
        prefix = {"bus": "bus", "line": "LI", "transformer": "T",
                  "load": "LD", "shunt": "SH", "source": "SRC",
                  "impedance": "Z"}.get(type_name, type_name[:3].upper())
        n = 1
        while True:
            candidate = f"{prefix}{n}"
            n += 1
            if candidate not in self.network:
                return candidate

    def new_name(self, type_name: str, obj_id: str) -> str:
        if type_name == "bus":
            used = {b.name for b in self.network.buses.values()}
            for i in range(1, 500):
                cand = _bus_letter(i)
                if cand not in used:
                    return cand
        return obj_id

    # ------------------------------------------------------------------ #
    # Results lifecycle
    # ------------------------------------------------------------------ #

    def invalidate_results(self) -> None:
        """Anything that changes the model makes the displayed numbers stale."""
        if self._results_valid:
            self._results_valid = False
        self.resultsInvalidated.emit()

    def mark_results_valid(self) -> None:
        self._results_valid = True

    # ------------------------------------------------------------------ #
    # Mutations. Called ONLY from psa.gui.commands.
    # ------------------------------------------------------------------ #

    def _apply_add(self, obj, layout: dict | None = None) -> None:
        self.network.add(obj)
        self.project.layout[obj.id] = dict(layout or {"x": 0, "y": 0})
        self.structureChanged.emit()
        self.invalidate_results()

    def _apply_remove(self, obj_id: str) -> tuple[object, dict]:
        obj = self.network.remove(obj_id)
        layout = self.project.layout.pop(obj_id, {})
        self.params.drop_component(obj_id)
        self.project.wires = {k: v for k, v in self.project.wires.items()
                              if not k.startswith(f"{obj_id}:")}
        self.structureChanged.emit()
        self.invalidate_results()
        return obj, layout

    def _apply_set_field(self, obj_id: str, field: str, value) -> None:
        obj = self.network.get(obj_id)
        setattr(obj, field, value)
        if field in getattr(obj, "BUS_FIELDS", ()):
            self.connectivityChanged.emit(obj_id)
        else:
            self.componentChanged.emit(obj_id)
        self.invalidate_results()

    def _apply_set_binding(self, obj_id: str, field: str, expr: str | None) -> None:
        if expr:
            self.params.bind(obj_id, field, expr)
        else:
            self.params.unbind(obj_id, field)
        self.parametersChanged.emit()
        self.invalidate_results()

    def _apply_layout(self, obj_id: str, layout: dict) -> None:
        self.project.layout[obj_id] = dict(layout)
        self.layoutChanged.emit(obj_id)

    def _apply_wire(self, key: str, waypoints: list) -> None:
        if waypoints:
            self.project.wires[key] = [list(p) for p in waypoints]
        else:
            self.project.wires.pop(key, None)
        self.wireChanged.emit(key)

    def _apply_network_settings(self, s_base: float, f_hz: float,
                                ref_bus: str, ref_u: float) -> None:
        net = self.network
        net.s_base_mva = s_base
        net.f_hz = f_hz
        net.base_ref_bus = ref_bus
        net.base_ref_u_kv = ref_u
        self.structureChanged.emit()
        self.invalidate_results()

    def _apply_parameters(self, script_path, project_vars, bindings) -> None:
        self.params.script_path = script_path
        if not script_path:
            self.params.script_vars = {}
        self.params.project_vars = list(project_vars)
        self.params.bindings = dict(bindings)
        # Re-evaluate here, inside the command, so undo and redo move the
        # bound field values together with the variables.
        self.params.apply(self.network)
        self.parametersChanged.emit()
        self.structureChanged.emit()
        self.invalidate_results()

    def _apply_cases(self, cases: list) -> None:
        self.project.cases = copy.deepcopy(list(cases))
        self.casesChanged.emit()

    # ------------------------------------------------------------------ #
    # Parameter evaluation
    # ------------------------------------------------------------------ #

    def apply_parameters(self) -> list[Issue]:
        """Evaluate the bindings into the network and report any problems.

        Uses the variables the linked script produced last time; it never runs
        the script (that is :meth:`reload_script`, an explicit user action).
        """
        issues = self.project.apply_parameters()
        self.structureChanged.emit()
        self.invalidate_results()
        return issues

    def reload_script(self) -> list[Issue]:
        base = Path(self.project.path).parent if self.project.path else None
        issues = self.params.reload_script(base)
        issues += self.params.apply(self.network)
        self.parametersChanged.emit()
        self.structureChanged.emit()
        self.invalidate_results()
        return issues

    # ------------------------------------------------------------------ #
    # Queries used by the views
    # ------------------------------------------------------------------ #

    def snapshot(self, obj_id: str) -> dict:
        obj = self.network.get(obj_id)
        return {f: copy.copy(getattr(obj, f)) for f in obj.__dataclass_fields__}

    def dependents_of(self, bus_id: str) -> list:
        return self.network.dependents_of(bus_id)

    def bus_choices(self) -> list[tuple[str, str]]:
        """``(id, label)`` for every bus, for the combo boxes."""
        return [(b.id, b.name or b.id) for b in self.network.buses.values()]

    def case(self, case_id: str):
        """The project's case with this id, or ``None``."""
        return next((c for c in self.project.cases if c.id == case_id), None)

    def ensure_layout(self) -> None:
        missing = [o.id for o in self.network if o.id not in self.project.layout]
        if missing:
            self.project.layout.update(
                auto_layout(self.network, skip=set(self.project.layout)))


def _bus_letter(n: int) -> str:
    """1 -> 'a', 26 -> 'z', 27 -> 'aa'."""
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("a") + rem) + out
    return out


def blank_document() -> NetworkDocument:
    """A new, empty project with sensible defaults."""
    net = Network(s_base_mva=10.0, f_hz=50.0)
    proj = Project(network=net, meta={"title": "Untitled"})
    doc = NetworkDocument(proj)
    doc.undo.setClean()
    return doc
