"""Element models: the physics of each component class.

Components are plain data (:mod:`psa.core.components`). Everything that turns
them into numbers lives here, one :class:`ElementModel` per class, registered
with :func:`element_model`. Adding a component type means writing its
dataclass and one model; the per-unit conversion, the base propagation and
the Y-bus pick it up through :func:`model_for`.

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
from typing import TYPE_CHECKING, Any, Callable

from ..components import (
    Impedance,
    Line,
    Load,
    Shunt,
    Source,
    Transformer,
    display_name,
)
from ..errors import WARNING, Issue, ValidationError
from .stamps import BranchStamp, ConversionStep, Seq, ShuntStamp, SourceStamp

if TYPE_CHECKING:
    from ..bases import BaseTable

#: Relative tolerance for the "same impedance seen from either side" check.
REFER_RTOL = 1e-9


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


@dataclass
class StampContext:
    """What an element model needs to know about the network it is stamped into."""

    bases: BaseTable
    s_base: float
    seq: Seq = Seq.POS


@dataclass
class Stamps:
    """Everything the element models wrote for one sequence."""

    branches: list[BranchStamp] = field(default_factory=list)
    shunts: list[ShuntStamp] = field(default_factory=list)
    sources: list[SourceStamp] = field(default_factory=list)
    trace: list[ConversionStep] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


class ElementModel:
    """The physics of one component class. Subclasses override what applies."""

    def base_ratio(self, comp: Any, from_bus: str, to_bus: str) -> float:
        """Base-voltage multiplier when crossing ``comp`` from one bus to another."""
        return 1.0

    def stamp(self, comp: Any, ctx: StampContext, out: Stamps) -> None:
        """Append this component's stamps, audit row and issues to ``out``."""
        raise NotImplementedError


ELEMENT_MODELS: dict[type, ElementModel] = {}


def element_model(cls: type) -> Callable[[type], type]:
    """Class decorator registering an :class:`ElementModel` for ``cls``."""

    def register(model_cls: type) -> type:
        ELEMENT_MODELS[cls] = model_cls()
        return model_cls

    return register


def model_for(comp: Any) -> ElementModel:
    for klass in type(comp).__mro__:
        model = ELEMENT_MODELS.get(klass)
        if model is not None:
            return model
    raise ValidationError(
        f"{display_name(comp)}: no element model for component type "
        f"'{type(comp).__name__}'.", [comp.id])


def base_ratio(comp: Any, from_bus: str, to_bus: str) -> float:
    return model_for(comp).base_ratio(comp, from_bus, to_bus)


# --------------------------------------------------------------------------- #
# Conversions (also used directly by tests and reports)
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


def transformer_z_pu(tf: Transformer, bases: BaseTable, s_base: float,
                     zero: bool = False) -> tuple[complex, ConversionStep]:
    """Series impedance in per unit, plus the audit row.

    ``zero`` uses ``r0_pct``/``x0_pct`` where they are given.
    """
    if tf.sn_mva <= 0:
        raise ValidationError(
            f"{display_name(tf)}: rated power must be positive.", [tf.id])
    r_pct = tf.r0_pct if zero and tf.r0_pct is not None else tf.r_pct
    x_pct = tf.x0_pct if zero and tf.x0_pct is not None else tf.x_pct

    def _referred(side: str) -> tuple[complex, str, float]:
        if side == "lv":
            bus, u_n = tf.bus_lv, tf.u2n_kv
        else:
            bus, u_n = tf.bus_hv, tf.u1n_kv
        u_b = bases.u(bus)
        z = complex(r_pct, x_pct) / 100.0 * (s_base / tf.sn_mva) \
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


