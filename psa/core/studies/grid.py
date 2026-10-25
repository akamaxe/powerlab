"""Network studies: admittance matrix, linear voltages, Thevenin, short circuit.

These are the S2 and S3 tasks, written for any network: S2a/S3a are ``ybus``
(positive sequence with the source as an internal node; zero sequence with it
grounded), S2b/S3b/S3d ``thevenin``, S2c/S3c ``short_circuit``, and S2d the
linear voltage solve on a case that halves a load. Every study keeps only the
energized part of the network, so a case that disconnects part of it (the
industry of S1, for S2a-S2c and S3a-S3c) simply sees the rest.
"""

from __future__ import annotations

import math

import numpy as np

from ..components import ExternalGrid, display_name
from ..errors import ValidationError
from ..model.sequence import INTERNAL_NODE_SUFFIX, ModelOptions, build_model
from ..model.stamps import Seq
from ..network import Network
from ..present import matrix_table
from ..results import Annotation, LabeledMatrix, Quantity, Result, Table, annotate_voltages
from ..solve import SEQS, SequenceNetworks, sequence_thevenin, solve_fault, to_phases
from ..solve.shunt import FAULT_KINDS
from ..solve.thevenin import thevenin_at
from ..study import OptionSpec, Study, StudyContext, _far_bus, _run_voltages, register
from ..units import fmt_polar, fmt_real, fmt_rect, polar_pair

SOURCE_MODES = ("internal_node", "grounded", "exclude")
SEQUENCES = {"positive": Seq.POS, "negative": Seq.NEG, "zero": Seq.ZERO}
SEQ_NAME = {Seq.POS: "positive", Seq.NEG: "negative", Seq.ZERO: "zero"}
SEQ_INDEX = {Seq.POS: "1", Seq.NEG: "2", Seq.ZERO: "0"}
PHASES = ("a", "b", "c")


def _energized(ctx: StudyContext):
    return build_model(ctx.net, ModelOptions(energized_only=True))


def _default_bus(net: Network) -> str:
    """The base reference bus, unless an ideal source sits there."""
    ideal = {c.bus for c in net.active_components()
             if isinstance(c, ExternalGrid) and c.is_ideal}
    bus = net.base_ref_bus
    if not bus or bus in ideal:
        bus = _far_bus(net, net.base_ref_bus)
    return bus


def _bus_label(net: Network, bus: str) -> str:
    return display_name(net.buses[bus]) if bus in net.buses else bus


def _checks_table(checks) -> Table:
    t = Table("Cross-checks", ["Comparison", "Residual"])
    for label, value in checks:
        t.add(label, f"{value:.3e}")
    return t


# --------------------------------------------------------------------------- #
# Admittance matrix
# --------------------------------------------------------------------------- #


def _run_ybus(ctx: StudyContext) -> Result:
    net = ctx.net
    mode = ctx.options.get("sources") or "internal_node"
    if mode not in SOURCE_MODES:
        mode = "internal_node"
    seq = SEQUENCES.get(ctx.options.get("sequence") or "positive", Seq.POS)
    sm = _energized(ctx).seq(seq)
    Y, index = sm.ybus(mode)

    labels = []
    nodes = Table("Nodes", ["Row", "Node", "Id", "U base [kV]"])
    for i, node in enumerate(index, start=1):
        if node.endswith(INTERNAL_NODE_SUFFIX):
            src = net.components[node[: -len(INTERNAL_NODE_SUFFIX)]]
            label = f"{display_name(src)} (internal)"
            u_base = sm.bases.u(src.bus)
        else:
            label = _bus_label(net, node)
            u_base = sm.bases.u(node)
        labels.append(label)
        nodes.add(str(i), label, node, fmt_real(u_base))

    described = {"internal_node": "each source behind its impedance as an extra node",
                 "grounded": "each source's impedance to ground (source node grounded)",
                 "exclude": "the passive network only (sources left out)"}[mode]
    res = Result("ybus", f"Admittance matrix, {SEQ_NAME[seq]} sequence")
    lm = LabeledMatrix(Y, labels, labels, kind="admittance_pu",
                       title=f"Y{SEQ_INDEX[seq]}, {SEQ_NAME[seq]} sequence [pu], "
                             f"{len(labels)} x {len(labels)}")
    res.matrices["Y"] = lm
    res.tables.append(matrix_table(lm))
    res.tables.append(nodes)
    asym = float(np.max(np.abs(Y - Y.T))) if Y.size else 0.0
    chk = Table("Checks", ["Quantity", "Value"])
    chk.add("max |Y - Y^T|", f"{asym:.3e}")
    chk.add("sources", described)
    res.tables.append(chk)
    res.headline = (f"{len(labels)} x {len(labels)} {SEQ_NAME[seq]}-sequence admittance "
                    f"matrix with {described}.")
    return res


