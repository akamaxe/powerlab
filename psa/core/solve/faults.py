"""Shunt faults at a bus.

The pre-fault state is the linear nodal solve (constant-impedance loads, no
c-factor), as in the course. The fault draws ``I_f`` from bus ``k``; every bus
voltage then changes by ``-Z(i,k) I_f``, one impedance-matrix column per
sequence.

Only the three-phase fault exists so far, which needs the positive sequence
alone: ``I_f = U_th / (Z_th + Z_f)``. The unsymmetrical faults arrive with the
negative- and zero-sequence networks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import ValidationError
from ..model.sequence import NetworkModel
from ..model.stamps import Seq
from .linear import BranchCurrents, solve_nodal
from .thevenin import TheveninEquivalent, thevenin_at
from .zbus import ZBus

FAULT_KINDS = ("3ph",)


@dataclass
class FaultResult:
    """Fault current and the post-fault state, positive sequence, in pu."""

    bus: str
    kind: str
    zf: complex
    i_f: complex
    thevenin: TheveninEquivalent
    u_pre: dict[str, complex] = field(default_factory=dict)
    u_post: dict[str, complex] = field(default_factory=dict)
    branches: list[BranchCurrents] = field(default_factory=list)

    @property
    def s_f_mva(self) -> complex:
        """Fault power as the course defines it: ``U_pre conj(I_f) S_base``."""
        th = self.thevenin
        return th.u_th * self.i_f.conjugate() * th.s_base


def solve_fault(model: NetworkModel, bus: str, kind: str = "3ph",
                zf: complex = 0j) -> FaultResult:
    """A fault of ``kind`` at ``bus`` through ``zf`` (pu)."""
    if kind not in FAULT_KINDS:
        raise ValidationError(
            f"The '{kind}' fault needs the negative- and zero-sequence networks, "
            f"which are not modelled yet; only the three-phase fault is available.")
    sm = model.seq(Seq.POS)
    pre = solve_nodal(sm)
    zbus = ZBus(sm)
    th = thevenin_at(model, bus, solution=pre, zbus=zbus)
    i_f = th.u_th / (th.z_th + zf)
    col = zbus.column(bus)
    u_post = {b: pre.u[b] - col[b] * i_f for b in pre.u}
    return FaultResult(bus=bus, kind=kind, zf=zf, i_f=i_f, thevenin=th,
                       u_pre=dict(pre.u), u_post=u_post,
                       branches=pre.branch_currents(u_post))
