"""Conversion of a :class:`~psa.core.network.Network` into per-unit branches.

Everything downstream (Y-bus, two-port, load flow) works on the :class:`PuModel`
produced here, so this module is the single place where physical units turn
into per-unit numbers.

Formulas
--------
Transformer, referred to ``referred_side`` (default LV)::

    z_pu = (r_pct + j*x_pct)/100 * (S_base/S_n) * (U_n_side / U_base_side)**2

Line, ``n`` identical lines in parallel, PI equivalent::

    z_1    = (r + jx) * L                 ohm, ONE line
    y_1    = (g + jb) * L                 S,   ONE line, TOTAL charging
    z_pu   = z_1 / n / z_base
    ysh_pu = n * y_1 / 2 * z_base         at EACH end of the PI

Impedance load at its own nominal voltage::

    S      = P/cos_phi * (cos_phi +- j*sin_phi)          MVA
    y_pu   = conj(S)/S_base * (U_base/U_n)**2

The load admittance is built directly rather than as ``1/Z`` so that a zero
load degrades to an open circuit instead of dividing by zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .bases import BaseTable, propagate_bases
from .components import (
    Impedance,
    Line,
    Load,
    Shunt,
    Source,
    Transformer,
    buses_of,
    display_name,
)
from .errors import ERROR, WARNING, Issue, ValidationError
from .network import Network

#: Relative tolerance for the "same impedance seen from either side" check.
REFER_RTOL = 1e-9


# --------------------------------------------------------------------------- #
# Per-unit primitives
# --------------------------------------------------------------------------- #


@dataclass
class PuSeries:
    """A series branch between two buses, with optional PI half-shunts."""

    comp_id: str
    bus_i: str
    bus_j: str
    z_pu: complex
    ysh_pu: complex = 0j        # at EACH end

    @property
    def y_pu(self) -> complex:
        return 1.0 / self.z_pu


@dataclass
class PuShunt:
    """An admittance from one bus to ground."""

    comp_id: str
    bus: str
    y_pu: complex
    kind: str = "load"          # load | shunt | impedance | source_th


@dataclass
class PuSource:
    """A voltage source. Not a passive element, handled per study."""

    comp_id: str
    bus: str
    u_pu: complex               # magnitude and angle combined
    z_th_pu: complex
    is_slack: bool

    @property
    def is_ideal(self) -> bool:
        return self.z_th_pu == 0


@dataclass
class ConversionStep:
    """One row of the "component values in per unit" results table."""

    comp_id: str
    name: str
    comp_type: str
    bus_ref: str = ""
    u_base_kv: float = float("nan")
    z_base_ohm: float = float("nan")
    u_nominal_kv: float | None = None
    base_factor: float = 1.0        # (U_base / U_nominal)**2, must be auditable
    z_pu: complex | None = None
    ysh_pu: complex | None = None
    y_pu: complex | None = None
    s_mva: complex | None = None
    z_ohm: complex | None = None
    note: str = ""


@dataclass
class PuModel:
    """The whole network expressed in per unit."""

    s_base: float
    bases: BaseTable
    series: list[PuSeries] = field(default_factory=list)
    shunts: list[PuShunt] = field(default_factory=list)
    sources: list[PuSource] = field(default_factory=list)
    trace: list[ConversionStep] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    buses: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def step(self, comp_id: str) -> ConversionStep | None:
        for s in self.trace:
            if s.comp_id == comp_id:
                return s
        return None

    def series_of(self, comp_id: str) -> PuSeries | None:
        for s in self.series:
            if s.comp_id == comp_id:
                return s
        return None

    def shunt_of(self, comp_id: str) -> PuShunt | None:
        for s in self.shunts:
            if s.comp_id == comp_id:
                return s
        return None


# --------------------------------------------------------------------------- #
# Component conversions
# --------------------------------------------------------------------------- #


def load_power_mva(load: Load) -> complex:
    """Complex power of an impedance load, ``lag`` giving inductive (+jQ)."""
    cos_phi = load.cos_phi
    if not 0.0 < cos_phi <= 1.0:
        raise ValidationError(
            f"{display_name(load)}: cos phi must be in (0, 1], got {cos_phi}.",
            [load.id])
    sin_phi = math.sqrt(max(0.0, 1.0 - cos_phi * cos_phi))
    sign = 1.0 if load.pf_sign == "lag" else -1.0
    return load.p_mw / cos_phi * complex(cos_phi, sign * sin_phi)


def transformer_z_pu(tf: Transformer, bases: BaseTable, s_base: float
                     ) -> tuple[complex, ConversionStep]:
    """Series impedance in per unit, plus the audit row."""
    if tf.sn_mva <= 0:
        raise ValidationError(
            f"{display_name(tf)}: rated power must be positive.", [tf.id])

    def _referred(side: str) -> tuple[complex, str, float]:
        if side == "lv":
            bus, u_n = tf.bus_lv, tf.u2n_kv
        else:
            bus, u_n = tf.bus_hv, tf.u1n_kv
        u_b = bases.u(bus)
        z = complex(tf.r_pct, tf.x_pct) / 100.0 * (s_base / tf.sn_mva) \
            * (u_n / u_b) ** 2
        return z, bus, u_b

    z_pu, bus, u_b = _referred(tf.referred_side)
    other = "hv" if tf.referred_side == "lv" else "lv"
    note = ""
    try:
        z_other, _, _ = _referred(other)
    except KeyError:
        z_other = z_pu
    if abs(z_other - z_pu) > REFER_RTOL * max(abs(z_other), abs(z_pu), 1e-30):
        note = (f"referred to {tf.referred_side.upper()}; the {other.upper()} "
                f"side would give {z_other:.6g} pu (pinned bases disagree)")

    step = ConversionStep(
        comp_id=tf.id, name=display_name(tf), comp_type="transformer",
        bus_ref=bus, u_base_kv=u_b, z_base_ohm=bases.z(bus),
        u_nominal_kv=tf.u2n_kv if tf.referred_side == "lv" else tf.u1n_kv,
        base_factor=((tf.u2n_kv if tf.referred_side == "lv" else tf.u1n_kv) / u_b) ** 2,
        z_pu=z_pu, z_ohm=z_pu * bases.z(bus), note=note,
    )
    return z_pu, step


def line_pu(ln: Line, bases: BaseTable) -> tuple[complex, complex, ConversionStep]:
    """``(z_pu, ysh_pu_per_end, step)`` for a PI line."""
    u_from, u_to = bases.u(ln.bus_from), bases.u(ln.bus_to)
    if abs(u_from - u_to) > 1e-9 * max(u_from, u_to):
        raise ValidationError(
            f"{display_name(ln)} spans a change of voltage base "
            f"({u_from:g} kV vs {u_to:g} kV). A line cannot change the base; "
            f"use a transformer.", [ln.id])
    n = max(1, int(ln.n_parallel))
    z_base = bases.z(ln.bus_from)
    z_one = complex(ln.r_ohm_per_km, ln.x_ohm_per_km) * ln.length_km
    y_one = complex(ln.g_s_per_km, ln.b_s_per_km) * ln.length_km
    z_pu = z_one / n / z_base
    ysh_pu = n * y_one / 2.0 * z_base
    step = ConversionStep(
        comp_id=ln.id, name=display_name(ln), comp_type="line",
        bus_ref=ln.bus_from, u_base_kv=u_from, z_base_ohm=z_base,
        z_pu=z_pu, ysh_pu=ysh_pu, z_ohm=z_one / n,
        note=f"{n} parallel line(s), {ln.length_km:g} km" if n > 1
             else f"{ln.length_km:g} km",
    )
    return z_pu, ysh_pu, step


def load_y_pu(ld: Load, bases: BaseTable, s_base: float
              ) -> tuple[complex, ConversionStep]:
    """Shunt admittance in per unit, plus the audit row."""
    u_b = bases.u(ld.bus)
    u_n = ld.u_nominal_kv or u_b
    s_mva = load_power_mva(ld)
    factor = (u_b / u_n) ** 2
    y_pu = s_mva.conjugate() / s_base * factor
    z_pu = (1.0 / y_pu) if y_pu != 0 else None
    step = ConversionStep(
        comp_id=ld.id, name=display_name(ld), comp_type="load",
        bus_ref=ld.bus, u_base_kv=u_b, z_base_ohm=bases.z(ld.bus),
        u_nominal_kv=u_n, base_factor=factor,
        y_pu=y_pu, z_pu=z_pu, s_mva=s_mva,
        z_ohm=(u_n * u_n / s_mva.conjugate()) if s_mva != 0 else None,
    )
    return y_pu, step


def shunt_y_pu(sh: Shunt, bases: BaseTable, s_base: float
               ) -> tuple[complex, ConversionStep]:
    u_b = bases.u(sh.bus)
    u_n = sh.u_nominal_kv or u_b
    factor = (u_b / u_n) ** 2
    y_pu = 1j * sh.q_mvar / s_base * factor
    step = ConversionStep(
        comp_id=sh.id, name=display_name(sh), comp_type="shunt",
        bus_ref=sh.bus, u_base_kv=u_b, z_base_ohm=bases.z(sh.bus),
        u_nominal_kv=u_n, base_factor=factor, y_pu=y_pu,
        s_mva=complex(0.0, -sh.q_mvar),
        note="capacitive" if sh.q_mvar >= 0 else "inductive (reactor)",
    )
    return y_pu, step


# --------------------------------------------------------------------------- #
# Whole-network conversion
# --------------------------------------------------------------------------- #


def to_per_unit(net: Network, bases: BaseTable | None = None,
                exclude: set[str] | None = None) -> PuModel:
    """Convert every active component of ``net`` into per-unit primitives."""
    net.require_valid()
    bases = bases or propagate_bases(net)
    base_issues = bases.issues()
    if any(i.is_error for i in base_issues):
        raise ValidationError(
            "Per-unit bases could not be established:\n  "
            + "\n  ".join(str(i) for i in base_issues),
            [i for issue in base_issues for i in issue.ids])

    model = PuModel(s_base=net.s_base_mva, bases=bases, issues=list(base_issues),
                    labels={b.id: display_name(b) for b in net.buses.values()},
                    buses=[b for b in net.active_bus_ids()
                           if b in bases.u_base_kv])

    for comp in net.active_components(exclude):
        if isinstance(comp, Transformer):
            z_pu, step = transformer_z_pu(comp, bases, net.s_base_mva)
            model.series.append(PuSeries(comp.id, comp.bus_hv, comp.bus_lv, z_pu))
            model.trace.append(step)
            if step.note:
                model.issues.append(Issue(
                    WARNING, "refer_side", f"{step.name}: {step.note}", [comp.id]))
            if comp.tap != 1.0:
                model.issues.append(Issue(
                    WARNING, "off_nominal_tap",
                    f"{step.name} has an off-nominal tap ({comp.tap:g}); the S1 "
                    f"studies model it as a plain series impedance and the "
                    f"det(F) = 1 reciprocity check no longer applies.", [comp.id]))

        elif isinstance(comp, Line):
            z_pu, ysh_pu, step = line_pu(comp, bases)
            model.series.append(
                PuSeries(comp.id, comp.bus_from, comp.bus_to, z_pu, ysh_pu))
            model.trace.append(step)

        elif isinstance(comp, Load):
            y_pu, step = load_y_pu(comp, bases, net.s_base_mva)
            model.shunts.append(PuShunt(comp.id, comp.bus, y_pu, "load"))
            model.trace.append(step)
            if comp.model != "constant_impedance":
                model.issues.append(Issue(
                    WARNING, "load_model",
                    f"{step.name} is set to '{comp.model}', but these studies "
                    f"are linear and model every load as a constant impedance "
                    f"at its nominal voltage.", [comp.id]))

        elif isinstance(comp, Shunt):
            y_pu, step = shunt_y_pu(comp, bases, net.s_base_mva)
            model.shunts.append(PuShunt(comp.id, comp.bus, y_pu, "shunt"))
            model.trace.append(step)

        elif isinstance(comp, Source):
            bus = comp.bus
            z_base = bases.z(bus)
            z_th_pu = complex(comp.z_th_ohm) / z_base
            u = comp.u_pu * complex(math.cos(math.radians(comp.theta_deg)),
                                    math.sin(math.radians(comp.theta_deg)))
            model.sources.append(
                PuSource(comp.id, bus, u, z_th_pu, comp.is_slack))
            model.trace.append(ConversionStep(
                comp_id=comp.id, name=display_name(comp), comp_type="source",
                bus_ref=bus, u_base_kv=bases.u(bus), z_base_ohm=z_base,
                z_pu=z_th_pu if z_th_pu != 0 else None,
                note="ideal voltage source" if z_th_pu == 0
                     else "Thevenin equivalent",
            ))

        elif isinstance(comp, Impedance):
            bus = comp.bus_from
            z_base = bases.z(bus)
            z_raw = complex(comp.r, comp.x)
            z_pu = z_raw if comp.in_pu else z_raw / z_base
            if z_pu == 0:
                raise ValidationError(
                    f"{display_name(comp)} has zero impedance.", [comp.id])
            step = ConversionStep(
                comp_id=comp.id, name=display_name(comp), comp_type="impedance",
                bus_ref=bus, u_base_kv=bases.u(bus), z_base_ohm=z_base,
                z_pu=z_pu, z_ohm=z_pu * z_base,
            )
            if comp.bus_to:
                model.series.append(
                    PuSeries(comp.id, comp.bus_from, comp.bus_to, z_pu))
            else:
                model.shunts.append(
                    PuShunt(comp.id, bus, 1.0 / z_pu, "impedance"))
                step.y_pu = 1.0 / z_pu
            model.trace.append(step)

    return model