def line_pu(ln: Line, bases: BaseTable, zero: bool = False
            ) -> tuple[complex, complex, ConversionStep]:
    """``(z_pu, ysh_pu_per_end, step)`` for a PI line.

    ``zero`` gives the zero-sequence PI from the line's ``z0_mode`` data.
    """
    u_from, u_to = bases.u(ln.bus_from), bases.u(ln.bus_to)
    if abs(u_from - u_to) > 1e-9 * max(u_from, u_to):
        raise ValidationError(
            f"{display_name(ln)} spans a change of voltage base "
            f"({u_from:g} kV vs {u_to:g} kV). A line cannot change the base; "
            f"use a transformer.", [ln.id])
    n = max(1, int(ln.n_parallel))
    z_base = bases.z(ln.bus_from)
    r, x, g, b = ln.r_ohm_per_km, ln.x_ohm_per_km, ln.g_s_per_km, ln.b_s_per_km
    if zero and ln.z0_mode == "factors":
        r, x, g, b = (r * ln.r0_factor, x * ln.x0_factor,
                      g * ln.b0_factor, b * ln.b0_factor)
    elif zero:
        r, x, b = ln.r0_ohm_per_km, ln.x0_ohm_per_km, ln.b0_s_per_km
    z_one = complex(r, x) * ln.length_km
    y_one = complex(g, b) * ln.length_km
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


def _issue_once(ctx: StampContext, out: Stamps, issue: Issue) -> None:
    """Element warnings belong to the network, not to each sequence built."""
    if ctx.seq == Seq.POS:
        out.issues.append(issue)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


@element_model(Transformer)
class TransformerModel(ElementModel):
    """Series impedance; in the zero sequence, whatever its windings allow.

    Zero sequence, with ``Zn`` the neutral impedances (3 Zn in the sequence
    network) and no phase shift:

    * YNyn: a series branch ``Z0 + 3 Zn_hv + 3 Zn_lv``;
    * YNd: ``Z0 + 3 Zn_hv`` from the HV bus to earth, the LV side open;
    * Dyn: ``Z0 + 3 Zn_lv`` from the LV bus to earth, the HV side open;
    * anything else (no grounded star facing a delta or another grounded
      star): open.
    """

    def base_ratio(self, comp: Transformer, from_bus: str, to_bus: str) -> float:
        if from_bus == comp.bus_hv:
            return comp.u2n_kv / comp.u1n_kv
        return comp.u1n_kv / comp.u2n_kv

    def stamp(self, comp: Transformer, ctx: StampContext, out: Stamps) -> None:
        zero = ctx.seq == Seq.ZERO
        z_pu, step = transformer_z_pu(comp, ctx.bases, ctx.s_base, zero=zero)
        step.seq = ctx.seq
        if z_pu == 0:
            raise ValidationError(
                f"{step.name} has zero impedance (r = x = 0 %), which is a "
                f"short circuit between its buses.", [comp.id])
        if step.note:
            _issue_once(ctx, out, Issue(
                WARNING, "refer_side", f"{step.name}: {step.note}", [comp.id]))
        if comp.tap != 1.0:
            _issue_once(ctx, out, Issue(
                WARNING, "off_nominal_tap",
                f"{step.name} has an off-nominal tap ({comp.tap:g}); the S1 "
                f"studies model it as a plain series impedance and the "
                f"det(F) = 1 reciprocity check no longer applies.", [comp.id]))
        if not zero:
            out.branches.append(BranchStamp(comp.id, comp.bus_hv, comp.bus_lv, z=z_pu))
            out.trace.append(step)
            return

        try:
            hv, lv = comp.winding_codes
        except ValueError as exc:
            raise ValidationError(f"{step.name}: {exc}.", [comp.id]) from None
        zn_hv = 3.0 * complex(comp.zn_hv_ohm) / ctx.bases.z(comp.bus_hv)
        zn_lv = 3.0 * complex(comp.zn_lv_ohm) / ctx.bases.z(comp.bus_lv)
        if hv == "YN" and lv == "yn":
            z = z_pu + zn_hv + zn_lv
            out.branches.append(BranchStamp(comp.id, comp.bus_hv, comp.bus_lv, z=z))
            note = f"{comp.vector_group}: series Z0 + 3Zn"
        elif hv == "YN" and lv == "d":
            z = z_pu + zn_hv
            out.shunts.append(ShuntStamp(comp.id, comp.bus_hv, 1.0 / z, "transformer"))
            note = f"{comp.vector_group}: Z0 + 3Zn to earth at the HV bus, LV side open"
        elif hv == "D" and lv == "yn":
            z = z_pu + zn_lv
            out.shunts.append(ShuntStamp(comp.id, comp.bus_lv, 1.0 / z, "transformer"))
            note = f"{comp.vector_group}: Z0 + 3Zn to earth at the LV bus, HV side open"
        else:
            z = None
            note = f"{comp.vector_group}: open in the zero sequence"
        step.z_pu = z
        step.z_ohm = None
        step.note = note
        out.trace.append(step)


