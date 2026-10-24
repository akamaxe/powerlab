"""Thevenin equivalent of a network seen from one bus.

``U_th`` is the bus voltage of the pre-fault nodal solve; ``Z_th`` is the
diagonal entry of the impedance matrix with the sources switched off. Both are
cross-checked against an independent route: ``Z_th`` against the Kron
reduction of the admittance matrix, and ``U_th`` against the way the course's
MATLAB solution writes it, ``Z(k,s) E / Z(s,s)`` with the source as an
internal node ``s`` (when the network has a single source behind an impedance).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..errors import TopologyError
from ..model.sequence import INTERNAL_NODE_SUFFIX, NetworkModel
from ..model.stamps import Seq
from ..ybus import kron_reduce
from .linear import NodalSolution, solve_nodal
from .zbus import ZBus


@dataclass
class TheveninEquivalent:
    """``U_th`` and ``Z_th`` (pu) at a bus, with its bases and cross-checks."""

    bus: str
    label: str
    u_th: complex
    z_th: complex
    u_base_kv: float
    z_base_ohm: float
    i_base_ka: float
    s_base: float
    checks: list[tuple[str, float]] = field(default_factory=list)

    @property
    def i_sc(self) -> complex:
        """Solid three-phase fault current, pu."""
        return self.u_th / self.z_th

    @property
    def i_sc_ka(self) -> complex:
        return self.i_sc * self.i_base_ka

    @property
    def s_sc_mva(self) -> complex:
        """Short-circuit power as the course defines it: ``U_th conj(I_sc) S_base``."""
        return self.u_th * self.i_sc.conjugate() * self.s_base

    @property
    def s_sc_nominal_mva(self) -> float:
        """The usual fault level ``sqrt(3) U_base |I_sc|``, at the base voltage."""
        return math.sqrt(3.0) * self.u_base_kv * abs(self.i_sc_ka)


def thevenin_at(model: NetworkModel, bus: str,
                solution: NodalSolution | None = None,
                zbus: ZBus | None = None) -> TheveninEquivalent:
    """The positive-sequence Thevenin equivalent seen from ``bus``."""
    sm = model.seq(Seq.POS)
    if bus not in sm.buses:
        raise TopologyError(
            f"Bus '{sm.labels.get(bus, bus)}' is not energized in this case, so it "
            f"has no Thevenin equivalent.", [bus])
    solution = solution or solve_nodal(sm)
    zbus = zbus or ZBus(sm)
    z_th = zbus.diagonal(bus)
    th = TheveninEquivalent(
        bus=bus, label=sm.labels.get(bus, bus), u_th=solution.u[bus], z_th=z_th,
        u_base_kv=sm.bases.u(bus), z_base_ohm=sm.bases.z(bus),
        i_base_ka=sm.bases.i(bus), s_base=sm.s_base)

    # Z_th again, by Kron reduction of the grounded admittance matrix.
    Y, index = sm.ybus("grounded")
    keep = [i for b, i in index.items() if b in zbus.buses]
    labels = [b for b in index if b in zbus.buses]
    Yg = Y[np.ix_(keep, keep)]
    y = complex(kron_reduce(Yg, [labels.index(bus)], labels=labels)[0, 0])
    th.checks.append(("Z_th: impedance matrix vs Kron reduction",
                      abs(1.0 / y - z_th) / abs(z_th)))

    # U_th again, the MATLAB way, when there is exactly one source behind a Z.
    finite = [s for s in sm.sources if not s.is_ideal]
    if len(finite) == 1 and len(sm.sources) == 1:
        src = finite[0]
        Yi, idx = sm.ybus("internal_node")
        Z = np.linalg.inv(Yi)
        s = idx[src.comp_id + INTERNAL_NODE_SUFFIX]
        k = idx[bus]
        u_route = Z[k, s] * src.e / Z[s, s]
        th.checks.append(("U_th: nodal solve vs Z(k,s) E / Z(s,s)",
                          abs(u_route - th.u_th)))
    return th
