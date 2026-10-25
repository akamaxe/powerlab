"""Study results: the numbers, and their presentation.

A :class:`Result` carries both the formatted :class:`Table` objects the GUI
renders and the headless runner prints, and the numeric payload behind them
(:class:`Quantity` per bus, per element and overall, :class:`LabeledMatrix`)
that diagram annotations and exports are built from. Formatting follows the
course convention: impedance and complex power rectangular, voltage and current
polar with the angle in degrees.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .analysis import EquivalentZResult, LoadFlowResult
from .perunit import PuModel
from .twoport import TwoPortResult
from .units import (
    fmt_matrix,
    fmt_polar,
    fmt_quantity,
    fmt_real,
    fmt_rect,
    polar_pair,
    short_style,
)


@dataclass
class Table:
    """One titled block of results."""

    title: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    note: str = ""
    #: Key of the :attr:`Result.matrices` entry this table prints, if any.
    source: str = ""

    def add(self, *cells: str) -> None:
        self.rows.append([str(c) for c in cells])

    def text(self) -> str:
        out = [self.title, "-" * len(self.title)]
        cols = [self.headers] + self.rows if self.headers else self.rows
        if not cols:
            return "\n".join(out)
        widths = [max(len(r[i]) if i < len(r) else 0 for r in cols)
                  for i in range(max(len(r) for r in cols))]
        if self.headers:
            out.append("  ".join(h.ljust(widths[i])
                                 for i, h in enumerate(self.headers)))
            out.append("  ".join("-" * widths[i] for i in range(len(self.headers))))
        for r in self.rows:
            out.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)).rstrip())
        if self.note:
            out.append(f"({self.note})")
        return "\n".join(out)


@dataclass
class Quantity:
    """A number plus what it means, so it can be formatted and annotated.

    ``kind`` is a key of :data:`psa.core.units.QUANTITY_STYLE` (for example
    ``voltage_pu``, ``power_mva``, ``impedance_pu``), which fixes the course
    presentation: rectangular, polar or plain.
    """

    value: complex | float
    kind: str = "ratio"
    label: str = ""

    def text(self) -> str:
        return fmt_quantity(self.value, self.kind)


@dataclass
class LabeledMatrix:
    """A matrix with its row and column labels (bus names, sequence names)."""

    data: np.ndarray
    rows: list[str]
    cols: list[str]
    kind: str = "admittance_pu"
    title: str = ""


@dataclass(frozen=True)
class Annotation:
    """One badge on the diagram: some lines of text pinned to an element."""

    target: str                     # bus or component id
    lines: tuple[str, ...]
    slot: str = "bus"               # bus | branch | element
    colour: str = "text"            # text | flow | warn


@dataclass
class Result:
    """A study's output: a headline, tables, and the numbers behind them."""

    key: str
    title: str
    tables: list[Table] = field(default_factory=list)
    headline: str = ""
    warnings: list[str] = field(default_factory=list)
    #: Overall values, e.g. ``{"Z": Quantity(...)}``.
    scalars: dict[str, Quantity] = field(default_factory=dict)
    #: Per-bus values, ``{bus_id: {"u": Quantity, ...}}``.
    buses: dict[str, dict[str, Quantity]] = field(default_factory=dict)
    #: Per-component values, ``{comp_id: {"i_from": Quantity, ...}}``.
    elements: dict[str, dict[str, Quantity]] = field(default_factory=dict)
    matrices: dict[str, LabeledMatrix] = field(default_factory=dict)
    #: Non-numeric facts the annotations need, e.g. ``{"fault_bus": "b7"}``.
    info: dict[str, str] = field(default_factory=dict)
    #: The case that produced it, empty for a bare study run.
    case_id: str = ""

    def text(self) -> str:
        parts = [f"=== {self.title} ==="]
        if self.headline:
            parts.append(self.headline)
        parts.extend(t.text() for t in self.tables)
        parts.extend(f"! {w}" for w in self.warnings)
        return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def base_tables(pu: PuModel) -> list[Table]:
    """Voltage zones and the per-unit bases of every bus."""
    b = pu.bases
    lab = pu.labels
    t = Table("Base values",
              ["Bus", "U base [kV]", "Z base [ohm]", "I base [kA]"])
    u_style = short_style(*b.u_base_kv.values())
    z_style = short_style(*b.z_base_ohm.values())
    for bus_id in b.u_base_kv:
        t.add(lab.get(bus_id, bus_id),
              fmt_real(b.u_base_kv[bus_id], u_style),
              fmt_real(b.z_base_ohm[bus_id], z_style),
              fmt_real(b.i_base_ka[bus_id]))
    t.note = f"S base = {fmt_real(pu.s_base)} MVA"

    z = Table("Voltage zones", ["U base [kV]", "Buses"])
    for u, ids in b.zones:
        z.add(fmt_real(u, u_style), ", ".join(lab.get(i, i) for i in ids))
    return [t, z]


