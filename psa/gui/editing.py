"""Typed text into field edits, shared by the property editor and the data manager.

A numeric field takes a number, or (when parametric) an expression over the
project variables, which becomes a binding; an empty text clears the binding
and, for a field whose default is ``None`` ("use the bus base", "no limit"),
the value too. Every edit is one undo step, whatever it touches.
"""

from __future__ import annotations

import dataclasses

from ..core.components import FieldSpec
from ..core.errors import ExpressionError
from ..core.params import safe_eval
from .commands import SetBinding, SetField


class FieldInputError(ValueError):
    """The text cannot go into the field; the message says why."""


def num_text(value) -> str:
    """A field value the way an editor shows it."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, complex):
        return f"{value.real:g}{value.imag:+g}j" if value.imag else f"{value.real:g}"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def try_float(text: str):
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def try_complex(text: str):
    """A literal like ``1.6+0.3j`` (or MATLAB's ``1.6+0.3i``), else ``None``."""
    compact = text.replace(" ", "").replace(",", ".")
    if compact.endswith("i"):
        compact = compact[:-1] + "j"
    try:
        return complex(compact)
    except ValueError:
        return None


def coerce_numeric(spec: FieldSpec, value):
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


def is_optional(obj, field: str) -> bool:
    """Whether the field's default is ``None``, so an empty text means "none"."""
    for f in dataclasses.fields(obj):
        if f.name == field:
            return f.default is None
    return False


def parse_numeric(doc, spec: FieldSpec, text: str):
    """``(value, expression)`` for a non-empty text; raises :class:`FieldInputError`."""
    text = text.strip()
    plain = try_complex(text) if spec.kind == "complex" else try_float(text)
    expr = None
    try:
        if plain is not None:
            value = coerce_numeric(spec, plain)
        elif spec.parametric:
            value = coerce_numeric(spec, safe_eval(text, doc.params.values(), target=spec.name))
            expr = text
        else:
            raise FieldInputError(f"{spec.label} takes a number.")
    except (ExpressionError, TypeError, ValueError, OverflowError) as exc:
        raise FieldInputError(str(exc)) from exc
    if not isinstance(value, complex):
        if spec.minimum is not None and value < spec.minimum:
            raise FieldInputError(f"{spec.label} must be at least {spec.minimum:g}.")
        if spec.maximum is not None and value > spec.maximum:
            raise FieldInputError(f"{spec.label} must be at most {spec.maximum:g}.")
    return value, expr


def apply_numeric(doc, obj_ids: list[str], spec: FieldSpec, value, expr) -> bool:
    """Set value and binding on every object, in one undo step; False if nothing changed."""
    net = doc.network
    changes = []
    for obj_id in obj_ids:
        binding_changes = doc.params.expression_for(obj_id, spec.name) != expr
        value_changes = getattr(net.get(obj_id), spec.name) != value
        if binding_changes or value_changes:
            changes.append((obj_id, binding_changes, value_changes))
    if not changes:
        return False
    doc.undo.beginMacro(f"Set {spec.label}")
    try:
        for obj_id, binding_changes, value_changes in changes:
            if binding_changes:
                doc.undo.push(SetBinding(doc, obj_id, spec.name, expr))
            if value_changes:
                doc.undo.push(SetField(doc, obj_id, spec.name, value, spec.label))
    finally:
        doc.undo.endMacro()
    return True


def clear_numeric(doc, obj_ids: list[str], spec: FieldSpec) -> bool:
    """Drop the bindings and, for an optional field, the value; False if nothing changed."""
    net = doc.network
    todo = []
    for obj_id in obj_ids:
        obj = net.get(obj_id)
        unbind = bool(doc.params.expression_for(obj_id, spec.name))
        reset = getattr(obj, spec.name) is not None and is_optional(obj, spec.name)
        if unbind or reset:
            todo.append((obj_id, unbind, reset))
    if not todo:
        return False
    doc.undo.beginMacro(f"Clear {spec.label}")
    try:
        for obj_id, unbind, reset in todo:
            if unbind:
                doc.undo.push(SetBinding(doc, obj_id, spec.name, None))
            if reset:
                doc.undo.push(SetField(doc, obj_id, spec.name, None))
    finally:
        doc.undo.endMacro()
    return True


def set_value(doc, obj_ids: list[str], spec: FieldSpec, value) -> bool:
    """A plain (choice, bool, text) value on every object that differs, in one step."""
    net = doc.network
    targets = [i for i in obj_ids if i in net and getattr(net.get(i), spec.name) != value]
    if not targets:
        return False
    doc.undo.beginMacro(f"Set {spec.label}")
    try:
        for obj_id in targets:
            doc.undo.push(SetField(doc, obj_id, spec.name, value, spec.label))
    finally:
        doc.undo.endMacro()
    return True
