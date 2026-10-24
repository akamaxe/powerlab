"""Network studies: equivalent impedance, nodal voltage solve, Thevenin."""

from __future__ import annotations

import cmath
from dataclasses import dataclass, field

import numpy as np

from .components import display_name
from .errors import TopologyError, ValidationError
from .network import Network
from .perunit import PuModel, to_per_unit
from .twoport import TwoPortResult, two_port
from .ybus import build_ybus, kron_reduce

#: Tolerances for the Kron / two-port agreement on the equivalent impedance.
#: The Schur complement subtracts nearly equal quantities, so the achievable
#: absolute residual is around 1e-11 on a magnitude-25 value, not 1e-14.
EQUIV_RTOL = 1e-9
EQUIV_ATOL = 1e-12


# --------------------------------------------------------------------------- #
# Equivalent impedance
# --------------------------------------------------------------------------- #


@dataclass
class EquivalentZResult:
    """``Z = U/I`` seen from one bus, with the two-method cross-check."""

    bus: str
    z_pu: complex
    z_ohm: complex
    z_base_ohm: float
    z_pu_twoport: complex | None = None
    residual_abs: float = 0.0
    residual_rel: float = 0.0
    far_bus: str | None = None
    z_termination_pu: complex | None = None
    label: str = ""

    @property
    def magnitude(self) -> float:
        return abs(self.z_pu)

    @property
    def angle_deg(self) -> float:
        return np.degrees(cmath.phase(self.z_pu))


def equivalent_impedance(net: Network, bus: str,
                         pu: PuModel | None = None,
                         ignore_sources: bool = True,
                         far_bus: str | None = None) -> EquivalentZResult:
    """Impedance of everything attached to ``bus``, looking into the network.

    Sources are excluded by default: the question "what is the impedance of the
    industry seen from its feeding point" is about the passive network, not
    about the grid feeding it.

    When ``far_bus`` is given, the same value is recomputed through the two-port
    relation ``(A*Z_term + B)/(C*Z_term + D)`` and the residual is reported.
    """
    if bus not in net.buses:
        raise ValidationError(f"Bus '{bus}' does not exist.", [bus])
    pu = pu or to_per_unit(net)

    exclude = {s.comp_id for s in pu.sources} if ignore_sources else set()
    Y, index = build_ybus(pu, exclude=exclude, include_sources=not ignore_sources)
    if bus not in index:
        raise ValidationError(f"Bus '{bus}' has no per-unit base.", [bus])

    labels = list(index.keys())
    Yr = kron_reduce(Y, [index[bus]], labels=labels)
    y = complex(Yr[0, 0])
    if y == 0:
        raise TopologyError(
            f"Nothing is connected to bus '{display_name(net.buses[bus])}', so "
            f"its equivalent impedance is infinite.", [bus])
    z_pu = 1.0 / y
    z_base = pu.bases.z(bus)

    result = EquivalentZResult(
        bus=bus, z_pu=z_pu, z_ohm=z_pu * z_base, z_base_ohm=z_base,
        far_bus=far_bus, label=display_name(net.buses[bus]))

    if far_bus:
        tp = two_port(net, bus, far_bus, pu=pu)
        z_term = _termination_admittance(net, pu, far_bus, exclude)
        if z_term is not None:
            result.z_termination_pu = z_term
            z_tp = tp.terminated_impedance(z_term)
            result.z_pu_twoport = z_tp
            result.residual_abs = abs(z_tp - z_pu)
            result.residual_rel = result.residual_abs / max(abs(z_pu), 1e-30)
    return result


def _termination_admittance(net: Network, pu: PuModel, bus: str,
                            exclude: set[str]) -> complex | None:
    """Aggregate impedance of the node shunts sitting at ``bus``."""
    y = 0j
    for sh in pu.shunts:
        if sh.bus == bus and sh.comp_id not in exclude:
            y += sh.y_pu
    for src in pu.sources:
        if src.bus == bus and src.comp_id not in exclude and not src.is_ideal:
            y += 1.0 / src.z_th_pu
    if y == 0:
        return None
    return 1.0 / y


# --------------------------------------------------------------------------- #
# Nodal voltage solve
# --------------------------------------------------------------------------- #


@dataclass
class BranchFlow:
    """Currents, powers and losses of one series branch, both ends."""

    comp_id: str
    name: str
    bus_from: str
    bus_to: str
    i_from_pu: complex = 0j
    i_to_pu: complex = 0j
    s_from_pu: complex = 0j
    s_to_pu: complex = 0j

    @property
    def losses_pu(self) -> complex:
        return self.s_from_pu + self.s_to_pu


@dataclass
class LoadFlowResult:
    """Bus voltages and branch flows for a network driven by its sources."""

    u_pu: dict[str, complex] = field(default_factory=dict)
    u_kv: dict[str, float] = field(default_factory=dict)
    i_inject_pu: dict[str, complex] = field(default_factory=dict)
    branch: dict[str, BranchFlow] = field(default_factory=dict)
    s_source_mva: dict[str, complex] = field(default_factory=dict)
    i_source_pu: dict[str, complex] = field(default_factory=dict)
    s_base_mva: float = 10.0
    slack_buses: list[str] = field(default_factory=list)

    @property
    def total_losses_pu(self) -> complex:
        return sum((b.losses_pu for b in self.branch.values()), 0j)

    def u_polar(self, bus: str) -> tuple[float, float]:
        mag, ang = cmath.polar(self.u_pu[bus])
        return mag, np.degrees(ang)


