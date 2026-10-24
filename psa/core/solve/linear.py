"""Linear nodal solve of one sequence network driven by its sources.

Ideal sources fix their bus voltage; a source behind an impedance becomes a
Norton pair (``1/z`` to ground plus the injection ``e/z``). Loads and shunts are
constant admittances, so a single solve is exact. This is the pre-fault state
the course uses for Thevenin equivalents and short circuits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..errors import TopologyError
from ..model.sequence import SequenceModel

#: Above this condition number the reduced admittance matrix is treated as singular.
COND_LIMIT = 1e14


@dataclass
class BranchCurrents:
    """Currents into a branch at both ends, and the power it takes there."""

    comp_id: str
    bus_i: str
    bus_j: str
    i_i: complex
    i_j: complex
    s_i: complex
    s_j: complex

    @property
    def losses(self) -> complex:
        return self.s_i + self.s_j


@dataclass
class NodalSolution:
    """Bus voltages (pu) of a sequence network, and what follows from them."""

    sm: SequenceModel
    u: dict[str, complex] = field(default_factory=dict)
    i_source: dict[str, complex] = field(default_factory=dict)

    def branch_currents(self, u: dict[str, complex] | None = None) -> list[BranchCurrents]:
        """Currents of every branch for these voltages (default: the solution's)."""
        u = self.u if u is None else u
        out = []
        for br in self.sm.branches:
            if br.bus_i not in u or br.bus_j not in u:
                continue
            ui, uj = u[br.bus_i], u[br.bus_j]
            m = br.matrix()
            ii = complex(m[0, 0] * ui + m[0, 1] * uj)
            ij = complex(m[1, 0] * ui + m[1, 1] * uj)
            out.append(BranchCurrents(br.comp_id, br.bus_i, br.bus_j, ii, ij,
                                      ui * ii.conjugate(), uj * ij.conjugate()))
        return out

    def source_power(self, comp_id: str) -> complex:
        """Complex power (pu) a source delivers into its bus."""
        src = next(s for s in self.sm.sources if s.comp_id == comp_id)
        return self.u[src.bus] * self.i_source[comp_id].conjugate()


def solve_nodal(sm: SequenceModel) -> NodalSolution:
    """Solve ``Y U = I`` with the sources as boundary conditions or Norton pairs."""
    if not sm.sources:
        raise TopologyError(
            "There is no source in the network, so the bus voltages are not "
            "defined. Add an external grid (typically at the feeding bus).")
    Y, index = sm.ybus("grounded")
    n = len(index)
    u = np.zeros(n, dtype=complex)
    inj = np.zeros(n, dtype=complex)
    fixed: list[int] = []
    for src in sm.sources:
        k = index[src.bus]
        if src.is_ideal:
            u[k] = src.e
            if k not in fixed:
                fixed.append(k)
        else:
            inj[k] += src.e / src.z

    rest = [i for i in range(n) if i not in set(fixed)]
    if rest:
        Yrr = Y[np.ix_(rest, rest)]
        cond = np.linalg.cond(Yrr)
        if not np.isfinite(cond) or cond > COND_LIMIT:
            raise TopologyError(
                "The network cannot be solved: some buses are floating (no "
                "path to ground and no source).")
        rhs = inj[rest] - (Y[np.ix_(rest, fixed)] @ u[fixed] if fixed else 0)
        u[rest] = np.linalg.solve(Yrr, rhs)

    labels = list(index)
    sol = NodalSolution(sm, u={b: complex(u[index[b]]) for b in labels})
    i_bus = Y @ u
    for src in sm.sources:
        k = index[src.bus]
        if src.is_ideal:
            sol.i_source[src.comp_id] = complex(i_bus[k])
        else:
            sol.i_source[src.comp_id] = complex((src.e - u[k]) / src.z)
    return sol