register(Study(
    key="ybus",
    title="Admittance matrix (Y-bus)",
    menu_group="Model",
    run=_run_ybus,
    option_fields=(
        OptionSpec("sequence", "Sequence", "choice", choices=tuple(SEQUENCES)),
        OptionSpec("sources", "Sources", "choice", choices=SOURCE_MODES,
                   tooltip="internal_node: each source behind its impedance becomes "
                           "an extra node (the S2a form). grounded: its impedance "
                           "goes to ground at the bus (the S3a form). exclude: leave "
                           "sources out."),
    ),
    description="The bus admittance matrix of the energized network, in per unit.",
    defaults=lambda net: {"sequence": "positive", "sources": "internal_node"},
))


# --------------------------------------------------------------------------- #
# Linear voltages (S1c, S2d)
# --------------------------------------------------------------------------- #


def _run_linear(ctx: StudyContext) -> Result:
    res = _run_voltages(ctx)
    res.key, res.title = "linear", "Bus voltages"
    return res


register(Study(
    key="linear",
    title="Bus voltages (linear network)",
    menu_group="Network",
    run=_run_linear,
    option_fields=(OptionSpec("bus", "Report voltage at bus", "bus"),),
    description=("Every bus voltage and branch flow of the network driven by its "
                 "sources, with loads as constant impedances."),
    defaults=lambda net: {"bus": _far_bus(net, net.base_ref_bus)},
    annotate=annotate_voltages,
))


# --------------------------------------------------------------------------- #
# Thevenin equivalent (S2b, S3b, S3d)
# --------------------------------------------------------------------------- #


def _sequence_networks(model, warnings: list[str]) -> SequenceNetworks:
    """Positive and negative always; zero when the network has its data."""
    try:
        model.seq(Seq.ZERO)
        return SequenceNetworks.build(model, SEQS)
    except ValidationError as exc:
        warnings.append(f"No zero-sequence equivalent: {exc}")
        return SequenceNetworks.build(model, (Seq.POS, Seq.NEG))


