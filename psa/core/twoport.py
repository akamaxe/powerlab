"""Two-port (ABCD / F) matrix extraction between two buses.

Convention throughout: ``[U_1; I_1] = F @ [U_2; I_2]``, port 1 upstream (source
side), port 2 downstream (load side), ``I_1`` flowing *into* the network at
port 1 and ``I_2`` flowing *out of* it at port 2.

Two independent methods are computed and cross-checked:

a. **Cascade** -- multiply the per-element F matrices along the path. Only
   possible when the path between the ports is unique, but it is the method
   that exposes the individual component matrices.
b. **General** -- Kron-reduce the Y-bus to the two port buses and convert the
   2x2 Y-parameters to ABCD. Works on meshed networks too.

Their agreement is asserted, not merely displayed: line charging lives inside
``F_pi`` for method (a) and on the Y diagonal for method (b), so a double count
in either one shows up here as a ~1e-5 relative residual that would otherwise
be invisible at four decimals.

Termination rule
----------------
1. Every **series** element is included, unconditionally.
2. **Line charging half-shunts** are included at *both* ends of every included
   line, including when that end is a port bus. They are never node shunts and
   never excludable.
3. A **node shunt** (load, shunt, source, grounded impedance) is included if
   and only if its bus is neither port.
4. Rule 3 is overridable per component, with one hard block: an *ideal* source
   at a port bus can never be re-included, because as a shunt it short-circuits
   the port.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import islice

import networkx as nx
import numpy as np

from .components import Source, buses_of, display_name, is_node_shunt
from .errors import DecoupledPortsError, TopologyError, ValidationError
from .network import Network
from .perunit import PuModel, PuSeries, to_per_unit
from .ybus import build_ybus, kron_reduce

#: Relative tolerance for the agreement between the two methods.
METHOD_RTOL = 1e-9


# --------------------------------------------------------------------------- #
# Elementary two-port blocks
# --------------------------------------------------------------------------- #


def f_series(z: complex) -> np.ndarray:
    """Series impedance."""
    return np.array([[1.0, z], [0.0, 1.0]], dtype=complex)


def f_shunt(y: complex) -> np.ndarray:
    """Shunt admittance to ground."""
    return np.array([[1.0, 0.0], [y, 1.0]], dtype=complex)


def f_pi(z: complex, ysh: complex) -> np.ndarray:
    """Symmetric PI section: series ``z``, ``ysh`` at each end."""
    a = 1.0 + ysh * z
    return np.array([[a, z], [ysh * (2.0 + ysh * z), a]], dtype=complex)


def f_of_branch(br: PuSeries) -> np.ndarray:
    """The F matrix of one per-unit series branch."""
    if br.ysh_pu == 0:
        return f_series(br.z_pu)
    return f_pi(br.z_pu, br.ysh_pu)


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #


@dataclass
class TwoPortResult:
    """Everything the S1a report needs, plus the cross-check."""

    bus_i: str
    bus_j: str
    f_general: np.ndarray
    f_cascade: np.ndarray | None = None
    per_element: list[tuple[str, np.ndarray]] = field(default_factory=list)
    excluded_at_ports: list[tuple[str, str]] = field(default_factory=list)
    path: list[str] | None = None
    cascade_status: str = "ok"     # ok | no_path | not_unique | parallel_merged
    label_i: str = ""
    label_j: str = ""
    residual_abs: float = 0.0
    residual_rel: float = 0.0
    reciprocity_applies: bool = True

    @property
    def f(self) -> np.ndarray:
        """The preferred matrix: the cascade when available, else the general one."""
        return self.f_cascade if self.f_cascade is not None else self.f_general

    @property
    def abcd(self) -> tuple[complex, complex, complex, complex]:
        m = self.f
        return complex(m[0, 0]), complex(m[0, 1]), complex(m[1, 0]), complex(m[1, 1])

    @property
    def det(self) -> complex:
        return complex(np.linalg.det(self.f))

    def terminated_impedance(self, z_load: complex) -> complex:
        """``Z_in = (A*Z + B) / (C*Z + D)`` for a load ``z_load`` at port 2."""
        a, b, c, d = self.abcd
        return (a * z_load + b) / (c * z_load + d)

    def solve_downstream(self, u1: complex, i1: complex) -> tuple[complex, complex]:
        """``[U_2; I_2] = F^-1 [U_1; I_1]``, by solve rather than an explicit inverse."""
        x = np.linalg.solve(self.f, np.array([u1, i1], dtype=complex))
        return complex(x[0]), complex(x[1])


# --------------------------------------------------------------------------- #
# Termination rule
# --------------------------------------------------------------------------- #


def resolve_port_exclusions(net: Network, bus_i: str, bus_j: str,
                            overrides: set[str] = frozenset()) -> set[str]:
    """Component ids dropped because they sit at a port bus.

    ``overrides`` names components the user wants to keep despite rule 3. An
    ideal source at a port bus is never honoured, because as a shunt it is a
    short circuit across the port.
    """
    ports = {bus_i, bus_j}
    excluded: set[str] = set()
    for comp in net.active_components():
        if not is_node_shunt(comp):
            continue
        bs = buses_of(comp)
        if not bs or bs[0] not in ports:
            continue
        if comp.id in overrides and not (isinstance(comp, Source) and comp.is_ideal):
            continue
        excluded.add(comp.id)
    return excluded


def _excluded_detail(net: Network, excluded: set[str]) -> list[tuple[str, str]]:
    out = []
    for comp_id in sorted(excluded):
        comp = net.components[comp_id]
        out.append((comp_id, f"{display_name(comp)} at bus "
                             f"{buses_of(comp)[0]} ({comp.TYPE})"))
    return out


# --------------------------------------------------------------------------- #
# Method (a): cascade along the path
# --------------------------------------------------------------------------- #


def _merge_parallel(branches: list[PuSeries]) -> PuSeries:
    """Combine parallel PI branches: impedances in parallel, shunts summed."""
    y = sum(1.0 / b.z_pu for b in branches)
    ysh = sum(b.ysh_pu for b in branches)
    return PuSeries(
        comp_id="+".join(b.comp_id for b in branches),
        bus_i=branches[0].bus_i, bus_j=branches[0].bus_j,
        z_pu=1.0 / y, ysh_pu=ysh,
    )


def cascade_two_port(net: Network, pu: PuModel, bus_i: str, bus_j: str,
                     excluded: set[str]
                     ) -> tuple[np.ndarray | None, list[tuple[str, np.ndarray]],
                                list[str] | None, str]:
    """Multiply the per-element F matrices along the unique path ``bus_i`` to ``bus_j``."""
    series = [b for b in pu.series if b.comp_id not in excluded]
    # A simple Graph, not a MultiGraph: nx.all_simple_paths on a MultiGraph
    # yields the same node sequence once per parallel edge, which would make
    # two cables drawn separately look like a mesh. Parallel branches are
    # merged below instead, from ``by_pair``.
    g = nx.Graph()
    g.add_nodes_from(pu.buses or pu.bases.u_base_kv.keys())
    for br in series:
        g.add_edge(br.bus_i, br.bus_j)

    if bus_i not in g or bus_j not in g or not nx.has_path(g, bus_i, bus_j):
        return None, [], None, "no_path"

    paths = list(islice(nx.all_simple_paths(g, bus_i, bus_j), 2))
    if len(paths) != 1:
        return None, [], None, "not_unique"
    path = paths[0]

    # Node shunts per bus, summed. Shunt blocks at the same node commute and
    # compose additively, so summing first is exact and order independent.
    shunt_y: dict[str, complex] = {}
    shunt_names: dict[str, list[str]] = {}
    for sh in pu.shunts:
        if sh.comp_id in excluded:
            continue
        shunt_y[sh.bus] = shunt_y.get(sh.bus, 0j) + sh.y_pu
        shunt_names.setdefault(sh.bus, []).append(
            display_name(net.components[sh.comp_id]))
    for src in pu.sources:
        if src.comp_id in excluded or src.is_ideal:
            continue
        shunt_y[src.bus] = shunt_y.get(src.bus, 0j) + 1.0 / src.z_th_pu
        shunt_names.setdefault(src.bus, []).append(
            display_name(net.components[src.comp_id]))

    by_pair: dict[frozenset, list[PuSeries]] = {}
    for br in series:
        by_pair.setdefault(frozenset((br.bus_i, br.bus_j)), []).append(br)

    status = "ok"
    f_total = np.eye(2, dtype=complex)
    per_element: list[tuple[str, np.ndarray]] = []

    for k in range(len(path) - 1):
        here, nxt = path[k], path[k + 1]

        # Node shunt at an intermediate bus (never at the ports: port shunts are
        # either excluded by the termination rule or kept via an override, and
        # an override at a port is intentionally applied here too).
        if k > 0 and shunt_y.get(here):
            fk = f_shunt(shunt_y[here])
            f_total = f_total @ fk
            per_element.append((" + ".join(shunt_names[here]), fk))

        branches = by_pair[frozenset((here, nxt))]
        if len(branches) > 1:
            branch = _merge_parallel(branches)
            status = "parallel_merged"
            label = " || ".join(display_name(net.components[b.comp_id])
                                for b in branches)
        else:
            branch = branches[0]
            label = display_name(net.components[branch.comp_id])

        # The branch's own charging is inside f_pi. It must NOT also be added
        # as a node shunt, or it is counted twice.
        fk = f_of_branch(branch)
        f_total = f_total @ fk
        per_element.append((label, fk))

    return f_total, per_element, path, status


# --------------------------------------------------------------------------- #
# Method (b): Y-bus + Kron + Y-to-ABCD
# --------------------------------------------------------------------------- #


def general_two_port(pu: PuModel, bus_i: str, bus_j: str,
                     excluded: set[str]) -> np.ndarray:
    """Reduce the network to a 2x2 admittance matrix and convert it to ABCD."""
    Y, index = build_ybus(pu, exclude=excluded, include_sources=True)
    if bus_i not in index or bus_j not in index:
        missing = [b for b in (bus_i, bus_j) if b not in index]
        raise ValidationError(f"Unknown port bus: {', '.join(missing)}", missing)

    labels = list(index.keys())
    Yr = kron_reduce(Y, [index[bus_i], index[bus_j]], labels=labels)
    y11, y12, y21, y22 = Yr[0, 0], Yr[0, 1], Yr[1, 0], Yr[1, 1]

    scale = float(np.max(np.abs(Y))) or 1.0
    if abs(y21) < 1e-14 * scale:
        raise DecoupledPortsError(
            f"Buses '{bus_i}' and '{bus_j}' are not coupled through the "
            f"included elements, so no two-port matrix exists between them.",
            bus_i, bus_j)

    # I2 is defined as injected in the Y formulation; the ABCD convention has
    # it flowing out of the network, hence the sign.
    a = -y22 / y21
    b = -1.0 / y21
    c = y12 - y11 * y22 / y21
    d = -y11 / y21
    return np.array([[a, b], [c, d]], dtype=complex)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def two_port(net: Network, bus_i: str, bus_j: str,
             exclude_overrides: set[str] = frozenset(),
             pu: PuModel | None = None) -> TwoPortResult:
    """Two-port matrix seen from ``bus_i`` (port 1) towards ``bus_j`` (port 2).

    Note that ``two_port(a, e)`` and ``two_port(e, a)`` are different matrices,
    not inverses of each other under the same labelling.
    """
    if bus_i == bus_j:
        raise TopologyError("The two ports must be different buses.", [bus_i])
    for b in (bus_i, bus_j):
        if b not in net.buses:
            raise ValidationError(f"Bus '{b}' does not exist.", [b])

    pu = pu or to_per_unit(net)
    excluded = resolve_port_exclusions(net, bus_i, bus_j, set(exclude_overrides))

    f_gen = general_two_port(pu, bus_i, bus_j, excluded)
    f_cas, per_element, path, status = cascade_two_port(
        net, pu, bus_i, bus_j, excluded)

    res = TwoPortResult(
        bus_i=bus_i, bus_j=bus_j,
        f_general=f_gen, f_cascade=f_cas,
        per_element=per_element,
        excluded_at_ports=_excluded_detail(net, excluded),
        path=path, cascade_status=status,
        label_i=display_name(net.buses[bus_i]),
        label_j=display_name(net.buses[bus_j]),
        reciprocity_applies=all(
            t.tap == 1.0 for t in net.of_type("transformer") if t.in_service),
    )

    if f_cas is not None:
        diff = np.max(np.abs(f_cas - f_gen))
        scale = float(np.max(np.abs(f_cas))) or 1.0
        res.residual_abs = float(diff)
        res.residual_rel = float(diff / scale)
        if res.residual_rel > METHOD_RTOL:
            raise TopologyError(
                f"The cascade and Y-bus two-port methods disagree by "
                f"{res.residual_rel:.3e} (relative), which is far above the "
                f"{METHOD_RTOL:.0e} tolerance. This normally means line "
                f"charging has been counted twice. Ports: {bus_i} -> {bus_j}.",
                [bus_i, bus_j])
    return res