def per_unit_table(pu: PuModel) -> Table:
    """Every component's per-unit value, with the base factor kept auditable."""
    t = Table("Component values in per unit",
              ["Component", "Type", "Z [pu]", "y shunt [pu]", "S [MVA]",
               "U nom [kV]", "(Ub/Un)^2", "Note"])
    for s in pu.trace:
        t.add(
            s.name,
            s.comp_type,
            fmt_rect(s.z_pu) if s.z_pu is not None else "",
            fmt_rect(s.ysh_pu) if s.ysh_pu is not None else (
                fmt_rect(s.y_pu) if s.y_pu is not None else ""),
            fmt_rect(s.s_mva) if s.s_mva is not None else "",
            fmt_real(s.u_nominal_kv) if s.u_nominal_kv is not None else "",
            fmt_real(s.base_factor),
            s.note,
        )
    t.note = ("y shunt is the PI half-shunt for lines and the total admittance "
              "for loads and shunts")
    return t


def two_port_result(tp: TwoPortResult, title: str = "S1a) Two-port matrix") -> Result:
    """The per-element F matrices, the product, det and the method cross-check."""
    li = tp.label_i or tp.bus_i
    lj = tp.label_j or tp.bus_j
    res = Result("s1a", f"{title}: bus {li} -> bus {lj}")

    conv = Table("Convention")
    conv.add("[U1; I1] = F * [U2; I2]")
    conv.add(f"port 1 = bus {li} (source side), port 2 = bus {lj} (load side)")
    res.tables.append(conv)

    if tp.excluded_at_ports:
        ex = Table("Excluded at the ports (termination rule)",
                   ["Component", "Reason"])
        for comp_id, desc in tp.excluded_at_ports:
            ex.add(comp_id, desc)
        ex.note = ("node shunts sitting on a port bus are the termination and "
                   "the source, so they are not part of the two-port; line "
                   "charging is always kept")
        res.tables.append(ex)

    if tp.per_element:
        el = Table("Element matrices, in cascade order")
        for name, m in tp.per_element:
            el.add(f"F_{name}")
            for row in fmt_matrix(m):
                el.add(f"    {row}")
        res.tables.append(el)

    prod = Table(f"F_{li}{lj}", source="F")
    for row in fmt_matrix(tp.f):
        prod.add(row)
    res.tables.append(prod)

    chk = Table("Checks", ["Quantity", "Value"])
    chk.add("det(F)", fmt_rect(tp.det))
    if tp.reciprocity_applies:
        chk.add("|det(F) - 1|", f"{abs(tp.det - 1.0):.3e}")
    else:
        chk.add("|det(F) - 1|", "n/a (off-nominal tap present)")
    if tp.f_cascade is not None:
        chk.add("cascade vs Y-bus, relative", f"{tp.residual_rel:.3e}")
        chk.add("cascade path", " -> ".join(tp.path or []))
    else:
        chk.add("cascade", f"unavailable ({tp.cascade_status})")
    res.tables.append(chk)

    if tp.cascade_status == "not_unique":
        res.warnings.append(
            "The path between the ports is not unique (meshed network), so the "
            "element-by-element cascade is unavailable. The matrix shown comes "
            "from the Y-bus reduction, which is valid either way.")
    elif tp.cascade_status == "parallel_merged":
        res.warnings.append(
            "Parallel branches were merged into a single equivalent section "
            "before cascading.")
    elif tp.cascade_status == "no_path":
        res.warnings.append("No series path connects the two ports.")

    a, b, c, d = tp.abcd
    res.headline = (f"A = {fmt_rect(a)}   B = {fmt_rect(b)}\n"
                    f"C = {fmt_rect(c)}   D = {fmt_rect(d)}")
    res.matrices["F"] = LabeledMatrix(
        np.array(tp.f, dtype=complex), ["U1", "I1"], ["U2", "I2"],
        kind="ratio", title=f"F_{li}{lj}")
    for name, value in zip("ABCD", (a, b, c, d)):
        res.scalars[name] = Quantity(value, "impedance_pu", name)
    res.scalars["det"] = Quantity(tp.det, "ratio", "det(F)")
    return res


