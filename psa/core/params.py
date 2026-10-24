"""Parametric values: project variables, a user Python script, and field bindings.

The assignment states its data algebraically ("length: 217 + A metres",
"P = (117 - A) kW"), so the app lets you write exactly that. Define ``A`` once
and every bound field follows.

Design
------
Component dataclasses stay plain floats. Expressions live here, in a section
parallel to the network, exactly like the drawing layout does. That keeps the
physics core clean and makes the project file easy to diff.

Three layers, evaluated in order, each able to use the names defined before it:

1. ``script_vars``  -- numeric globals pulled out of the user's own ``.py`` file
2. ``project_vars`` -- ordered ``name = expression`` pairs edited in the GUI
3. ``bindings``     -- ``"COMP.field" = expression``, written into the network

Safety
------
Inline expressions go through :func:`safe_eval`, an AST whitelist with no
attribute access, no subscripting and no lambdas. The *script* is different: it
is ordinary Python and runs with full privileges, so loading one is always an
explicit, visible action -- never a silent side effect of opening a project.
"""

from __future__ import annotations

import ast
import math
import operator
import runpy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ERROR, WARNING, ExpressionError, Issue

# --------------------------------------------------------------------------- #
# Safe expression evaluation
# --------------------------------------------------------------------------- #

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

#: Functions and constants available inside an expression.
BUILTINS: dict[str, Any] = {
    "pi": math.pi,
    "e": math.e,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "hypot": math.hypot,
    "degrees": math.degrees,
    "radians": math.radians,
    "deg": math.degrees,
    "rad": math.radians,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "float": float,
    "int": int,
}


def safe_eval(expr: str, scope: dict[str, Any] | None = None,
              target: str = "") -> Any:
    """Evaluate an arithmetic expression against ``scope``.

    Only literals, names, unary/binary arithmetic and calls to :data:`BUILTINS`
    are allowed. Anything else raises :class:`ExpressionError`.
    """
    text = (expr or "").strip()
    if not text:
        raise ExpressionError("Empty expression.", expr, target)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Cannot parse '{text}': {exc.msg}", expr, target) from None

    names = dict(BUILTINS)
    names.update(scope or {})

    def _eval(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, complex)):
                return node.value
            raise ExpressionError(
                f"Only numbers are allowed, got {node.value!r}.", expr, target)
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ExpressionError(
                    f"Unknown name '{node.id}'. Define it as a variable or "
                    f"load it from your script.", expr, target)
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](_eval(node.operand))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ExpressionError(
                    "Only direct calls to the built-in maths functions are "
                    "allowed.", expr, target)
            fn = names.get(node.func.id)
            if not callable(fn):
                raise ExpressionError(
                    f"'{node.func.id}' is not a function.", expr, target)
            if node.keywords:
                raise ExpressionError(
                    "Keyword arguments are not allowed in expressions.", expr, target)
            return fn(*[_eval(a) for a in node.args])
        raise ExpressionError(
            f"'{type(node).__name__}' is not allowed in an expression.",
            expr, target)

    try:
        value = _eval(tree)
    except ExpressionError:
        raise
    except ZeroDivisionError:
        raise ExpressionError("Division by zero.", expr, target) from None
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        raise ExpressionError(f"{type(exc).__name__}: {exc}", expr, target) from None

    if not isinstance(value, (int, float, complex, bool)):
        raise ExpressionError(
            f"Expression produced {type(value).__name__}, expected a number.",
            expr, target)
    return value


# --------------------------------------------------------------------------- #
# Script loading
# --------------------------------------------------------------------------- #


def load_script_vars(path: str | Path) -> dict[str, Any]:
    """Run a user Python file and keep its numeric globals.

    Only public (non-underscore) ``int``, ``float``, ``complex`` and ``bool``
    values are kept; functions, modules and containers are ignored.

    The file is executed as ordinary Python, so only ever call this in response
    to an explicit user action.
    """
    p = Path(path)
    if not p.is_file():
        raise ExpressionError(f"Variable script not found: {p}", target=str(p))
    try:
        namespace = runpy.run_path(str(p))
    except Exception as exc:  # noqa: BLE001 - reported with the script's own message
        raise ExpressionError(
            f"Error while running '{p.name}': {type(exc).__name__}: {exc}",
            target=str(p)) from None
    return {
        k: v for k, v in namespace.items()
        if not k.startswith("_") and isinstance(v, (int, float, complex, bool))
        and not isinstance(v, type)
    }


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #


@dataclass
class Parameters:
    """Variables and field bindings for one project."""

    script_path: str | None = None
    script_vars: dict[str, Any] = field(default_factory=dict)
    project_vars: list[tuple[str, str]] = field(default_factory=list)
    bindings: dict[str, str] = field(default_factory=dict)

    # -- variables ---------------------------------------------------- #

    def reload_script(self, base_dir: str | Path | None = None) -> list[Issue]:
        """Re-run the linked script and refresh :attr:`script_vars`."""
        if not self.script_path:
            self.script_vars = {}
            return []
        p = Path(self.script_path)
        if not p.is_absolute() and base_dir:
            p = Path(base_dir) / p
        try:
            self.script_vars = load_script_vars(p)
        except ExpressionError as exc:
            self.script_vars = {}
            return [Issue(ERROR, "script", str(exc), [])]
        return [Issue(
            "info", "script",
            f"Loaded {len(self.script_vars)} variable(s) from {p.name}: "
            f"{', '.join(sorted(self.script_vars)) or '(none)'}")]

    def scope(self) -> tuple[dict[str, Any], list[Issue]]:
        """Resolved variable namespace, plus any evaluation problems."""
        issues: list[Issue] = []
        names: dict[str, Any] = dict(self.script_vars)
        for name, expr in self.project_vars:
            if not name:
                continue
            try:
                names[name] = safe_eval(expr, names, target=name)
            except ExpressionError as exc:
                issues.append(Issue(
                    ERROR, "variable",
                    f"Variable '{name}': {exc}", [name]))
        return names, issues

    def values(self) -> dict[str, Any]:
        """Just the resolved namespace, ignoring problems."""
        return self.scope()[0]

    def set_var(self, name: str, expr: str) -> None:
        """Add or update a project variable, preserving declaration order."""
        for i, (n, _) in enumerate(self.project_vars):
            if n == name:
                self.project_vars[i] = (name, expr)
                return
        self.project_vars.append((name, expr))

    def remove_var(self, name: str) -> None:
        self.project_vars = [(n, e) for n, e in self.project_vars if n != name]

    # -- bindings ----------------------------------------------------- #

    @staticmethod
    def key(comp_id: str, field_name: str) -> str:
        return f"{comp_id}.{field_name}"

    def bind(self, comp_id: str, field_name: str, expr: str) -> None:
        self.bindings[self.key(comp_id, field_name)] = expr

    def unbind(self, comp_id: str, field_name: str) -> None:
        self.bindings.pop(self.key(comp_id, field_name), None)

    def expression_for(self, comp_id: str, field_name: str) -> str | None:
        return self.bindings.get(self.key(comp_id, field_name))

    def rename_component(self, old_id: str, new_id: str) -> None:
        prefix = f"{old_id}."
        for k in [k for k in self.bindings if k.startswith(prefix)]:
            self.bindings[f"{new_id}.{k[len(prefix):]}"] = self.bindings.pop(k)

    def drop_component(self, comp_id: str) -> None:
        prefix = f"{comp_id}."
        for k in [k for k in self.bindings if k.startswith(prefix)]:
            del self.bindings[k]

    def apply(self, net) -> list[Issue]:
        """Evaluate every binding and write the result into the network.

        Returns the problems found; the network is left untouched for any
        binding that failed, so a typo cannot silently zero a parameter.
        """
        names, issues = self.scope()
        for key, expr in sorted(self.bindings.items()):
            comp_id, _, field_name = key.partition(".")
            if comp_id not in net:
                issues.append(Issue(
                    WARNING, "stale_binding",
                    f"Binding '{key}' refers to '{comp_id}', which no longer "
                    f"exists.", [comp_id]))
                continue
            obj = net.get(comp_id)
            if not hasattr(obj, field_name):
                issues.append(Issue(
                    WARNING, "stale_binding",
                    f"Binding '{key}': '{type(obj).__name__}' has no field "
                    f"'{field_name}'.", [comp_id]))
                continue
            try:
                value = safe_eval(expr, names, target=key)
            except ExpressionError as exc:
                issues.append(Issue(ERROR, "binding", str(exc), [comp_id]))
                continue
            current = getattr(obj, field_name)
            if isinstance(current, bool):
                setattr(obj, field_name, bool(value))
            elif isinstance(current, int) and not isinstance(current, bool):
                setattr(obj, field_name, int(round(float(value.real
                                                         if isinstance(value, complex)
                                                         else value))))
            elif isinstance(value, complex) and value.imag == 0:
                setattr(obj, field_name, float(value.real))
            else:
                setattr(obj, field_name, value if isinstance(value, complex)
                        else float(value))
        return issues
