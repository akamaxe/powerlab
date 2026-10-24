"""Study cases: a study plus its options, run on a variant of the network.

A :class:`Case` names the study to run, its options, and how the network
differs for this run without editing it:

* ``exclude``: components (or buses) taken out of service, e.g. the industry
  of S1 disconnected from the meshed grid for S2a-S2c;
* ``overrides``: ``"COMPONENT.field": "expression"``, evaluated with the project
  variables plus ``value``, the field's own value, e.g.
  ``"LDe.p_mw": "0.5*value"`` for S2d or ``"LD4.p_mw": "1.1*value"`` for LFd.

:func:`prepare_network` applies both to a deep copy taken after the project's
bindings have been evaluated, so the edited network is never touched.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .errors import WARNING, Issue
from .network import Network
from .params import Parameters, coerce_like, safe_eval


@dataclass
class Case:
    """One runnable variant: which study, with which options, on which network."""

    id: str
    study: str
    title: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)
    exclude: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"id": self.id, "study": self.study}
        if self.title:
            out["title"] = self.title
        out["options"] = dict(self.options)
        if self.overrides:
            out["overrides"] = dict(self.overrides)
        if self.exclude:
            out["exclude"] = list(self.exclude)
        return out

    @classmethod
    def from_dict(cls, raw: dict) -> Case:
        return cls(
            id=str(raw["id"]),
            study=str(raw.get("study") or raw["id"]),
            title=str(raw.get("title") or ""),
            options=dict(raw.get("options") or {}),
            overrides={str(k): str(v) for k, v in (raw.get("overrides") or {}).items()},
            exclude=[str(x) for x in (raw.get("exclude") or [])],
        )

    @property
    def is_variant(self) -> bool:
        """True when the case changes the network, not just the options."""
        return bool(self.overrides or self.exclude)


def prepare_network(net: Network, case: Case,
                    params: Parameters | None = None) -> tuple[Network, list[Issue]]:
    """A copy of ``net`` with the case's overrides and exclusions applied.

    Raises :class:`~psa.core.errors.ExpressionError` for an override that does
    not evaluate; an override or exclusion naming something that no longer
    exists is only reported.
    """
    if not case.is_variant:
        return net, []
    work = copy.deepcopy(net)
    names = params.values() if params is not None else {}
    issues: list[Issue] = []

    for key, expr in case.overrides.items():
        comp_id, _, field_name = key.partition(".")
        if comp_id not in work or not hasattr(work.get(comp_id), field_name):
            issues.append(Issue(
                WARNING, "stale_override",
                f"Case '{case.id}': override '{key}' names something that does "
                f"not exist, so it is ignored.", [comp_id]))
            continue
        obj = work.get(comp_id)
        current = getattr(obj, field_name)
        value = safe_eval(expr, {**names, "value": current}, target=key)
        setattr(obj, field_name, coerce_like(current, value))

    for obj_id in case.exclude:
        if obj_id in work:
            work.get(obj_id).in_service = False
        else:
            issues.append(Issue(
                WARNING, "stale_exclusion",
                f"Case '{case.id}': '{obj_id}' does not exist, so excluding it "
                f"has no effect.", [obj_id]))
    return work, issues


def unique_case_id(cases: list[Case], base: str) -> str:
    """``base``, or ``base_2``, ``base_3``... whichever is free."""
    taken = {c.id for c in cases}
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"