@element_model(Line)
class LineModel(ElementModel):
    def stamp(self, comp: Line, ctx: StampContext, out: Stamps) -> None:
        zero = ctx.seq == Seq.ZERO
        if zero and comp.z0_mode not in ("factors", "absolute"):
            raise ValidationError(
                f"{display_name(comp)} has no zero-sequence data. Set 'Z0 data' in "
                f"its Zero sequence properties (factors of r, x, b or per-km "
                f"values).", [comp.id])
        z_pu, ysh_pu, step = line_pu(comp, ctx.bases, zero=zero)
        step.seq = ctx.seq
        if z_pu == 0:
            raise ValidationError(
                f"{step.name} has zero series impedance (length "
                f"{comp.length_km:g} km, r + jx = {comp.r_ohm_per_km:g} + "
                f"j{comp.x_ohm_per_km:g} ohm/km), which is a short circuit "
                f"between its buses.", [comp.id])
        out.branches.append(BranchStamp(comp.id, comp.bus_from, comp.bus_to,
                                        z=z_pu, ysh_i=ysh_pu, ysh_j=ysh_pu))
        out.trace.append(step)


def _grounded_star_y(y: complex, zn_pu: complex) -> complex:
    """Zero-sequence admittance of a grounded-star element: ``1/(1/y + 3 Zn)``."""
    if y == 0:
        return 0j
    return 1.0 / (1.0 / y + 3.0 * zn_pu)


@element_model(Load)
class LoadModel(ElementModel):
    def stamp(self, comp: Load, ctx: StampContext, out: Stamps) -> None:
        y_pu, step = load_y_pu(comp, ctx.bases, ctx.s_base)
        step.seq = ctx.seq
        if comp.model != "constant_impedance":
            _issue_once(ctx, out, Issue(
                WARNING, "load_model",
                f"{step.name} is set to '{comp.model}', but these studies "
                f"are linear and model every load as a constant impedance "
                f"at its nominal voltage.", [comp.id]))
        if ctx.seq == Seq.ZERO:
            if comp.connection == "Y0":
                y_pu = _grounded_star_y(y_pu, complex(comp.zn_ohm) / ctx.bases.z(comp.bus))
                step.note = "Y0: grounded star" + (" through Zn" if comp.zn_ohm else "")
            else:
                step.note = f"{comp.connection}: open in the zero sequence"
                step.y_pu = step.z_pu = None
                out.trace.append(step)
                return
            step.y_pu = y_pu
            step.z_pu = 1.0 / y_pu if y_pu else None
        out.shunts.append(ShuntStamp(comp.id, comp.bus, y_pu, "load"))
        out.trace.append(step)


@element_model(Shunt)
class ShuntModel(ElementModel):
    def stamp(self, comp: Shunt, ctx: StampContext, out: Stamps) -> None:
        y_pu, step = shunt_y_pu(comp, ctx.bases, ctx.s_base)
        step.seq = ctx.seq
        if ctx.seq == Seq.ZERO and comp.connection != "Y0":
            step.note = f"{comp.connection}: open in the zero sequence"
            step.y_pu = None
            out.trace.append(step)
            return
        out.shunts.append(ShuntStamp(comp.id, comp.bus, y_pu, "shunt"))
        out.trace.append(step)


