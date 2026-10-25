"""Newton-Raphson load flow in polar coordinates, the way the course writes it.

The bus types follow from what is connected (course naming): a bus with a
slack device is ``Slack`` (U and the angle given), one with a voltage
controller (a PV generator, an SVC) is ``PU`` (P and U given), every other
bus ``PQ``. Constant-impedance loads and constant-B shunts are part of the
admittance matrix; constant-power loads, constant-Q shunts and generators are
injections (:class:`~psa.core.model.stamps.Injection`).

With ``theta`` the angles and ``U`` the magnitudes, the mismatches at the
solution are zero::

    dP = P_spec - P(theta, U)      (PU and PQ buses)
    dQ = Q_spec - Q(theta, U)      (PQ buses)

and each iteration solves the linearised equations for the angle and the
relative magnitude corrections::

    [dP]   [H  N] [d theta]
    [dQ] = [J  L] [dU / U ]        theta += d theta,  U *= 1 + dU/U

with ``H = dP/dtheta``, ``N = U dP/dU``, ``J = dQ/dtheta``, ``L = U dQ/dU``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import networkx as nx
import numpy as np

from ..errors import TopologyError, ValidationError
from ..model.sequence import NetworkModel, SequenceModel
from ..model.stamps import Injection, Seq
from .linear import BranchCurrents, NodalSolution

SLACK, PU, PQ = "Slack", "PU", "PQ"

#: Default convergence limit on the largest power mismatch, in pu.
TOLERANCE = 1e-10
MAX_ITERATIONS = 20
#: Rounds of PU-to-PQ switching when Q limits are enforced.
MAX_LIMIT_ROUNDS = 10


@dataclass
class Iteration:
    """One row of the iteration log: the mismatch before the correction."""

    number: int
    max_dp: float
    max_dq: float
    worst_bus: str
    max_step: float = 0.0           # largest |d theta| or |dU/U| applied


@dataclass
class DeviceFlow:
    """What one injection ends up delivering, in pu (generation positive)."""

    inj: Injection
    s: complex
    bus_type: str


@dataclass
class LoadFlowSolution:
    """Converged (or not) state of a load flow, and what follows from it."""

    sm: SequenceModel
    buses: list[str]
    types: dict[str, str]
    u: dict[str, complex] = field(default_factory=dict)
    converged: bool = False
    iterations: int = 0
    log: list[Iteration] = field(default_factory=list)
    #: The bus admittance matrix the equations use (sources excluded).
    Y: np.ndarray | None = None
    #: The Jacobian at the flat start and at the last state, with labels.
    jacobian_0: np.ndarray | None = None
    jacobian: np.ndarray | None = None
    row_labels: list[str] = field(default_factory=list)
    col_labels: list[str] = field(default_factory=list)
    devices: list[DeviceFlow] = field(default_factory=list)
    #: PU buses turned into PQ buses at a reactive limit, ``{bus: injection id}``.
    limited: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def max_mismatch(self) -> float:
        return max(self.log[-1].max_dp, self.log[-1].max_dq) if self.log else math.inf

    def s_bus(self) -> dict[str, complex]:
        """Complex power injected into the network at every bus, ``U conj(Y U)``."""
        u = np.array([self.u[b] for b in self.buses])
        s = u * np.conj(self.Y @ u)
        return {b: complex(s[k]) for k, b in enumerate(self.buses)}

    def branch_flows(self) -> list[BranchCurrents]:
        return NodalSolution(self.sm, u=self.u).branch_currents()

    def losses(self) -> complex:
        """Sum of the branch losses (pu): series ``I^2 Z`` plus line charging."""
        return sum((br.losses for br in self.branch_flows()), 0j)

    def shunt_power(self) -> dict[str, complex]:
        """Power (pu) every shunt stamp takes from its bus, ``|U|^2 conj(y)``."""
        out: dict[str, complex] = {}
        for sh in self.sm.shunts:
            if sh.bus in self.u:
                out[sh.comp_id] = out.get(sh.comp_id, 0j) + abs(self.u[sh.bus]) ** 2 * np.conj(sh.y)
        return out


# --------------------------------------------------------------------------- #
# The equations
# --------------------------------------------------------------------------- #


def bus_powers(Y: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Complex power injected at every bus, ``S = U conj(Y U)``."""
    return u * np.conj(Y @ u)


