"""The :class:`Network` container and its structural validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

import networkx as nx

from .components import (
    Bus,
    Source,
    TYPE_REGISTRY,
    buses_of,
    display_name,
    is_node_shunt,
    is_series,
)
from .errors import ERROR, INFO, WARNING, Issue, ValidationError


@dataclass
class Network:
    """A power system: bases, buses and everything attached to them.

    ``buses`` and ``components`` are id-keyed so lookups are O(1) and the GUI
    can address anything by a single string.
    """

    s_base_mva: float = 10.0
    f_hz: float = 50.0
    base_ref_bus: str = ""
    base_ref_u_kv: float = 0.0
    buses: dict[str, Bus] = field(default_factory=dict)
    components: dict[str, Any] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Container protocol
    # ------------------------------------------------------------------ #

    def add(self, obj: Any) -> Any:
        """Add a bus or a component. Raises on a duplicate id."""
        if obj.id in self.buses or obj.id in self.components:
            raise ValidationError(f"Duplicate id '{obj.id}'", [obj.id])
        if isinstance(obj, Bus):
            self.buses[obj.id] = obj
        else:
            self.components[obj.id] = obj
        return obj

    def remove(self, obj_id: str) -> Any:
        """Remove a bus or component by id and return it."""
        if obj_id in self.buses:
            return self.buses.pop(obj_id)
        if obj_id in self.components:
            return self.components.pop(obj_id)
        raise KeyError(obj_id)

    def get(self, obj_id: str) -> Any:
        if obj_id in self.buses:
            return self.buses[obj_id]
        return self.components[obj_id]

    def __contains__(self, obj_id: str) -> bool:
        return obj_id in self.buses or obj_id in self.components

    def __iter__(self) -> Iterator[Any]:
        yield from self.buses.values()
        yield from self.components.values()

    def of_type(self, type_name: str) -> list[Any]:
        cls = TYPE_REGISTRY[type_name]
        if cls is Bus:
            return list(self.buses.values())
        return [c for c in self.components.values() if isinstance(c, cls)]

    def dependents_of(self, bus_id: str) -> list[Any]:
        """Components that would be orphaned if ``bus_id`` were deleted."""
        return [c for c in self.components.values() if bus_id in buses_of(c)]

    # ------------------------------------------------------------------ #
    # Active (in-service) views. Every study works on these.
    # ------------------------------------------------------------------ #

    def active_components(self, exclude: set[str] | None = None) -> list[Any]:
        skip = exclude or set()
        out = []
        for c in self.components.values():
            if not c.in_service or c.id in skip:
                continue
            if any(b not in self.buses or not self.buses[b].in_service
                   for b in buses_of(c)):
                continue
            out.append(c)
        return out

    def active_bus_ids(self) -> list[str]:
        """In-service bus ids, in insertion order (deterministic matrix ordering)."""
        return [b.id for b in self.buses.values() if b.in_service]

    def series_edges(self, exclude: set[str] | None = None) -> list[tuple[str, str, str]]:
        """``(comp_id, bus_i, bus_j)`` for every active series branch."""
        return [
            (c.id, *buses_of(c))
            for c in self.active_components(exclude)
            if is_series(c)
        ]

    def shunt_attachments(self, exclude: set[str] | None = None) -> list[tuple[str, str]]:
        """``(comp_id, bus)`` for every active node-shunt component."""
        return [
            (c.id, buses_of(c)[0])
            for c in self.active_components(exclude)
            if is_node_shunt(c) and buses_of(c)
        ]

    def graph(self, exclude: set[str] | None = None) -> nx.MultiGraph:
        """Connectivity graph over active series branches only."""
        g = nx.MultiGraph()
        g.add_nodes_from(self.active_bus_ids())
        for comp_id, bi, bj in sorted(self.series_edges(exclude)):
            g.add_edge(bi, bj, key=comp_id, comp_id=comp_id)
        return g

    def slack_sources(self) -> list[Source]:
        return [
            c for c in self.active_components()
            if isinstance(c, Source) and c.is_slack
        ]

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate(self) -> list[Issue]:
        """Collect structural problems without raising. Errors block the studies."""
        issues: list[Issue] = []

        if not self.buses:
            issues.append(Issue(ERROR, "empty", "The network has no buses."))
            return issues

        if self.s_base_mva <= 0:
            issues.append(Issue(ERROR, "s_base", "S base must be positive."))

        # Reference bus for the per-unit bases
        if not self.base_ref_bus:
            issues.append(Issue(
                ERROR, "no_base_ref",
                "No base reference bus is set. Pick the bus where the base "
                "voltage is defined (Network > Base settings)."))
        elif self.base_ref_bus not in self.buses:
            issues.append(Issue(
                ERROR, "bad_base_ref",
                f"The base reference bus '{self.base_ref_bus}' does not exist.",
                [self.base_ref_bus]))
        elif self.base_ref_u_kv <= 0:
            issues.append(Issue(
                ERROR, "bad_base_u",
                "The base voltage at the reference bus must be positive."))

        # Dangling terminals
        for c in self.components.values():
            for attr in getattr(c, "BUS_FIELDS", ()):
                val = getattr(c, attr, None)
                if val and val not in self.buses:
                    issues.append(Issue(
                        ERROR, "dangling",
                        f"{display_name(c)}: terminal '{attr}' points at bus "
                        f"'{val}', which does not exist.", [c.id]))
            if is_series(c):
                bs = buses_of(c)
                if len(bs) < 2:
                    issues.append(Issue(
                        ERROR, "unconnected",
                        f"{display_name(c)} is a series branch but is not "
                        f"connected at both ends.", [c.id]))
                elif bs[0] == bs[1]:
                    issues.append(Issue(
                        ERROR, "self_loop",
                        f"{display_name(c)} connects bus '{bs[0]}' to itself.",
                        [c.id]))
            elif is_node_shunt(c) and not buses_of(c):
                issues.append(Issue(
                    ERROR, "unconnected",
                    f"{display_name(c)} is not connected to a bus.", [c.id]))

        # Component-specific sanity
        for c in self.of_type("transformer"):
            if c.u1n_kv <= 0 or c.u2n_kv <= 0:
                issues.append(Issue(
                    ERROR, "bad_ratio",
                    f"{display_name(c)}: rated voltages must be positive.", [c.id]))
            if c.sn_mva <= 0:
                issues.append(Issue(
                    ERROR, "bad_sn",
                    f"{display_name(c)}: rated power must be positive.", [c.id]))
            if c.x_pct == 0 and c.r_pct == 0:
                issues.append(Issue(
                    WARNING, "zero_z",
                    f"{display_name(c)} has zero impedance.", [c.id]))
        for c in self.of_type("line"):
            if c.n_parallel < 1:
                issues.append(Issue(
                    ERROR, "bad_n",
                    f"{display_name(c)}: the number of parallel lines must be "
                    f"at least 1.", [c.id]))
            if c.length_km < 0:
                issues.append(Issue(
                    ERROR, "bad_len",
                    f"{display_name(c)}: length must not be negative.", [c.id]))
        for c in self.of_type("load"):
            if not 0.0 < c.cos_phi <= 1.0:
                issues.append(Issue(
                    ERROR, "bad_pf",
                    f"{display_name(c)}: cos phi must be in (0, 1].", [c.id]))

        # Islands
        g = self.graph()
        if g.number_of_nodes() > 1:
            comps = list(nx.connected_components(g))
            isolated = [n for n in g.nodes if g.degree(n) == 0
                        and not self.dependents_of(n)]
            if len(comps) > 1:
                issues.append(Issue(
                    WARNING, "islands",
                    f"The network has {len(comps)} separate islands. Studies "
                    f"only see the island containing their reference bus.",
                    sorted(min(c) for c in comps)))
            for n in isolated:
                issues.append(Issue(
                    INFO, "isolated_bus",
                    f"Bus '{display_name(self.buses[n])}' has nothing attached.",
                    [n]))

        return issues

    def require_valid(self) -> None:
        """Raise :class:`ValidationError` if validation produced any error."""
        errs = [i for i in self.validate() if i.is_error]
        if errs:
            ids = [i for e in errs for i in e.ids]
            raise ValidationError(
                "The network is not ready to analyse:\n  "
                + "\n  ".join(str(e) for e in errs),
                ids,
            )