def _run_thevenin(ctx: StudyContext) -> Result:
    bus = ctx.options.get("bus") or _default_bus(ctx.net)
    model = _energized(ctx)
    warnings: list[str] = []
    nets = _sequence_networks(model, warnings)
    st = sequence_thevenin(nets, bus)
    th = thevenin_at(model, bus, solution=nets.pre, zbus=nets.zbus[Seq.POS])

    res = Result("thevenin", f"Thevenin equivalent at bus {th.label}")
    res.warnings.extend(warnings)
    t = Table(f"Thevenin equivalent at bus {th.label}", ["Quantity", "Value"])
    t.add("U_th", fmt_polar(th.u_th, "pu"))
    t.add("", fmt_polar(th.u_th * th.u_base_kv, "kV"))
    for s in SEQS:
        z = st.z.get(s)
        name = f"Z_th,{SEQ_INDEX[s]}"
        if s not in nets.zbus:
            continue
        if z is None:
            t.add(name, "open (no path to ground in this sequence)")
            continue
        t.add(name, fmt_rect(z, unit="pu"))
        t.add("", fmt_rect(z * th.z_base_ohm, unit="ohm"))
        res.scalars[name.replace(",", "")] = Quantity(z, "impedance_pu", name)
    t.add("I_sc = U_th / Z_th,1 (three-phase)", fmt_polar(th.i_sc, "pu"))
    t.add("", fmt_polar(th.i_sc_ka, "kA"))
    t.add("S_sc = U_th conj(I_sc) S_base", fmt_rect(th.s_sc_mva, unit="MVA"))
    t.add("|S_sc|", f"{fmt_real(abs(th.s_sc_mva))} MVA")
    t.add("sqrt(3) U_base |I_sc|", f"{fmt_real(th.s_sc_nominal_mva)} MVA")
    z0 = st.z.get(Seq.ZERO)
    if Seq.ZERO in nets.zbus and z0 is not None:
        i_slg = 3.0 * th.u_th / (st.z[Seq.POS] + st.z[Seq.NEG] + z0)
        t.add("I_f = 3 U_th / (Z1 + Z2 + Z0) (line-to-ground)", fmt_polar(i_slg, "pu"))
        t.add("", fmt_polar(i_slg * th.i_base_ka, "kA"))
        res.scalars["I_slg"] = Quantity(i_slg, "current_pu", "I_f line-to-ground")
    t.note = (f"U base = {fmt_real(th.u_base_kv)} kV, Z base = "
              f"{fmt_real(th.z_base_ohm)} ohm, I base = {fmt_real(th.i_base_ka)} kA")
    res.tables.append(t)
    res.tables.append(_checks_table(th.checks))

    res.scalars.update({
        "U_th": Quantity(th.u_th, "voltage_pu", "U_th"),
        "U_th_kv": Quantity(th.u_th * th.u_base_kv, "voltage_kv", "U_th"),
        "Z_th": Quantity(th.z_th, "impedance_pu", "Z_th"),
        "Z_th_ohm": Quantity(th.z_th * th.z_base_ohm, "impedance_ohm", "Z_th"),
        "I_sc": Quantity(th.i_sc, "current_pu", "I_sc"),
        "I_sc_ka": Quantity(th.i_sc_ka, "current_ka", "I_sc"),
        "S_sc": Quantity(th.s_sc_mva, "power_mva", "S_sc"),
        "S_sc_nominal": Quantity(th.s_sc_nominal_mva, "ratio", "sqrt(3) U_base |I_sc| [MVA]"),
    })
    res.buses[bus] = {"u_th": res.scalars["U_th"], "z_th": res.scalars["Z_th"]}
    res.headline = (f"U_th = {fmt_polar(th.u_th, 'pu')}   "
                    f"Z_th,1 = {fmt_rect(th.z_th, unit='pu')}"
                    + (f"   Z_th,0 = {fmt_rect(z0, unit='pu')}"
                       if Seq.ZERO in nets.zbus and z0 is not None else ""))
    return res


def _annotate_thevenin(res: Result, mode: str = "") -> list[Annotation]:
    out = []
    for bus, q in res.buses.items():
        mag, ang = polar_pair(q["u_th"].value)
        z = q["z_th"].value
        lines = [f"U_th {mag:.4f} pu < {ang:.3f}\N{DEGREE SIGN}",
                 f"Z_th1 {z.real:.4f}{z.imag:+.4f}j pu"]
        z0 = res.scalars.get("Z_th0")
        if z0 is not None:
            lines.append(f"Z_th0 {z0.value.real:.4f}{z0.value.imag:+.4f}j pu")
        out.append(Annotation(bus, tuple(lines), "bus", "warn"))
    return out


register(Study(
    key="thevenin",
    title="Thevenin equivalent",
    menu_group="Network",
    run=_run_thevenin,
    option_fields=(OptionSpec("bus", "Seen from bus", "bus"),),
    description=("U_th from the pre-fault nodal solve and the positive-, negative- "
                 "and zero-sequence impedances from the impedance matrices with the "
                 "sources switched off."),
    defaults=lambda net: {"bus": _default_bus(net)},
    annotate=_annotate_thevenin,
))


# --------------------------------------------------------------------------- #
# Short circuit (S2c, S3c)
# --------------------------------------------------------------------------- #


def _phase_rows(t: Table, label: str, vec, base: float, unit: str) -> None:
    for name, value in zip(PHASES, vec):
        t.add(f"{label}{name}", fmt_polar(value, "pu"), fmt_polar(value * base, unit))