@element_model(Source)
class ExternalGridModel(ElementModel):
    """A source behind ``Z1``; behind ``Z2 = Z1`` and ``Z0`` without voltage."""

    def stamp(self, comp: Source, ctx: StampContext, out: Stamps) -> None:
        bus = comp.bus
        z_base = ctx.bases.z(bus)
        try:
            z_ohm = comp.impedance_ohm(ctx.bases.u(bus))
            z0_ohm = comp.impedance0_ohm(ctx.bases.u(bus)) if ctx.seq == Seq.ZERO else None
        except ValueError as exc:
            raise ValidationError(f"{display_name(comp)}: {exc}.", [comp.id]) from None
        z_th_pu = z_ohm / z_base
        e = comp.u_pu * complex(math.cos(math.radians(comp.theta_deg)),
                                math.sin(math.radians(comp.theta_deg)))
        if comp.z_mode == "ik":
            note = (f"Thevenin equivalent from Ik = {comp.ik_ka:g} kA, "
                    f"cos phi = {comp.cos_phi:g}")
        elif comp.z_mode == "sk":
            note = (f"Thevenin equivalent from Sk = {comp.sk_mva:g} MVA, "
                    f"cos phi = {comp.cos_phi:g}")
        else:
            note = "ideal voltage source" if z_th_pu == 0 else "Thevenin equivalent"

        if ctx.seq == Seq.POS:
            out.sources.append(SourceStamp(comp.id, bus, e, z_th_pu, comp.is_slack))
        elif ctx.seq == Seq.NEG:
            out.sources.append(SourceStamp(comp.id, bus, 0j, z_th_pu, comp.is_slack))
            note = "Z2 = Z1, no source voltage"
        else:
            if z0_ohm is None:
                note = "no zero-sequence path (Z0 not given)"
                z_ohm = z_th_pu = 0j
            else:
                z_ohm = z0_ohm
                z_th_pu = z0_ohm / z_base
                out.sources.append(SourceStamp(comp.id, bus, 0j, z_th_pu, comp.is_slack))
                note = {"ik1": f"Z0 = 3U/Ik1 - 2Z1, Ik1 = {comp.ik1_ka:g} kA, "
                               f"cos phi = {comp.cos_phi0:g}",
                        "z0_z1": f"Z0 = {comp.z0_z1:g} Z1"}.get(comp.z0_mode, "Z0 given")
        out.trace.append(ConversionStep(
            comp_id=comp.id, name=display_name(comp), comp_type="source",
            bus_ref=bus, u_base_kv=ctx.bases.u(bus), z_base_ohm=z_base,
            u_nominal_kv=comp.un_kv if comp.z_mode != "z_ohm" else None,
            z_pu=z_th_pu if z_th_pu != 0 else None,
            z_ohm=z_ohm if z_ohm != 0 else None,
            note=note, seq=ctx.seq,
        ))


@element_model(Impedance)
class ImpedanceModel(ElementModel):
    """The same impedance in every sequence."""

    def stamp(self, comp: Impedance, ctx: StampContext, out: Stamps) -> None:
        bus = comp.bus_from
        z_base = ctx.bases.z(bus)
        z_raw = complex(comp.r, comp.x)
        z_pu = z_raw if comp.in_pu else z_raw / z_base
        if z_pu == 0:
            raise ValidationError(
                f"{display_name(comp)} has zero impedance.", [comp.id])
        step = ConversionStep(
            comp_id=comp.id, name=display_name(comp), comp_type="impedance",
            bus_ref=bus, u_base_kv=ctx.bases.u(bus), z_base_ohm=z_base,
            z_pu=z_pu, z_ohm=z_pu * z_base, seq=ctx.seq,
        )
        if comp.bus_to:
            out.branches.append(BranchStamp(comp.id, comp.bus_from, comp.bus_to,
                                            z=z_pu))
        else:
            out.shunts.append(ShuntStamp(comp.id, bus, 1.0 / z_pu, "impedance"))
            step.y_pu = 1.0 / z_pu
        out.trace.append(step)
