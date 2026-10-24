"""Per-unit base propagation.

One global ``S_base``, one ``U_base`` per galvanically separated voltage zone.
Starting from the reference bus, the base voltage is carried across every
series branch: unity for a line, the **rated** turns ratio for a transformer.

The off-nominal ``tap`` is deliberately *not* applied here. Bases follow rated
ratios only (the standard convention), which keeps them independent of tap
position; a tap deviation belongs inside the branch model instead.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .components import Bus, Transformer, buses_of, display_name, is_series
from .errors import ERROR, Issue, BaseConflict
from .network import Network

#: Relative tolerance when comparing two candidate bases for the same bus.
BASE_RTOL = 1e-9


@dataclass
class BaseTable:
    """Per-unit bases for every reachable bus."""

    s_base_mva: float
    u_base_kv: dict[str, float] = field(default_factory=dict)
    z_base_ohm: dict[str, float] = field(default_factory=dict)
    i_base_ka: dict[str, float] = field(default_factory=dict)
    zones: list[tuple[float, list[str]]] = field(default_factory=list)
    conflicts: list[BaseConflict] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)

    def u(self, bus: str) -> float:
        try:
            return self.u_base_kv[bus]
        except KeyError:
            raise KeyError(
                f"Bus '{bus}' has no voltage base: it is not connected to the "
                f"base reference bus."
            ) from None

    def z(self, bus: str) -> float:
        self.u(bus)
        return self.z_base_ohm[bus]

    def i(self, bus: str) -> float:
        self.u(bus)
        return self.i_base_ka[bus]

    def issues(self) -> list[Issue]:
        out = [c.as_issue() for c in self.conflicts]
        for b in self.unreachable:
            out.append(Issue(
                ERROR, "unreachable",
                f"Bus '{b}' cannot be reached from the base reference bus, so "
                f"it has no per-unit base.", [b]))
        return out


def _ratio(comp, from_bus: str) -> float:
    """Base-voltage multiplier when travelling across ``comp`` away from ``from_bus``."""
    if isinstance(comp, Transformer):
        if from_bus == comp.bus_hv:
            return comp.u2n_kv / comp.u1n_kv
        return comp.u1n_kv / comp.u2n_kv
    return 1.0


def propagate_bases(net: Network) -> BaseTable:
    """Derive ``U_base``, ``Z_base`` and ``I_base`` for every bus.

    Buses whose ``u_base_kv`` is set are pinned and seed the search alongside
    the reference bus. The first assignment for a bus wins and is never
    overwritten; a second, different candidate is recorded as a
    :class:`~psa.core.errors.BaseConflict` so the user sees *which* transformer
    around the loop is inconsistent.
    """
    table = BaseTable(s_base_mva=net.s_base_mva)

    # Adjacency over active series branches, ordered by component id so the
    # traversal (and therefore which assignment wins) is deterministic.
    # Bases follow the nameplate design, not the switching state: opening a
    # breaker must not strip a bus of its voltage base. So the traversal spans
    # every series branch, in service or not.
    adj: dict[str, list[tuple[str, str, object]]] = {b: [] for b in net.buses}
    for comp in sorted(net.components.values(), key=lambda c: c.id):
        if not is_series(comp):
            continue
        bs = buses_of(comp)
        if len(bs) != 2:
            continue
        bi, bj = bs
        if bi in adj and bj in adj:
            adj[bi].append((bj, comp.id, comp))
            adj[bj].append((bi, comp.id, comp))

    seeds: list[tuple[str, float]] = []
    if net.base_ref_bus in adj and net.base_ref_u_kv > 0:
        seeds.append((net.base_ref_bus, float(net.base_ref_u_kv)))
    for bus in net.buses.values():
        if bus.u_base_kv:
            seeds.append((bus.id, float(bus.u_base_kv)))

    queue: deque[str] = deque()
    for bus_id, u in seeds:
        if bus_id not in table.u_base_kv:
            table.u_base_kv[bus_id] = u
            queue.append(bus_id)

    while queue:
        u_bus = queue.popleft()
        u_val = table.u_base_kv[u_bus]
        for v_bus, comp_id, comp in adj[u_bus]:
            cand = u_val * _ratio(comp, u_bus)
            if v_bus not in table.u_base_kv:
                table.u_base_kv[v_bus] = cand
                queue.append(v_bus)
            else:
                have = table.u_base_kv[v_bus]
                if abs(cand - have) > BASE_RTOL * max(abs(cand), abs(have), 1e-30):
                    table.conflicts.append(
                        BaseConflict(bus=v_bus, via=comp_id,
                                     expected=have, found=cand))

    table.unreachable = [b for b in adj if b not in table.u_base_kv]

    for bus_id, u in table.u_base_kv.items():
        table.z_base_ohm[bus_id] = u * u / net.s_base_mva
        table.i_base_ka[bus_id] = net.s_base_mva / (math.sqrt(3.0) * u)

    groups: dict[float, list[str]] = {}
    for bus_id, u in table.u_base_kv.items():
        groups.setdefault(round(u, 9), []).append(bus_id)
    table.zones = sorted(
        ((u, sorted(ids)) for u, ids in groups.items()),
        key=lambda t: -t[0],
    )
    return table
