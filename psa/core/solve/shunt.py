"""Shunt conditions at a bus, solved in symmetrical components.

A shunt fault and an unbalanced load are the same problem: something at bus
``k`` imposes three phase relations

    A U_abc + B I_abc = 0,

with ``I_abc`` the currents drawn from the network. Seen from ``k``, each
sequence network is its Thevenin equivalent, ``U_s = U_pre,s - Z_s I_s``, and
``U_abc = T U_s``, ``I_abc = T I_s`` with

    T = [[1, 1, 1], [a^2, a, 1], [a, a^2, 1]],   a = e^{j 120 deg},

in the course's sequence order (1, 2, 0). Substituting gives one 3x3 linear
system ``(B T - A T Z_s) I_s = -A T U_pre,s``. A solid single line-to-ground
fault (``A`` picks ``U_a``, ``B`` picks ``I_b``, ``I_c``) reduces to the textbook
``I_1 = I_2 = I_0 = U_th / (Z_1 + Z_2 + Z_0 + 3 Z_f)``; an unbalanced grounded-
star load is ``A = Y_ph``, ``B = -1``, which is what the S3e solution writes as
``I = -(Z_th + Z_s)^-1 U_pre``.

A sequence in which the bus has no path to ground (``Z = infinity``) carries no
current; its voltage at ``k`` becomes the unknown instead. Every other bus then
follows from one impedance-matrix column per sequence:
``U_n(i) = U_pre,n(i) - Z_n(i, k) I_n``.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field

import numpy as np

from ..errors import TopologyError, ValidationError
from ..model.sequence import NetworkModel
from ..model.stamps import Seq
from .linear import NodalSolution, solve_nodal
from .zbus import ZBus

#: Sequence order of every vector here: positive, negative, zero.
SEQS = (Seq.POS, Seq.NEG, Seq.ZERO)
ALPHA = cmath.exp(2j * math.pi / 3)
#: ``U_abc = T @ U_s`` with ``U_s = (U1, U2, U0)``.
T = np.array([[1, 1, 1], [ALPHA ** 2, ALPHA, 1], [ALPHA, ALPHA ** 2, 1]], dtype=complex)
T_INV = np.linalg.inv(T)


def to_phases(seq: np.ndarray) -> np.ndarray:
    return T @ np.asarray(seq, dtype=complex)


def to_sequences(abc: np.ndarray) -> np.ndarray:
    return T_INV @ np.asarray(abc, dtype=complex)


# --------------------------------------------------------------------------- #
# Sequence networks seen from a bus
# --------------------------------------------------------------------------- #


@dataclass
class SequenceNetworks:
    """The three sequence networks of a model, solved once and reused."""

    model: NetworkModel
    pre: NodalSolution                          # positive-sequence pre-fault state
    zbus: dict[Seq, ZBus]

    @classmethod
    def build(cls, model: NetworkModel, sequences=SEQS) -> SequenceNetworks:
        pos = model.seq(Seq.POS)
        return cls(model=model, pre=solve_nodal(pos),
                   zbus={s: ZBus(model.seq(s)) for s in sequences})

    def u_pre(self, bus: str, s: Seq) -> complex:
        return self.pre.u.get(bus, 0j) if s == Seq.POS else 0j

    def z(self, bus: str, s: Seq) -> complex | None:
        """``Z_kk`` of sequence ``s``, or ``None`` when the bus is open there."""
        zb = self.zbus.get(s)
        if zb is None or zb.is_open(bus):
            return None
        if bus in zb.grounded:
            return 0j                                   # an ideal source's bus
        if bus not in zb.buses:
            return None
        return zb.diagonal(bus)


@dataclass
class SequenceThevenin:
    """``U_th`` and the three sequence impedances (pu) seen from a bus."""

    bus: str
    label: str
    u_th: complex
    z: dict[Seq, complex | None]
    u_base_kv: float
    z_base_ohm: float
    i_base_ka: float
    s_base: float

    def z_text_key(self, s: Seq) -> str:
        return {Seq.POS: "Z_th,1", Seq.NEG: "Z_th,2", Seq.ZERO: "Z_th,0"}[s]


def sequence_thevenin(nets: SequenceNetworks, bus: str) -> SequenceThevenin:
    sm = nets.model.seq(Seq.POS)
    if bus not in sm.buses:
        raise TopologyError(
            f"Bus '{sm.labels.get(bus, bus)}' is not energized in this case.", [bus])
    return SequenceThevenin(
        bus=bus, label=sm.labels.get(bus, bus), u_th=nets.pre.u[bus],
        z={s: nets.z(bus, s) for s in nets.zbus},
        u_base_kv=sm.bases.u(bus), z_base_ohm=sm.bases.z(bus),
        i_base_ka=sm.bases.i(bus), s_base=sm.s_base)


# --------------------------------------------------------------------------- #
# The shunt condition
# --------------------------------------------------------------------------- #


@dataclass
class ShuntConditionResult:
    """Sequence and phase quantities at the bus, and the voltages everywhere."""

    bus: str
    thevenin: SequenceThevenin
    i_s: np.ndarray                             # drawn from the network, (I1, I2, I0)
    u_s: np.ndarray                             # at the bus, during the condition
    u_bus_s: dict[str, np.ndarray] = field(default_factory=dict)   # every bus
    nets: SequenceNetworks | None = None

    @property
    def i_abc(self) -> np.ndarray:
        return to_phases(self.i_s)

    @property
    def u_abc(self) -> np.ndarray:
        return to_phases(self.u_s)

    def u_bus_abc(self, bus: str) -> np.ndarray:
        return to_phases(self.u_bus_s[bus])


def solve_shunt_condition(nets: SequenceNetworks, bus: str,
                          A: np.ndarray, B: np.ndarray) -> ShuntConditionResult:
    """Solve ``A U_abc + B I_abc = 0`` at ``bus`` (``I_abc`` drawn from the network)."""
    th = sequence_thevenin(nets, bus)
    if th.z[Seq.POS] is None:
        raise TopologyError(f"Bus '{th.label}' has no positive-sequence path.", [bus])
    M_U = np.asarray(A, dtype=complex) @ T
    M_I = np.asarray(B, dtype=complex) @ T
    u_pre = np.array([nets.u_pre(bus, s) for s in SEQS])
    coeff = np.zeros((3, 3), dtype=complex)
    const = np.zeros(3, dtype=complex)
    for n, s in enumerate(SEQS):
        z = th.z.get(s)
        if z is None:                               # open: U_n unknown, I_n = 0
            coeff[:, n] = M_U[:, n]
        else:
            coeff[:, n] = M_I[:, n] - M_U[:, n] * z
            const += M_U[:, n] * u_pre[n]
    if abs(np.linalg.det(coeff)) < 1e-300 or not np.isfinite(np.linalg.cond(coeff)) \
            or np.linalg.cond(coeff) > 1e14:
        raise ValidationError(
            f"The condition at bus '{th.label}' cannot be solved: it short-circuits "
            f"an ideal source or leaves the currents undetermined.", [bus])
    x = np.linalg.solve(coeff, -const)
    i_s = np.zeros(3, dtype=complex)
    u_s = np.zeros(3, dtype=complex)
    for n, s in enumerate(SEQS):
        z = th.z.get(s)
        if z is None:
            u_s[n] = x[n]
        else:
            i_s[n] = x[n]
            u_s[n] = u_pre[n] - z * x[n]

    result = ShuntConditionResult(bus=bus, thevenin=th, i_s=i_s, u_s=u_s, nets=nets)
    columns = {s: (nets.zbus[s].column(bus) if th.z.get(s) is not None else {})
               for s in SEQS}
    for b in nets.model.seq(Seq.POS).buses:
        result.u_bus_s[b] = np.array([
            nets.u_pre(b, s) - columns[s].get(b, 0j) * i_s[n]
            for n, s in enumerate(SEQS)])
    result.u_bus_s[bus] = u_s
    return result


# --------------------------------------------------------------------------- #
# Constraint builders
# --------------------------------------------------------------------------- #

FAULT_KINDS = ("3ph", "slg", "ll", "llg")
FAULT_NAMES = {"3ph": "three-phase", "slg": "single line-to-ground (phase a)",
               "ll": "line-to-line (phases b and c)",
               "llg": "double line-to-ground (phases b and c)"}


def fault_constraints(kind: str, zf: complex = 0j, zg: complex = 0j
                      ) -> tuple[np.ndarray, np.ndarray]:
    """``(A, B)`` of a shunt fault, phase a the reference phase."""
    zf, zg = complex(zf), complex(zg)
    if kind == "3ph":
        return np.eye(3, dtype=complex), -zf * np.eye(3, dtype=complex)
    if kind == "slg":                                # U_a = Zf I_a, I_b = I_c = 0
        A = np.array([[1, 0, 0], [0, 0, 0], [0, 0, 0]], dtype=complex)
        B = np.array([[-zf, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=complex)
        return A, B
    if kind == "ll":                                 # I_a = 0, I_b = -I_c, U_b - U_c = Zf I_b
        A = np.array([[0, 0, 0], [0, 0, 0], [0, 1, -1]], dtype=complex)
        B = np.array([[1, 0, 0], [0, 1, 1], [0, -zf, 0]], dtype=complex)
        return A, B
    if kind == "llg":                                # I_a = 0, U_b,c = Zf I_b,c + Zg (I_b + I_c)
        A = np.array([[0, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=complex)
        B = np.array([[1, 0, 0], [0, -zf - zg, -zg], [0, -zg, -zf - zg]], dtype=complex)
        return A, B
    raise ValidationError(f"Unknown fault type '{kind}'.")


def phase_admittance(y: complex, connection: str, factors=(1.0, 1.0, 1.0),
                     zn: complex = 0j) -> np.ndarray:
    """Phase admittance matrix of a load whose balanced per-phase admittance is ``y``.

    ``factors`` scale each phase's admittance (its power at nominal voltage);
    for a delta load they apply to the branches ab, bc and ca. ``zn`` is the
    neutral impedance of a grounded star.
    """
    fa, fb, fc = (float(f) for f in factors)
    if connection == "Y0":
        if zn == 0:
            return np.diag([fa * y, fb * y, fc * y]).astype(complex)
        with np.errstate(divide="ignore"):
            z = np.diag([1.0 / (f * y) if f * y else 1e30 for f in (fa, fb, fc)])
        return np.linalg.inv(z + zn * np.ones((3, 3))).astype(complex)
    if connection == "Y":
        ys = np.array([fa * y, fb * y, fc * y], dtype=complex)
        total = ys.sum()
        if total == 0:
            return np.zeros((3, 3), dtype=complex)
        return np.diag(ys) - np.outer(ys, ys) / total
    if connection == "D":
        # A balanced delta drawing the same power has y/3 per branch.
        yab, ybc, yca = fa * y / 3.0, fb * y / 3.0, fc * y / 3.0
        return np.array([[yab + yca, -yab, -yca],
                         [-yab, yab + ybc, -ybc],
                         [-yca, -ybc, ybc + yca]], dtype=complex)
    raise ValidationError(f"Unknown load connection '{connection}'.")