def _run_short_circuit(ctx: StudyContext) -> Result:
    net = ctx.net
    bus = ctx.options.get("bus") or _default_bus(net)
    kind = ctx.options.get("kind") or "3ph"
    if kind not in FAULT_KINDS:
        kind = "3ph"
    model = _energized(ctx)
    sm = model.seq(Seq.POS)
    z_base = sm.bases.z(bus) if bus in sm.bases.z_base_ohm else 1.0
    zf_ohm = complex(ctx.options.get("zf_ohm") or 0j)
    zg_ohm = complex(ctx.options.get("zg_ohm") or 0j)
    fr = solve_fault(model, bus, kind, zf_ohm / z_base, zg_ohm / z_base)
    th = fr.thevenin
    i_base, u_ph_kv = th.i_base_ka, th.u_base_kv / math.sqrt(3.0)

    res = Result("short_circuit", f"Short circuit at bus {th.label}: {fr.name}")
    f = Table("Fault", ["Quantity", "Value", ""])
    f.add("Bus", th.label, "")
    f.add("Type", fr.name, "")
    f.add("Z_f", fmt_rect(zf_ohm, unit="ohm"), fmt_rect(fr.zf, unit="pu"))
    if kind == "llg":
        f.add("Z_g", fmt_rect(zg_ohm, unit="ohm"), fmt_rect(fr.zg, unit="pu"))
    f.add("U_pre = U_th", fmt_polar(th.u_th, "pu"), "")
    for s in SEQS:
        z = th.z.get(s)
        if s in fr.cond.nets.zbus:
            f.add(f"Z_th,{SEQ_INDEX[s]}", "open" if z is None else fmt_rect(z, unit="pu"), "")
    for n, s in enumerate(SEQS):
        f.add(f"I_{SEQ_INDEX[s]}", fmt_polar(fr.i_s[n], "pu"),
              fmt_polar(fr.i_s[n] * i_base, "kA"))
    f.add("I_f", fmt_polar(fr.i_f, "pu"), fmt_polar(fr.i_f * i_base, "kA"))
    if kind in ("slg", "llg"):
        f.add("I_earth = 3 I_0", fmt_polar(fr.i_ground, "pu"),
              fmt_polar(fr.i_ground * i_base, "kA"))
    if kind == "3ph":
        f.add("S_f = U_th conj(I_f) S_base", fmt_rect(fr.s_f_mva, unit="MVA"),
              f"|S_f| = {fmt_real(abs(fr.s_f_mva))} MVA")
    f.add("sqrt(3) U_base |I_f|",
          f"{fmt_real(math.sqrt(3.0) * th.u_base_kv * abs(fr.i_f * i_base))} MVA", "")
    res.tables.append(f)

    ph = Table("At the faulted bus", ["Quantity", "pu", "SI"])
    _phase_rows(ph, "I_", fr.i_abc, i_base, "kA")
    _phase_rows(ph, "U_", fr.cond.u_abc, u_ph_kv, "kV")
    ph.note = "phase voltages line-to-neutral"
    res.tables.append(ph)

    v = Table("Bus voltages during the fault",
              ["Bus", "U_a [pu]", "U_b [pu]", "U_c [pu]", "|U_a| [kV]", "|U_b| [kV]",
               "|U_c| [kV]"])
    for b, u_s in fr.cond.u_bus_s.items():
        abc = to_phases(u_s)
        base = sm.bases.u(b) / math.sqrt(3.0)
        v.add(_bus_label(net, b), *(fmt_polar(x) for x in abc),
              *(fmt_real(abs(x) * base) for x in abc))
        res.buses[b] = {"u_pre": Quantity(fr.cond.nets.u_pre(b, Seq.POS), "voltage_pu", "U pre"),
                        "u_fault": Quantity(abc[0], "voltage_pu", "U_a fault")}
        for name, x in zip(PHASES, abc):
            res.buses[b][f"u_{name}"] = Quantity(x, "voltage_pu", f"U_{name}")
    v.note = "line-to-neutral"
    res.tables.append(v)

    c = Table("Branch currents during the fault",
              ["Branch", "At bus", "I_a [pu]", "I_b [pu]", "I_c [pu]", "max |I| [kA]"])
    for end in fr.branches:
        abc = end.i_abc
        i_ka = float(np.max(np.abs(abc))) * sm.bases.i(end.bus)
        c.add(display_name(net.components[end.comp_id]), _bus_label(net, end.bus),
              *(fmt_polar(x) for x in abc), fmt_real(i_ka))
        res.elements.setdefault(end.comp_id, {})
        if "i_fault_ka" not in res.elements[end.comp_id]:
            res.elements[end.comp_id] = {
                "i_fault": Quantity(abc[0], "current_pu", "I_a"),
                "i_fault_ka": Quantity(i_ka, "ratio", "max |I| [kA]")}
    res.tables.append(c)
    th1 = thevenin_at(model, bus, solution=fr.cond.nets.pre, zbus=fr.cond.nets.zbus[Seq.POS])
    res.tables.append(_checks_table(th1.checks))

    res.scalars.update({
        "U_th": Quantity(th.u_th, "voltage_pu", "U_th"),
        "Z_th": Quantity(th.z[Seq.POS], "impedance_pu", "Z_th,1"),
        "I_f": Quantity(fr.i_f, "current_pu", "I_f"),
        "I_f_ka": Quantity(fr.i_f * i_base, "current_ka", "I_f"),
        "S_f": Quantity(fr.s_f_mva, "power_mva", "S_f"),
    })
    for n, s in enumerate(SEQS):
        res.scalars[f"I{SEQ_INDEX[s]}"] = Quantity(fr.i_s[n], "current_pu", f"I_{SEQ_INDEX[s]}")
    res.info.update({"fault_bus": bus, "kind": kind})
    res.headline = (f"I_f = {fmt_polar(fr.i_f * i_base, 'kA')}   ({fr.name})"
                    + (f"   S_f = {fmt_rect(fr.s_f_mva, unit='MVA')}" if kind == "3ph" else ""))
    return res


