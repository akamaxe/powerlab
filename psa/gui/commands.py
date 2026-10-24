"""Undo commands. The only code allowed to mutate the network.

Deletion keeps the removed dataclass alive on the command, so undo can put back
the very same object. The corresponding graphics item is likewise kept alive by
the scene rather than destroyed, which is what stops the classic Qt binding crash
where undo dereferences an already-deleted C++ object.
"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtGui import QUndoCommand

from ..core.components import buses_of


class AddComponent(QUndoCommand):
    def __init__(self, doc, obj, layout: dict | None = None, text: str = ""):
        super().__init__(text or f"Add {obj.TYPE} {obj.name or obj.id}")
        self.doc = doc
        self.obj = obj
        self.layout = dict(layout or {})

    def redo(self):
        self.doc._apply_add(self.obj, self.layout)

    def undo(self):
        _, self.layout = self.doc._apply_remove(self.obj.id)


class DeleteComponent(QUndoCommand):
    """Delete one object, remembering it and everything hanging off it."""

    def __init__(self, doc, obj_id: str, text: str = ""):
        obj = doc.network.get(obj_id)
        super().__init__(text or f"Delete {obj.name or obj.id}")
        self.doc = doc
        self.obj_id = obj_id
        self.obj = obj
        self.layout = dict(doc.project.layout.get(obj_id, {}))
        self.bindings = {k: v for k, v in doc.params.bindings.items()
                         if k.startswith(f"{obj_id}.")}

    def redo(self):
        self.obj, self.layout = self.doc._apply_remove(self.obj_id)

    def undo(self):
        self.doc._apply_add(self.obj, self.layout)
        self.doc.params.bindings.update(self.bindings)


class SetField(QUndoCommand):
    """Change one component attribute."""

    def __init__(self, doc, obj_id: str, field: str, value: Any,
                 label: str | None = None):
        obj = doc.network.get(obj_id)
        name = obj.name or obj.id
        super().__init__(f"Change {label or field} of {name}")
        self.doc = doc
        self.obj_id = obj_id
        self.field = field
        self.new = value
        self.old = copy.copy(getattr(obj, field))

    def redo(self):
        self.doc._apply_set_field(self.obj_id, self.field, self.new)

    def undo(self):
        self.doc._apply_set_field(self.obj_id, self.field, self.old)

    def id(self):  # noqa: A003 - Qt's merge id
        return 1001

    def mergeWith(self, other):
        """Collapse repeated edits of the same field into one undo step."""
        if (isinstance(other, SetField) and other.obj_id == self.obj_id
                and other.field == self.field):
            self.new = other.new
            return True
        return False


class SetBinding(QUndoCommand):
    """Attach or clear a parametric expression on a field."""

    def __init__(self, doc, obj_id: str, field: str, expr: str | None):
        super().__init__(f"Bind {obj_id}.{field}" if expr
                         else f"Unbind {obj_id}.{field}")
        self.doc = doc
        self.obj_id = obj_id
        self.field = field
        self.new = expr
        self.old = doc.params.expression_for(obj_id, field)

    def redo(self):
        self.doc._apply_set_binding(self.obj_id, self.field, self.new)

    def undo(self):
        self.doc._apply_set_binding(self.obj_id, self.field, self.old)


class Connect(QUndoCommand):
    """Attach one terminal of a component to a bus."""

    def __init__(self, doc, comp_id: str, field: str, bus_id: str | None):
        comp = doc.network.get(comp_id)
        bus_name = (doc.network.buses[bus_id].name or bus_id) if bus_id else "nothing"
        super().__init__(f"Connect {comp.name or comp_id} to {bus_name}")
        self.doc = doc
        self.comp_id = comp_id
        self.field = field
        self.new = bus_id
        self.old = getattr(comp, field)

    def redo(self):
        self.doc._apply_set_field(self.comp_id, self.field, self.new)

    def undo(self):
        self.doc._apply_set_field(self.comp_id, self.field, self.old)


class MoveItems(QUndoCommand):
    """One command for a whole drag, however many items it moved."""

    def __init__(self, doc, moves: dict[str, tuple[dict, dict]]):
        super().__init__("Move item" if len(moves) == 1
                         else f"Move {len(moves)} items")
        self.doc = doc
        self.moves = moves          # obj_id -> (old_layout, new_layout)

    def redo(self):
        for obj_id, (_, new) in self.moves.items():
            self.doc._apply_layout(obj_id, new)

    def undo(self):
        for obj_id, (old, _) in self.moves.items():
            self.doc._apply_layout(obj_id, old)


class SetLayout(QUndoCommand):
    """Change one item's geometry (bus length, orientation, rotation)."""

    def __init__(self, doc, obj_id: str, layout: dict, text: str = "Resize"):
        super().__init__(text)
        self.doc = doc
        self.obj_id = obj_id
        self.new = dict(layout)
        self.old = dict(doc.project.layout.get(obj_id, {}))

    def redo(self):
        self.doc._apply_layout(self.obj_id, self.new)

    def undo(self):
        self.doc._apply_layout(self.obj_id, self.old)