def equivalent_z_result(eq: EquivalentZResult,
                        title: str = "S1b) Equivalent impedance") -> Result:
    lb = eq.label or eq.bus
    res = Result("s1b", f"{title} seen from bus {lb}")
    t = Table("Result", ["Quantity", "Value"])
    t.add(f"Z seen from bus {lb}", fmt_rect(eq.z_pu, unit="pu"))
    t.add("", fmt_rect(eq.z_ohm, unit="ohm"))
    t.add("|Z|", f"{fmt_real(abs(eq.z_pu))} pu")
    t.add("arg(Z)", f"{eq.angle_deg:.4f} deg")
    t.add("Z base", f"{fmt_real(eq.z_base_ohm)} ohm")
    res.tables.append(t)

    if eq.z_pu_twoport is not None:
        c = Table("Cross-check", ["Method", "Value"])
        c.add("Y-bus (Kron reduction)", fmt_rect(eq.z_pu, unit="pu"))
        c.add("two-port (A*Z+B)/(C*Z+D)", fmt_rect(eq.z_pu_twoport, unit="pu"))
        c.add("termination Z", fmt_rect(eq.z_termination_pu, unit="pu")
              if eq.z_termination_pu is not None else "")
        c.add("residual, absolute", f"{eq.residual_abs:.3e}")
        c.add("residual, relative", f"{eq.residual_rel:.3e}")
        res.tables.append(c)

    res.headline = f"Z = {fmt_rect(eq.z_pu, unit='pu')} = {fmt_rect(eq.z_ohm, unit='ohm')}"
    res.scalars["Z"] = Quantity(eq.z_pu, "impedance_pu", f"Z seen from bus {lb}")
    res.scalars["Z_ohm"] = Quantity(eq.z_ohm, "impedance_ohm", f"Z seen from bus {lb}")
    return res