def jacobian(Y: np.ndarray, u: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The four blocks ``H, N, J, L`` over all buses, at the voltages ``u``.

    ``H = dP/dtheta`` and ``J = dQ/dtheta`` are the real and imaginary parts of
    ``j diag(U) conj(diag(I) - Y diag(U))``; ``N = U dP/dU`` and ``L = U dQ/dU``
    those of ``diag(U) conj(Y diag(U)) + diag(U conj(I))``, with ``I = Y U``.
    Element by element they are the course formulas, e.g.
    ``H_kk = -Q_k - B_kk U_k^2`` and ``N_kk = P_k + G_kk U_k^2``.
    """
    i = Y @ u
    du = np.diag(u)
    ds_dtheta = 1j * du @ np.conj(np.diag(i) - Y @ du)
    ds_du = du @ np.conj(Y @ du) + np.diag(u * np.conj(i))
    return ds_dtheta.real, ds_du.real, ds_dtheta.imag, ds_du.imag


def _reduced_jacobian(Y, u, ang, mag) -> np.ndarray:
    H, N, J, L = jacobian(Y, u)
    return np.block([[H[np.ix_(ang, ang)], N[np.ix_(ang, mag)]],
                     [J[np.ix_(mag, ang)], L[np.ix_(mag, mag)]]])


# --------------------------------------------------------------------------- #
# Bus types
# --------------------------------------------------------------------------- #


def _islands(sm: SequenceModel, buses: list[str]) -> list[set[str]]:
    g = nx.Graph()
    g.add_nodes_from(buses)
    for br in sm.branches:
        if br.bus_i in g and br.bus_j in g:
            g.add_edge(br.bus_i, br.bus_j)
    return [set(c) for c in nx.connected_components(g)]


def classify(sm: SequenceModel, buses: list[str], injections: list[Injection],
             limited: dict[str, str] | None = None) -> dict[str, str]:
    """Bus types from the connected devices; raises when an island has no slack."""
    limited = limited or {}
    roles: dict[str, set[str]] = {b: set() for b in buses}
    for inj in injections:
        if inj.bus in roles:
            roles[inj.bus].add(inj.role)
    types = {}
    for b in buses:
        if "slack" in roles[b]:
            types[b] = SLACK
        elif "pv" in roles[b] and b not in limited:
            types[b] = PU
        else:
            types[b] = PQ
    for island in _islands(sm, buses):
        slack = sorted(b for b in island if types[b] == SLACK)
        names = ", ".join(sm.labels.get(b, b) for b in sorted(island, key=buses.index))
        if not slack:
            raise TopologyError(
                f"The island with buses {names} has no slack bus: connect an "
                f"external grid or set a generator's control to slack.", sorted(island))
        if len(slack) > 1:
            raise TopologyError(
                f"The island with buses {names} has {len(slack)} slack buses "
                f"({', '.join(sm.labels.get(b, b) for b in slack)}); a load flow "
                f"needs exactly one per island.", slack)
    return types


def _setpoints(sm: SequenceModel, injections: list[Injection]) -> dict[str, tuple[float, float]]:
    """``{bus: (U, theta_deg)}`` of the voltage-controlled buses; setpoints must agree."""
    out: dict[str, tuple[float, float, str]] = {}
    for inj in injections:
        if inj.role not in ("slack", "pv"):
            continue
        prev = out.get(inj.bus)
        theta = inj.theta_deg if inj.role == "slack" else None
        if prev is None:
            out[inj.bus] = (inj.u_set, theta, inj.comp_id)
            continue
        if abs(prev[0] - inj.u_set) > 1e-9 or (
                theta is not None and prev[1] is not None and abs(prev[1] - theta) > 1e-9):
            raise ValidationError(
                f"Conflicting voltage setpoints at bus {sm.labels.get(inj.bus, inj.bus)}: "
                f"{prev[2]} and {inj.comp_id} ask for different values.",
                [prev[2], inj.comp_id])
        if prev[1] is None and theta is not None:
            out[inj.bus] = (prev[0], theta, prev[2])
    return {b: (u, t or 0.0) for b, (u, t, _) in out.items()}


# --------------------------------------------------------------------------- #
# Solver
# --------------------------------------------------------------------------- #


def _newton(Y, u, types, buses, p_spec, q_spec, tol, max_iter, log, offset=0):
    """Iterate from ``u`` in place; returns (converged, Jacobian at the start, last)."""
    ang = [k for k, b in enumerate(buses) if types[b] != SLACK]
    mag = [k for k, b in enumerate(buses) if types[b] == PQ]
    j_first = j_last = None
    for it in range(max_iter + 1):
        s = bus_powers(Y, u)
        dp = p_spec[ang] - s.real[ang]
        dq = q_spec[mag] - s.imag[mag]
        mis = np.concatenate([dp, dq])
        worst = ""
        if mis.size:
            k = int(np.argmax(np.abs(mis)))
            worst = buses[ang[k]] if k < len(ang) else buses[mag[k - len(ang)]]
        log.append(Iteration(offset + it, float(np.max(np.abs(dp), initial=0.0)),
                             float(np.max(np.abs(dq), initial=0.0)), worst))
        if not np.all(np.isfinite(mis)):
            return False, j_first, j_last
        j_last = _reduced_jacobian(Y, u, ang, mag)
        if j_first is None:
            j_first = j_last
        if np.max(np.abs(mis), initial=0.0) < tol:
            return True, j_first, j_last
        if it == max_iter:
            break
        try:
            dx = np.linalg.solve(j_last, mis)
        except np.linalg.LinAlgError:
            return False, j_first, j_last
        theta = np.angle(u)
        mags = np.abs(u)
        theta[ang] += dx[: len(ang)]
        mags[mag] *= 1.0 + dx[len(ang):]
        u[:] = mags * np.exp(1j * theta)
        log[-1].max_step = float(np.max(np.abs(dx), initial=0.0))
    return False, j_first, j_last


def solve_load_flow(model: NetworkModel, tol: float = TOLERANCE,
                    max_iter: int = MAX_ITERATIONS,
                    q_limits: bool = False) -> LoadFlowSolution:
    """Newton-Raphson load flow of ``model`` from a flat start.

    ``model`` should be built with the load and shunt models the study wants
    (``ModelOptions(load_model="as_set", shunt_model="as_set")`` honours each
    component's own). With ``q_limits`` a PU bus whose devices would exceed
    their reactive limits becomes a PQ bus held at the limit, and the flow is
    solved again from the current state (no switching back).
    """
    sm = model.seq(Seq.POS)
    buses = list(sm.buses)
    injections = model.injections()
    if not buses:
        raise TopologyError("There are no energized buses to solve.")
    Y, index = sm.ybus("exclude", bus_order=buses)
    setpoints = _setpoints(sm, injections)
    limited: dict[str, str] = {}
    types = classify(sm, buses, injections)
    sol = LoadFlowSolution(sm, buses, types, Y=Y)

    pv_at_slack = [i for i in injections if i.role == "pv" and types.get(i.bus) == SLACK]
    for inj in pv_at_slack:
        sol.warnings.append(
            f"{inj.comp_id} controls the voltage of the slack bus; it keeps its P and "
            f"the slack takes the reactive power.")

    # Flat start: 1 pu and 0 degrees, except the given voltages
    u = np.ones(len(buses), dtype=complex)
    for b, (u_set, theta) in setpoints.items():
        if b in index:
            th = math.radians(theta) if types[b] == SLACK else 0.0
            u[index[b]] = u_set * complex(math.cos(th), math.sin(th))

    log: list[Iteration] = []
    for _ in range(MAX_LIMIT_ROUNDS):
        p_spec = np.zeros(len(buses))
        q_spec = np.zeros(len(buses))
        for inj in injections:
            k = index.get(inj.bus)
            if k is None or inj.role == "slack":
                continue
            p_spec[k] += inj.p
            if inj.role == "pq":
                q_spec[k] += inj.q
            elif inj.bus in limited:
                q_spec[k] += _limit_value(inj, limited[inj.bus])
        # Restore the magnitude of a PU bus at its setpoint
        for b in buses:
            if types[b] == PU:
                k = index[b]
                u[k] = setpoints[b][0] * u[k] / abs(u[k])
        ok, j0, jl = _newton(Y, u, types, buses, p_spec, q_spec, tol, max_iter, log,
                             offset=len(log))
        if sol.jacobian_0 is None:
            sol.jacobian_0 = j0
        sol.jacobian = jl
        sol.converged = ok
        sol.u = {b: complex(u[index[b]]) for b in buses}
        sol.types = types
        sol.devices = _device_flows(sol, injections)
        if not (ok and q_limits):
            break
        hit = _limit_violations(sol)
        if not hit:
            break
        limited.update(hit)
        for b, which in hit.items():
            sol.warnings.append(
                f"Bus {sm.labels.get(b, b)} reached its {which} reactive limit and "
                f"becomes a PQ bus.")
        types = classify(sm, buses, injections, limited)
    sol.limited = limited
    sol.iterations = len(log) - 1
    sol.log = log
    sol.row_labels = ([f"P {sm.labels.get(b, b)}" for b in buses if sol.types[b] != SLACK]
                      + [f"Q {sm.labels.get(b, b)}" for b in buses if sol.types[b] == PQ])
    sol.col_labels = ([f"theta {sm.labels.get(b, b)}" for b in buses if sol.types[b] != SLACK]
                      + [f"U/U {sm.labels.get(b, b)}" for b in buses if sol.types[b] == PQ])
    return sol


def _limit_value(inj: Injection, which: str) -> float:
    """Reactive power of a device held at its limit (``min`` or ``max``)."""
    return (inj.q_min if which == "min" else inj.q_max) or 0.0


def _device_flows(sol: LoadFlowSolution, injections: list[Injection]) -> list[DeviceFlow]:
    """Share each bus's computed injection among its devices.

    Fixed injections keep their values. What remains at a slack bus goes to its
    slack devices, and the reactive remainder at a PU bus to its voltage
    controllers, split equally when there are several.
    """
    s_bus = sol.s_bus()
    out = []
    by_bus: dict[str, list[Injection]] = {}
    for inj in injections:
        if inj.bus in sol.types:
            by_bus.setdefault(inj.bus, []).append(inj)
    for bus, injs in by_bus.items():
        t = sol.types[bus]
        fixed = 0j
        free = []
        for inj in injs:
            if inj.role == "pq":
                fixed += complex(inj.p, inj.q)
            elif t == SLACK and inj.role == "slack":
                free.append(inj)
            elif t == SLACK:                     # a PV device at the slack bus
                fixed += complex(inj.p, 0.0)
            elif bus in sol.limited:
                fixed += complex(inj.p, _limit_value(inj, sol.limited[bus]))
            else:
                free.append(inj)
        rest = s_bus[bus] - fixed
        for inj in injs:
            if inj in free:
                share = rest / len(free)
                s = share if t == SLACK else complex(inj.p, share.imag)
            elif inj.role == "pq":
                s = complex(inj.p, inj.q)
            elif t == SLACK:
                s = complex(inj.p, 0.0)
            else:
                s = complex(inj.p, _limit_value(inj, sol.limited[bus]))
            out.append(DeviceFlow(inj, s, t))
    return out


def _limit_violations(sol: LoadFlowSolution) -> dict[str, str]:
    """PU buses whose voltage controllers exceed their combined reactive limits."""
    q_bus: dict[str, float] = {}
    lo: dict[str, float] = {}
    hi: dict[str, float] = {}
    for d in sol.devices:
        inj = d.inj
        if inj.role != "pv" or sol.types.get(inj.bus) != PU:
            continue
        q_bus[inj.bus] = q_bus.get(inj.bus, 0.0) + d.s.imag
        lo[inj.bus] = lo.get(inj.bus, 0.0) + (-math.inf if inj.q_min is None else inj.q_min)
        hi[inj.bus] = hi.get(inj.bus, 0.0) + (math.inf if inj.q_max is None else inj.q_max)
    out = {}
    for b, q in q_bus.items():
        if q > hi[b] + 1e-9:
            out[b] = "max"
        elif q < lo[b] - 1e-9:
            out[b] = "min"
    return out
