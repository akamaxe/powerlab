"""Power system component definitions.

Design rules that the rest of the package depends on:

* Every component stores **plain floats only**. No geometry (that lives in the
  project's ``layout`` section) and no expressions (those live in
  :mod:`psa.core.params`), so the core stays a pure, headlessly testable
  physics library.
* Bus references are plain string ids held in the attributes that each class
  lists in ``TERMINALS``. That is the single source of truth for
  connectivity; ``BUS_FIELDS`` is derived from it, in the same order.
* The physics of each class (per-unit conversion, admittance stamps) lives in
  :mod:`psa.core.model.elements`, not here.
* ``FIELDS`` drives the GUI property editor, so adding a parameter is a
  one-line change that automatically shows up in the UI.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, fields as dc_fields
from typing import Any, ClassVar

# --------------------------------------------------------------------------- #
# Field metadata (drives the property editor and the study option dialogs)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldSpec:
    """UI and validation metadata for one component attribute."""

    name: str
    label: str
    kind: str = "float"          # float | int | str | bool | bus | choice | complex
    unit: str = ""
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    tooltip: str = ""
    group: str = "General"
    parametric: bool = True      # may be bound to an expression
    #: ``(field, values)``: only shown while that field holds one of ``values``.
    visible_if: tuple[str, tuple[Any, ...]] | None = None

    def is_visible(self, obj: Any) -> bool:
        if self.visible_if is None:
            return True
        field, values = self.visible_if
        return getattr(obj, field, None) in values


@dataclass(frozen=True)
class Terminal:
    """One connection point of a component: the field that holds its bus id."""

    field: str
    role: str = "bus"            # bus | hv | lv | from | to
    optional: bool = False       # may stay empty, e.g. an impedance to ground


_COMMON = (
    FieldSpec("name", "Name", "str", tooltip="Label shown on the diagram",
              parametric=False),
    FieldSpec("in_service", "In service", "bool",
              tooltip="Out-of-service components are ignored by every study",
              parametric=False),
)


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #


@dataclass
class Bus:
    """A node of the network. Everything else attaches to a bus."""

    id: str
    name: str = ""
    in_service: bool = True
    kind: str = "busbar"                 # busbar | junction
    u_base_kv: float | None = None       # None => propagated, float => pinned

    TYPE: ClassVar[str] = "bus"
    TERMINALS: ClassVar[tuple[Terminal, ...]] = ()
    BUS_FIELDS: ClassVar[tuple[str, ...]] = ()
    FIELDS: ClassVar[tuple[FieldSpec, ...]] = _COMMON + (
        FieldSpec("kind", "Kind", "choice", choices=("busbar", "junction"),
                  tooltip="Junctions are drawn as a small dot instead of a bar",
                  parametric=False),
        FieldSpec("u_base_kv", "Pinned U base", "float", "kV", minimum=0.0,
                  tooltip="Leave empty to derive the base by propagation from "
                          "the reference bus through the transformer ratios"),
    )


@dataclass
class ExternalGrid:
    """External grid or voltage source: a Thevenin equivalent at a bus.

    ``z_mode`` says how its impedance is given:

    * ``z_ohm``: directly, in ohm at the bus (0 is an ideal source);
    * ``ik``: by the three-phase fault current ``ik_ka`` at the bus, so
      ``|Z| = U/(sqrt(3)*Ik)``;
    * ``sk``: by the short-circuit power ``sk_mva``, so ``|Z| = U**2/Sk``.

    ``U`` is ``u_pu`` times ``un_kv`` (the bus base when empty): the pre-fault
    voltage the fault level refers to. For ``ik`` and ``sk`` the angle of ``Z``
    is ``acos(cos_phi)``, the lagging power factor of the fault current.
    """

    id: str
    bus: str = ""
    name: str = ""
    in_service: bool = True
    u_pu: float = 1.0
    theta_deg: float = 0.0
    z_mode: str = "z_ohm"                # z_ohm | ik | sk
    z_th_ohm: complex = 0j               # z_mode "z_ohm", at the bus; 0 means ideal
    ik_ka: float = 0.0                   # z_mode "ik"
    sk_mva: float = 0.0                  # z_mode "sk"
    cos_phi: float = 0.1                 # of the fault current, lagging
    un_kv: float | None = None           # None => the bus base voltage
    is_slack: bool = True

    TYPE: ClassVar[str] = "source"
    TERMINALS: ClassVar[tuple[Terminal, ...]] = (Terminal("bus"),)
    BUS_FIELDS: ClassVar[tuple[str, ...]] = tuple(t.field for t in TERMINALS)
    FIELDS: ClassVar[tuple[FieldSpec, ...]] = _COMMON + (
        FieldSpec("bus", "Bus", "bus", parametric=False),
        FieldSpec("u_pu", "U", "float", "pu", minimum=0.0,
                  tooltip="Source voltage; with Ik or Sk also the pre-fault voltage "
                          "the fault level refers to"),
        FieldSpec("theta_deg", "Angle", "float", "deg"),
        FieldSpec("z_mode", "Impedance from", "choice", choices=("z_ohm", "ik", "sk"),
                  parametric=False,
                  tooltip="z_ohm: give Z directly. ik: three-phase fault current. "
                          "sk: short-circuit power."),
        FieldSpec("z_th_ohm", "Z Thevenin", "complex", "ohm",
                  tooltip="0 means an ideal voltage source. Referred to the bus base.",
                  visible_if=("z_mode", ("z_ohm",))),
        FieldSpec("ik_ka", "Ik (three-phase)", "float", "kA", minimum=0.0,
                  visible_if=("z_mode", ("ik",))),
        FieldSpec("sk_mva", "Sk (three-phase)", "float", "MVA", minimum=0.0,
                  visible_if=("z_mode", ("sk",))),
        FieldSpec("cos_phi", "cos phi of the fault", "float", "", minimum=0.0,
                  maximum=1.0, visible_if=("z_mode", ("ik", "sk")),
                  tooltip="Lagging power factor of the fault current: R/|Z|"),
        FieldSpec("un_kv", "U nominal", "float", "kV", minimum=0.0,
                  visible_if=("z_mode", ("ik", "sk")),
                  tooltip="Voltage the fault level is quoted at. Empty means the "
                          "bus base."),
        FieldSpec("is_slack", "Slack", "bool", parametric=False,
                  tooltip="Fixes the voltage angle reference for the network"),
    )

    @property
    def is_ideal(self) -> bool:
        return self.z_mode == "z_ohm" and self.z_th_ohm == 0

    def impedance_ohm(self, u_base_kv: float) -> complex:
        """The Thevenin impedance in ohm, whatever ``z_mode`` gives it as."""
        if self.z_mode == "z_ohm":
            return complex(self.z_th_ohm)
        u_kv = self.u_pu * (self.un_kv or u_base_kv)
        if self.z_mode == "ik":
            if self.ik_ka <= 0:
                raise ValueError("the three-phase fault current must be positive")
            mag = u_kv / (math.sqrt(3.0) * self.ik_ka)
        elif self.z_mode == "sk":
            if self.sk_mva <= 0:
                raise ValueError("the short-circuit power must be positive")
            mag = u_kv * u_kv / self.sk_mva
        else:
            raise ValueError(f"unknown impedance mode '{self.z_mode}'")
        if not 0.0 <= self.cos_phi <= 1.0:
            raise ValueError("cos phi must be between 0 and 1")
        return cmath.rect(mag, math.acos(self.cos_phi))


#: The name the class had before it grew the fault-level modes.
Source = ExternalGrid


@dataclass
class Transformer:
    """Two-winding transformer, lossless series impedance.

    The turns ratio is absorbed by the per-unit base change, so the component
    contributes a plain series impedance to the network.
    """

    id: str
    bus_hv: str = ""
    bus_lv: str = ""
    name: str = ""
    in_service: bool = True
    sn_mva: float = 1.0
    u1n_kv: float = 1.0                  # rated voltage, HV side
    u2n_kv: float = 1.0                  # rated voltage, LV side
    x_pct: float = 0.0
    r_pct: float = 0.0
    tap: float = 1.0                     # off-nominal ratio, reserved for LF
    referred_side: str = "lv"            # lv | hv

    TYPE: ClassVar[str] = "transformer"
    TERMINALS: ClassVar[tuple[Terminal, ...]] = (
        Terminal("bus_hv", "hv"), Terminal("bus_lv", "lv"))
    BUS_FIELDS: ClassVar[tuple[str, ...]] = tuple(t.field for t in TERMINALS)
    FIELDS: ClassVar[tuple[FieldSpec, ...]] = _COMMON + (
        FieldSpec("bus_hv", "HV bus", "bus", parametric=False),
        FieldSpec("bus_lv", "LV bus", "bus", parametric=False),
        FieldSpec("sn_mva", "S rated", "float", "MVA", minimum=0.0),
        FieldSpec("u1n_kv", "U rated HV", "float", "kV", minimum=0.0),
        FieldSpec("u2n_kv", "U rated LV", "float", "kV", minimum=0.0),
        FieldSpec("x_pct", "x", "float", "%"),
        FieldSpec("r_pct", "r", "float", "%"),
        FieldSpec("tap", "Tap ratio", "float", "", minimum=0.0,
                  tooltip="Off-nominal ratio. Must be 1.0 for the S1 studies."),
        FieldSpec("referred_side", "Refer Z to", "choice", choices=("lv", "hv"),
                  parametric=False,
                  tooltip="Which side's rated voltage and base are used for the "
                          "per-unit conversion"),
    )

    @property
    def ratio_hv_to_lv(self) -> float:
        return self.u2n_kv / self.u1n_kv


@dataclass
class Line:
    """Line or cable, PI-equivalent, optionally n identical lines in parallel."""

    id: str
    bus_from: str = ""
    bus_to: str = ""
    name: str = ""
    in_service: bool = True
    r_ohm_per_km: float = 0.0
    x_ohm_per_km: float = 0.0
    b_s_per_km: float = 0.0              # TOTAL charging susceptance per km, ONE line
    g_s_per_km: float = 0.0
    length_km: float = 1.0
    n_parallel: int = 1

    TYPE: ClassVar[str] = "line"
    TERMINALS: ClassVar[tuple[Terminal, ...]] = (
        Terminal("bus_from", "from"), Terminal("bus_to", "to"))
    BUS_FIELDS: ClassVar[tuple[str, ...]] = tuple(t.field for t in TERMINALS)
    FIELDS: ClassVar[tuple[FieldSpec, ...]] = _COMMON + (
        FieldSpec("bus_from", "From bus", "bus", parametric=False),
        FieldSpec("bus_to", "To bus", "bus", parametric=False),
        FieldSpec("r_ohm_per_km", "r", "float", "ohm/km"),
        FieldSpec("x_ohm_per_km", "x", "float", "ohm/km"),
        FieldSpec("b_s_per_km", "b_c", "float", "S/km",
                  tooltip="Total charging susceptance per km of ONE line"),
        FieldSpec("g_s_per_km", "g", "float", "S/km"),
        FieldSpec("length_km", "Length", "float", "km", minimum=0.0),
        FieldSpec("n_parallel", "Parallel lines", "int", "", minimum=1),
    )


@dataclass
class Load:
    """Load described by the power it draws at a nominal voltage."""

    id: str
    bus: str = ""
    name: str = ""
    in_service: bool = True
    p_mw: float = 0.0
    cos_phi: float = 1.0
    pf_sign: str = "lag"                 # lag gives +jQ (inductive), lead gives -jQ
    u_nominal_kv: float | None = None    # None => use the bus base
    model: str = "constant_impedance"    # constant_power reserved for LF

    TYPE: ClassVar[str] = "load"
    TERMINALS: ClassVar[tuple[Terminal, ...]] = (Terminal("bus"),)
    BUS_FIELDS: ClassVar[tuple[str, ...]] = tuple(t.field for t in TERMINALS)
    FIELDS: ClassVar[tuple[FieldSpec, ...]] = _COMMON + (
        FieldSpec("bus", "Bus", "bus", parametric=False),
        FieldSpec("p_mw", "P", "float", "MW"),
        FieldSpec("cos_phi", "cos phi", "float", "", minimum=0.0, maximum=1.0),
        FieldSpec("pf_sign", "Power factor", "choice", choices=("lag", "lead"),
                  parametric=False),
        FieldSpec("u_nominal_kv", "U nominal", "float", "kV", minimum=0.0,
                  tooltip="Voltage at which the load consumes P. Empty means the bus base."),
        FieldSpec("model", "Model", "choice",
                  choices=("constant_impedance", "constant_power"),
                  parametric=False,
                  tooltip="Only constant_impedance is used by the S1 studies"),
    )


@dataclass
class Shunt:
    """Shunt compensation. ``q_mvar > 0`` is capacitive (it generates reactive power)."""

    id: str
    bus: str = ""
    name: str = ""
    in_service: bool = True
    q_mvar: float = 0.0
    u_nominal_kv: float | None = None

    TYPE: ClassVar[str] = "shunt"
    TERMINALS: ClassVar[tuple[Terminal, ...]] = (Terminal("bus"),)
    BUS_FIELDS: ClassVar[tuple[str, ...]] = tuple(t.field for t in TERMINALS)
    FIELDS: ClassVar[tuple[FieldSpec, ...]] = _COMMON + (
        FieldSpec("bus", "Bus", "bus", parametric=False),
        FieldSpec("q_mvar", "Q", "float", "MVAr",
                  tooltip="Positive is capacitive, the same sign as line charging. "
                          "Use a negative value for a reactor."),
        FieldSpec("u_nominal_kv", "U nominal", "float", "kV", minimum=0.0),
    )


@dataclass
class Impedance:
    """Raw impedance, series between two buses or shunt to ground. Escape hatch."""

    id: str
    bus_from: str = ""
    bus_to: str | None = None            # None means a shunt to ground
    name: str = ""
    in_service: bool = True
    r: float = 0.0
    x: float = 0.0
    in_pu: bool = False                  # False means ohm at the bus_from base

    TYPE: ClassVar[str] = "impedance"
    TERMINALS: ClassVar[tuple[Terminal, ...]] = (
        Terminal("bus_from", "from"), Terminal("bus_to", "to", optional=True))
    BUS_FIELDS: ClassVar[tuple[str, ...]] = tuple(t.field for t in TERMINALS)
    FIELDS: ClassVar[tuple[FieldSpec, ...]] = _COMMON + (
        FieldSpec("bus_from", "From bus", "bus", parametric=False),
        FieldSpec("bus_to", "To bus", "bus", parametric=False,
                  tooltip="Leave empty for a shunt to ground"),
        FieldSpec("r", "R", "float", "ohm / pu"),
        FieldSpec("x", "X", "float", "ohm / pu"),
        FieldSpec("in_pu", "Values in pu", "bool", parametric=False),
    )


# --------------------------------------------------------------------------- #
# Registry and helpers
# --------------------------------------------------------------------------- #

COMPONENT_CLASSES: tuple[type, ...] = (
    Bus, ExternalGrid, Transformer, Line, Load, Shunt, Impedance,
)

TYPE_REGISTRY: dict[str, type] = {c.TYPE: c for c in COMPONENT_CLASSES}


def is_series(comp: Any) -> bool:
    """True when the component is a branch between two or more buses.

    A required terminal counts even while it is unconnected (a line is a
    branch before it is wired); an optional one only once it names a bus, so
    an impedance without ``bus_to`` is a shunt to ground.
    """
    count = sum(1 for t in getattr(comp, "TERMINALS", ())
                if not t.optional or getattr(comp, t.field, None))
    return count >= 2


def is_node_shunt(comp: Any) -> bool:
    """True when the component attaches to exactly one bus (load, shunt, source)."""
    if isinstance(comp, Bus):
        return False
    return not is_series(comp)


def unconnected_terminals(comp: Any) -> list[str]:
    """Required terminal fields that do not name a bus yet."""
    return [t.field for t in getattr(comp, "TERMINALS", ())
            if not t.optional and not getattr(comp, t.field, None)]


def buses_of(comp: Any) -> list[str]:
    """Bus ids this component attaches to, skipping empty or optional terminals."""
    out: list[str] = []
    for attr in getattr(comp, "BUS_FIELDS", ()):
        val = getattr(comp, attr, None)
        if val:
            out.append(val)
    return out


def display_name(comp: Any) -> str:
    return comp.name or comp.id


def field_specs(comp_or_cls: Any) -> tuple[FieldSpec, ...]:
    cls = comp_or_cls if isinstance(comp_or_cls, type) else type(comp_or_cls)
    return cls.FIELDS


def data_field_names(comp_or_cls: Any) -> list[str]:
    """Dataclass field names, in declaration order."""
    cls = comp_or_cls if isinstance(comp_or_cls, type) else type(comp_or_cls)
    return [f.name for f in dc_fields(cls)]