class SetWire(QUndoCommand):
    """Change the bends of one wire (``[]`` restores the automatic route)."""

    def __init__(self, doc, key: str, waypoints: list[list[float]],
                 old: list[list[float]] | None = None, text: str = "Edit wire"):
        super().__init__(text)
        self.doc = doc
        self.key = key
        self.new = [list(p) for p in waypoints]
        self.old = ([list(p) for p in old] if old is not None
                    else [list(p) for p in doc.project.wires.get(key, [])])

    def redo(self):
        self.doc._apply_wire(self.key, self.new)

    def undo(self):
        self.doc._apply_wire(self.key, self.old)


class SetNetworkSettings(QUndoCommand):
    def __init__(self, doc, s_base: float, f_hz: float, ref_bus: str, ref_u: float):
        super().__init__("Change base settings")
        self.doc = doc
        self.new = (s_base, f_hz, ref_bus, ref_u)
        net = doc.network
        self.old = (net.s_base_mva, net.f_hz, net.base_ref_bus, net.base_ref_u_kv)

    def redo(self):
        self.doc._apply_network_settings(*self.new)

    def undo(self):
        self.doc._apply_network_settings(*self.old)


class SetParameters(QUndoCommand):
    """Replace the whole variable set (used by the Variables panel)."""

    def __init__(self, doc, script_path, project_vars, bindings):
        super().__init__("Change variables")
        self.doc = doc
        self.new = (script_path, list(project_vars), dict(bindings))
        p = doc.params
        self.old = (p.script_path, list(p.project_vars), dict(p.bindings))

    def redo(self):
        self.doc._apply_parameters(*self.new)

    def undo(self):
        self.doc._apply_parameters(*self.old)


class SetCases(QUndoCommand):
    """Replace the project's study cases (they are saved with the project)."""

    def __init__(self, doc, cases: list, text: str = "Change study cases"):
        super().__init__(text)
        self.doc = doc
        self.new = copy.deepcopy(list(cases))
        self.old = copy.deepcopy(list(doc.project.cases))

    def redo(self):
        self.doc._apply_cases(self.new)

    def undo(self):
        self.doc._apply_cases(self.old)


# --------------------------------------------------------------------------- #
# Macros
# --------------------------------------------------------------------------- #


def delete_objects(doc, obj_ids: list[str]) -> None:
    """Delete objects and, for a bus, everything attached to it.

    A component must never be left pointing at a bus that no longer exists, so
    dependents are deleted in the same undo step.
    """
    targets: list[str] = []
    for obj_id in obj_ids:
        if obj_id in doc.network.buses:
            for dep in doc.network.dependents_of(obj_id):
                if dep.id not in targets:
                    targets.append(dep.id)
        if obj_id not in targets:
            targets.append(obj_id)

    if not targets:
        return
    label = (f"Delete {len(targets)} objects" if len(targets) > 1
             else f"Delete {targets[0]}")
    doc.undo.beginMacro(label)
    # Components first, then buses, so no intermediate state dangles.
    for obj_id in [t for t in targets if t not in doc.network.buses]:
        doc.undo.push(DeleteComponent(doc, obj_id))
    for obj_id in [t for t in targets if t in doc.network.buses]:
        doc.undo.push(DeleteComponent(doc, obj_id))
    doc.undo.endMacro()


def add_component_connected(doc, obj, layout: dict, connections: dict[str, str]
                            ) -> None:
    """Add a component and wire its terminals in one undo step."""
    doc.undo.beginMacro(f"Add {obj.TYPE} {obj.name or obj.id}")
    doc.undo.push(AddComponent(doc, obj, layout))
    for field, bus_id in connections.items():
        if bus_id:
            doc.undo.push(Connect(doc, obj.id, field, bus_id))
    doc.undo.endMacro()
