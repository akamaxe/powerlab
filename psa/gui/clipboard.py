"""Copy, cut, paste and duplicate of diagram elements.

What is copied is a JSON payload: the objects (buses and components, with
every field), their layout and their parametric bindings. Pasting gives every
object a fresh id. Connections among the copied objects are kept; a
connection to a bus that was not copied is dropped, so pasting never wires a
new element in parallel with an existing one by accident.
"""

from __future__ import annotations

import json
from dataclasses import fields as dc_fields

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QGuiApplication

from ..core.components import TYPE_REGISTRY, Bus
from ..core.serialize import _build, _enc
from .commands import AddComponent, SetBinding

MIME = "application/x-powerlab-elements"
FORMAT = "powerlab-elements"


def copy_payload(document, ids: list[str]) -> dict:
    """The selected objects, their layout and their bindings, as plain data."""
    net = document.network
    ids = [i for i in ids if i in net]
    objects = []
    for obj_id in ids:
        obj = net.get(obj_id)
        raw = {"type": obj.TYPE}
        raw.update({f.name: _enc(getattr(obj, f.name)) for f in dc_fields(obj)})
        objects.append(raw)
    prefixes = tuple(f"{i}." for i in ids)
    return {
        "format": FORMAT,
        "objects": objects,
        "layout": {i: dict(document.project.layout.get(i, {})) for i in ids},
        "bindings": {k: v for k, v in document.params.bindings.items()
                     if k.startswith(prefixes)},
    }


def to_clipboard(payload: dict) -> None:
    text = json.dumps(payload)
    mime = QMimeData()
    mime.setData(MIME, text.encode("utf-8"))
    mime.setText(text)
    QGuiApplication.clipboard().setMimeData(mime)


def from_clipboard() -> dict | None:
    mime = QGuiApplication.clipboard().mimeData()
    if mime is None or not mime.hasFormat(MIME):
        return None
    try:
        payload = json.loads(bytes(mime.data(MIME)).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if payload.get("format") == FORMAT else None


def paste_payload(document, payload: dict, offset: float = 40.0) -> list[str]:
    """Add the payload's objects with fresh ids, in one undo step; returns the ids."""
    net = document.network
    taken: set[str] = set()

    def fresh_id(type_name: str) -> str:
        candidate = document.new_id(type_name)
        n = 1
        while candidate in taken or candidate in net:
            n += 1
            candidate = f"{candidate.rstrip('0123456789')}{n}"
        taken.add(candidate)
        return candidate

    objects = payload.get("objects") or []
    # Buses first, so components can be remapped onto the new bus ids.
    ordered = sorted(objects, key=lambda raw: raw.get("type") != "bus")
    id_map: dict[str, str] = {}
    built = []
    for raw in ordered:
        cls = TYPE_REGISTRY.get(raw.get("type"))
        if cls is None or "id" not in raw:
            continue
        new_id = fresh_id(cls.TYPE)
        id_map[raw["id"]] = new_id
        built.append((cls, raw, new_id))

    new_ids: list[str] = []
    document.undo.beginMacro(f"Paste {len(built)} object(s)")
    try:
        for cls, raw, new_id in built:
            obj = _build(cls, {**raw, "id": new_id})
            if isinstance(obj, Bus):
                obj.name = document.new_name("bus", new_id)
            else:
                obj.name = new_id
                for terminal in cls.TERMINALS:
                    old = getattr(obj, terminal.field, None)
                    mapped = id_map.get(old) if old else None
                    setattr(obj, terminal.field,
                            mapped if mapped else (None if terminal.optional else ""))
            layout = dict((payload.get("layout") or {}).get(raw["id"], {}))
            layout["x"] = float(layout.get("x", 0.0)) + offset
            layout["y"] = float(layout.get("y", 0.0)) + offset
            document.undo.push(AddComponent(document, obj, layout))
            new_ids.append(new_id)
        for key, expr in (payload.get("bindings") or {}).items():
            old_id, _, field_name = key.partition(".")
            if old_id in id_map:
                document.undo.push(SetBinding(document, id_map[old_id], field_name, expr))
    finally:
        document.undo.endMacro()
    return new_ids