def solve_voltages(net: Network, pu: PuModel | None = None) -> LoadFlowResult:
    """Solve the linear network driven by its voltage sources.

    Ideal sources fix their bus voltage exactly; sources behind a Thevenin
    impedance become a Norton pair (a shunt plus a current injection). With
    constant-impedance loads the problem is linear, so one solve is exact.
    """
    pu = pu or to_per_unit(net)
    if not pu.sources:
        raise TopologyError(
            "There is no source in the network, so the bus voltages are not "
            "defined. Add a Source component (typically at the feeding bus).")

    ideal = [s for s in pu.sources if s.is_ideal]
    if not ideal and not any(s.is_slack for s in pu.sources):
        raise TopologyError("No slack source: the voltage angle reference is undefined.")

    Y, index = build_ybus(pu, include_sources=True)
    labels = list(index.keys())
    n = len(labels)

    u = np.zeros(n, dtype=complex)
    slack_idx = []
    for src in ideal:
        i = index[src.bus]
        u[i] = src.u_pu
        if i not in slack_idx:
            slack_idx.append(i)

    # Norton injections from non-ideal sources.
    inj = np.zeros(n, dtype=complex)
    for src in pu.sources:
        if not src.is_ideal:
            inj[index[src.bus]] += src.u_pu / src.z_th_pu

    rest = [i for i in range(n) if i not in set(slack_idx)]
    if slack_idx:
        rhs = inj[rest] - Y[np.ix_(rest, slack_idx)] @ u[slack_idx]
    else:
        rhs = inj[rest]

    if rest:
        Yrr = Y[np.ix_(rest, rest)]
        cond = np.linalg.cond(Yrr)
        if not np.isfinite(cond) or cond > 1e14:
            raise TopologyError(
                "The network cannot be solved: some buses are floating (no "
                "path to ground and no source).")
        u[rest] = np.linalg.solve(Yrr, rhs)

    i_inject = Y @ u

    result = LoadFlowResult(s_base_mva=pu.s_base,
                            slack_buses=[labels[i] for i in slack_idx])
    for bus_id, k in index.items():
        result.u_pu[bus_id] = complex(u[k])
        result.u_kv[bus_id] = abs(u[k]) * pu.bases.u(bus_id)
        result.i_inject_pu[bus_id] = complex(i_inject[k])

    for br in pu.series:
        if br.bus_i not in index or br.bus_j not in index:
            continue
        ui, uj = u[index[br.bus_i]], u[index[br.bus_j]]
        y = 1.0 / br.z_pu
        i_from = (ui - uj) * y + ui * br.ysh_pu
        i_to = (uj - ui) * y + uj * br.ysh_pu
        comp = net.components[br.comp_id]
        result.branch[br.comp_id] = BranchFlow(
            comp_id=br.comp_id, name=display_name(comp),
            bus_from=br.bus_i, bus_to=br.bus_j,
            i_from_pu=complex(i_from), i_to_pu=complex(i_to),
            s_from_pu=complex(ui * np.conj(i_from)),
            s_to_pu=complex(uj * np.conj(i_to)),
        )

    # Current delivered by each source, and the power it supplies.
    for src in pu.sources:
        k = index[src.bus]
        if src.is_ideal:
            # Everything injected at the bus comes from the source.
            i_src = complex(i_inject[k])
        else:
            i_src = complex((src.u_pu - u[k]) / src.z_th_pu)
        result.i_source_pu[src.comp_id] = i_src
        result.s_source_mva[src.comp_id] = complex(
            u[k] * np.conj(i_src)) * pu.s_base

    return result


# --------------------------------------------------------------------------- #
# Thevenin equivalent (foundation for S2)
# --------------------------------------------------------------------------- #


@dataclass
class TheveninResult:
    bus: str
    u_th_pu: complex
    z_th_pu: complex
    z_base_ohm: float
    u_base_kv: float

    @property
    def z_th_ohm(self) -> complex:
        return self.z_th_pu * self.z_base_ohm

    @property
    def short_circuit_current_pu(self) -> complex:
        return self.u_th_pu / self.z_th_pu


def thevenin(net: Network, bus: str, pu: PuModel | None = None) -> TheveninResult:
    """Thevenin equivalent seen from ``bus``.

    ``U_th`` is the open-circuit voltage from the nodal solve; ``Z_th`` is the
    equivalent impedance with the ideal sources shorted (their bus becomes the
    reference) and the Thevenin sources reduced to their impedance.

    Not exposed by the S1 menu yet; it is the entry point for S2.
    """
    pu = pu or to_per_unit(net)
    lf = solve_voltages(net, pu)
    u_th = lf.u_pu[bus]

    Y, index = build_ybus(pu, include_sources=True)
    labels = list(index.keys())
    grounded = [index[s.bus] for s in pu.sources if s.is_ideal]
    if index[bus] in grounded:
        raise TopologyError(
            f"Bus '{bus}' holds an ideal source, so its Thevenin impedance is "
            f"zero by construction. Remove the source or give it a finite "
            f"Thevenin impedance.", [bus])

    keep = [i for i in range(len(labels)) if i not in set(grounded)]
    Yg = Y[np.ix_(keep, keep)]
    sub_labels = [labels[i] for i in keep]
    pos = sub_labels.index(bus)
    Yr = kron_reduce(Yg, [pos], labels=sub_labels)
    z_th = 1.0 / complex(Yr[0, 0])

    return TheveninResult(
        bus=bus, u_th_pu=u_th, z_th_pu=z_th,
        z_base_ohm=pu.bases.z(bus), u_base_kv=pu.bases.u(bus))