def voltage_result(lf: LoadFlowResult, pu: PuModel,
                   focus_bus: str | None = None,
                   title: str = "S1c) Bus voltages") -> Result:
    res = Result("s1c", title)

    t = Table("Bus voltages",
              ["Bus", "U [pu]", "angle [deg]", "U [kV]", "I injected [pu]"])
    lab = pu.labels
    for bus_id, u in lf.u_pu.items():
        mag, ang = lf.u_polar(bus_id)
        t.add(lab.get(bus_id, bus_id), fmt_real(mag), f"{ang:.4f}", fmt_real(lf.u_kv[bus_id]),
              fmt_polar(lf.i_inject_pu[bus_id]))
        res.buses[bus_id] = {
            "u": Quantity(u, "voltage_pu", "U"),
            "u_kv": Quantity(lf.u_kv[bus_id], "ratio", "|U| [kV]"),
            "i_inject": Quantity(lf.i_inject_pu[bus_id], "current_pu", "I injected"),
        }
    res.tables.append(t)

    f = Table("Branch flows",
              ["Branch", "From", "To", "I from [pu]", "S from [pu]",
               "losses [pu]"])
    for br in lf.branch.values():
        f.add(br.name, lab.get(br.bus_from, br.bus_from),
              lab.get(br.bus_to, br.bus_to),
              fmt_polar(br.i_from_pu), fmt_rect(br.s_from_pu),
              fmt_rect(br.losses_pu))
        res.elements[br.comp_id] = {
            "i_from": Quantity(br.i_from_pu, "current_pu", "I from"),
            "s_from": Quantity(br.s_from_pu * lf.s_base_mva, "power_mva", "S from"),
            "losses": Quantity(br.losses_pu * lf.s_base_mva, "power_mva", "losses"),
        }
    f.note = (f"total losses = {fmt_rect(lf.total_losses_pu, unit='pu')} = "
              f"{fmt_rect(lf.total_losses_pu * lf.s_base_mva, unit='MVA')}")
    res.tables.append(f)

    s = Table("Sources", ["Source", "I [pu]", "I [kA]", "S [MVA]"])
    for comp_id, i_pu in lf.i_source_pu.items():
        src = next(x for x in pu.sources if x.comp_id == comp_id)
        s.add(comp_id, fmt_polar(i_pu),
              fmt_polar(i_pu * pu.bases.i(src.bus)),
              fmt_rect(lf.s_source_mva[comp_id]))
    res.tables.append(s)

    for comp_id, i_pu in lf.i_source_pu.items():
        res.elements.setdefault(comp_id, {}).update({
            "i": Quantity(i_pu, "current_pu", "I"),
            "s": Quantity(lf.s_source_mva[comp_id], "power_mva", "S"),
        })

    if focus_bus:
        mag, ang = lf.u_polar(focus_bus)
        slack = lf.slack_buses[0] if lf.slack_buses else None
        drop = ""
        if slack:
            u_ref = abs(lf.u_pu[slack])
            drop = f", voltage drop {100.0 * (u_ref - mag) / u_ref:.4f} %"
        res.headline = (f"U at bus {lab.get(focus_bus, focus_bus)} = "
                        f"{fmt_polar(lf.u_pu[focus_bus], 'pu')}"
                        f" = {fmt_real(lf.u_kv[focus_bus])} kV{drop}")
    return res


def annotate_voltages(res: Result, mode: str = "kv_pu") -> list[Annotation]:
    """Bus voltages and branch flows of a nodal solution, as diagram badges.

    ``mode`` ``"kv"`` shows only the magnitude in kV; anything else adds the
    per-unit phasor.
    """
    out: list[Annotation] = []
    for bus_id, q in res.buses.items():
        if "u" not in q:
            continue
        mag, ang = polar_pair(q["u"].value)
        lines = [f"{q['u_kv'].value:.4f} kV"]
        if mode != "kv":
            lines.append(f"{mag:.4f} pu < {ang:.3f}\N{DEGREE SIGN}")
        out.append(Annotation(bus_id, tuple(lines), "bus"))
    for comp_id, q in res.elements.items():
        if "i_from" not in q:
            continue
        s = q["s_from"].value
        out.append(Annotation(
            comp_id, (f"{abs(q['i_from'].value):.4f} pu",
                      f"{s.real:+.4f}{s.imag:+.4f}j MVA"), "branch", "flow"))
    return out


def phasor_chain_table(u_a: complex, i_a: complex, u_e: complex, i_e: complex,
                       u_base_a: float, u_base_e: float,
                       i_base_a: float, i_base_e: float,
                       bus_a: str, bus_e: str) -> Table:
    """The explicit U_a -> I_a -> [U_e; I_e] chain the report asks for."""
    t = Table("Phasor chain", ["Quantity", "Per unit", "SI"])
    t.add(f"U_{bus_a}", fmt_polar(u_a, "pu"), f"{fmt_real(abs(u_a) * u_base_a)} kV")
    t.add(f"I_{bus_a}", fmt_polar(i_a, "pu"), f"{fmt_real(abs(i_a) * i_base_a)} kA")
    t.add(f"U_{bus_e}", fmt_polar(u_e, "pu"), f"{fmt_real(abs(u_e) * u_base_e)} kV")
    t.add(f"I_{bus_e}", fmt_polar(i_e, "pu"), f"{fmt_real(abs(i_e) * i_base_e)} kA")
    return t
