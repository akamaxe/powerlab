"""Shunt faults at a bus: three-phase, single line-to-ground, line-to-line, double
line-to-ground, each through a fault impedance ``Z_f`` (and ``Z_g`` to ground).

The pre-fault state is the linear nodal solve (constant-impedance loads, no
c-factor), as in the course. Each fault only builds the sequence networks it
needs: a three-phase fault the positive one, a line-to-line fault the positive
and negative ones, the faults to ground all three (so they need zero-sequence
data). The solution itself is :func:`psa.core.solve.shunt.solve_shunt_condition`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..model.sequence import NetworkModel
from ..model.stamps import Seq
from .shunt import (
    FAULT_KINDS,
    FAULT_NAMES,
    SEQS,
    SequenceNetworks,
    SequenceThevenin,
    ShuntConditionResult,
    fault_constraints,
    solve_shunt_condition,
    to_phases,
)

#: The sequence networks each fault needs.
NEEDED = {"3ph": (Seq.POS,), "ll": (Seq.POS, Seq.NEG),
          "slg": SEQS, "llg": SEQS}


@dataclass
class BranchEnd:
    """Phase currents (pu) flowing into a branch at one of its buses."""

    comp_id: str
    bus: str
    i_s: np.ndarray

    @property
    def i_abc(self) -> np.ndarray:
        return to_phases(self.i_s)


@dataclass
class FaultResult:
    bus: str
    kind: str
    zf: complex
    zg: complex
    cond: ShuntConditionResult
    branches: list[BranchEnd] = field(default_factory=list)

    @property
    def thevenin(self) -> SequenceThevenin:
        return self.cond.thevenin

    @property
    def name(self) -> str:
        return FAULT_NAMES[self.kind]

    @property
    def i_s(self) -> np.ndarray:
        return self.cond.i_s

    @property
    def i_abc(self) -> np.ndarray:
        return self.cond.i_abc

    @property
    def i_f(self) -> complex:
        """The fault current: phase a for faults involving it, else phase b."""
        return complex(self.i_abc[1] if self.kind in ("ll", "llg") else self.i_abc[0])

    @property
    def i_ground(self) -> complex:
        """Current into earth, ``3 I0``."""
        return complex(3.0 * self.i_s[2])

    @property
    def s_f_mva(self) -> complex:
        """Course definition, for a three-phase fault: ``U_th conj(I_f) S_base``."""
        th = self.thevenin
        return th.u_th * self.i_f.conjugate() * th.s_base


def branch_currents(nets: SequenceNetworks, u_bus_s: dict[str, np.ndarray]
                    ) -> list[BranchEnd]:
    """Sequence currents into every branch end, from the sequence voltages.

    A transformer that is a shunt in the zero sequence (YNd, Dyn) shows its
    zero-sequence current at the bus of its grounded star.
    """
    ends: dict[tuple[str, str], np.ndarray] = {}
    for n, s in enumerate(SEQS):
        if s not in nets.zbus:
            continue
        sm = nets.model.seq(s)
        for br in sm.branches:
            if br.bus_i not in u_bus_s or br.bus_j not in u_bus_s:
                continue
            m = br.matrix()
            ui, uj = u_bus_s[br.bus_i][n], u_bus_s[br.bus_j][n]
            for bus, current in ((br.bus_i, m[0, 0] * ui + m[0, 1] * uj),
                                 (br.bus_j, m[1, 0] * ui + m[1, 1] * uj)):
                ends.setdefault((br.comp_id, bus), np.zeros(3, dtype=complex))[n] += current
        for sh in sm.shunts:
            if sh.kind == "transformer" and sh.bus in u_bus_s:
                ends.setdefault((sh.comp_id, sh.bus), np.zeros(3, dtype=complex))[n] += \
                    sh.y * u_bus_s[sh.bus][n]
    return [BranchEnd(c, b, i) for (c, b), i in ends.items()]


def solve_fault(model: NetworkModel, bus: str, kind: str = "3ph",
                zf: complex = 0j, zg: complex = 0j) -> FaultResult:
    """A fault of ``kind`` at ``bus`` through ``zf`` (and ``zg``), all in pu."""
    if kind not in FAULT_KINDS:
        raise ValueError(f"unknown fault type '{kind}'")
    nets = SequenceNetworks.build(model, NEEDED[kind])
    A, B = fault_constraints(kind, zf, zg)
    cond = solve_shunt_condition(nets, bus, A, B)
    return FaultResult(bus=bus, kind=kind, zf=complex(zf), zg=complex(zg), cond=cond,
                       branches=branch_currents(nets, cond.u_bus_s))