def _annotate_short_circuit(res: Result, mode: str = "") -> list[Annotation]:
    fault_bus = res.info.get("fault_bus", "")
    out = []
    for bus, q in res.buses.items():
        mags = [abs(q[f"u_{p}"].value) for p in PHASES]
        if bus == fault_bus:
            i_mag, i_ang = polar_pair(res.scalars["I_f_ka"].value)
            out.append(Annotation(bus, (f"I_f {i_mag:.4f} kA < {i_ang:.2f}\N{DEGREE SIGN}",
                                        "U " + " / ".join(f"{m:.4f}" for m in mags) + " pu"),
                                  "bus", "warn"))
        elif max(mags) - min(mags) < 1e-6:
            out.append(Annotation(bus, (f"U {mags[0]:.4f} pu",), "bus"))
        else:
            out.append(Annotation(bus, ("U " + " / ".join(f"{m:.4f}" for m in mags) + " pu",),
                                  "bus"))
    for comp_id, q in res.elements.items():
        out.append(Annotation(comp_id, (f"{q['i_fault_ka'].value:.4f} kA",),
                              "branch", "flow"))
    return out


register(Study(
    key="short_circuit",
    title="Short circuit",
    menu_group="Network",
    run=_run_short_circuit,
    option_fields=(
        OptionSpec("bus", "Faulted bus", "bus"),
        OptionSpec("kind", "Fault type", "choice", choices=FAULT_KINDS,
                   tooltip="3ph: three-phase. slg: phase a to ground. ll: phases b "
                           "and c. llg: phases b and c to ground."),
        OptionSpec("zf_ohm", "Fault impedance Z_f", "complex", "ohm",
                   tooltip="Per faulted phase; 0 is a solid fault"),
        OptionSpec("zg_ohm", "Ground impedance Z_g", "complex", "ohm",
                   visible_if=("kind", ("llg",)),
                   tooltip="Common path to earth of a double line-to-ground fault"),
    ),
    description=("Fault current from the sequence Thevenin equivalents, and every "
                 "bus voltage and branch current during the fault, phase by phase "
                 "(pre-fault state: the linear network, no c-factor)."),
    defaults=lambda net: {"bus": _default_bus(net), "kind": "3ph", "zf_ohm": 0j,
                          "zg_ohm": 0j},
    annotate=_annotate_short_circuit,
))
