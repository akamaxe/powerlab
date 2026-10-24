"""Network studies: admittance matrix, linear voltages, Thevenin, short circuit.

These are the S2 tasks, written for any network: S2a is ``ybus`` with the
source as an internal node, S2b ``thevenin``, S2c ``short_circuit``, and S2d the
linear voltage solve on a case that halves a load. Every study keeps only the
energized part of the network, so a case that disconnects part of it (the
industry of S1, for S2a-S2c) simply sees the rest.
"""

from __future__ import annotations

import numpy as np

from ..components import ExternalGrid, display_name
from ..model.sequence import INTERNAL_NODE_SUFFIX, ModelOptions, build_model
from ..model.stamps import Seq
from ..network import Network
from ..present import matrix_table
from ..results import Annotation, LabeledMatrix, Quantity, Result, Table, annotate_voltages
from ..solve import solve_fault
from ..solve.thevenin import TheveninEquivalent, thevenin_at
from ..study import OptionSpec, Study, StudyContext, _far_bus, _run_voltages, register
from ..units import fmt_polar, fmt_real, fmt_rect, polar_pair

SOURCE_MODES = ("internal_node", "grounded", "exclude")


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


# --------------------------------------------------------------------------- #
# Admittance matrix
# --------------------------------------------------------------------------- #


def _run_ybus(ctx: StudyContext) -> Result:
    net = ctx.net
    mode = ctx.options.get("sources") or "internal_node"
    if mode not in SOURCE_MODES:
        mode = "internal_node"
    sm = _energized(ctx).seq(Seq.POS)
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
    res = Result("ybus", "Admittance matrix")
    lm = LabeledMatrix(Y, labels, labels, kind="admittance_pu",
                       title=f"Y, positive sequence [pu], {len(labels)} x {len(labels)}")
    res.matrices["Y"] = lm
    res.tables.append(matrix_table(lm))
    res.tables.append(nodes)
    asym = float(np.max(np.abs(Y - Y.T))) if Y.size else 0.0
    chk = Table("Checks", ["Quantity", "Value"])
    chk.add("max |Y - Y^T|", f"{asym:.3e}")
    chk.add("sources", described)
    res.tables.append(chk)
    res.headline = f"{len(labels)} x {len(labels)} admittance matrix with {described}."
    return res


register(Study(
    key="ybus",
    title="Admittance matrix (Y-bus)",
    menu_group="Model",
    run=_run_ybus,
    option_fields=(
        OptionSpec("sources", "Sources", "choice", choices=SOURCE_MODES,
                   tooltip="internal_node: each source behind its impedance becomes "
                           "an extra node (the S2a form). grounded: its impedance "
                           "goes to ground at the bus. exclude: leave sources out."),
    ),
    description="The bus admittance matrix of the energized network, in per unit.",
    defaults=lambda net: {"sources": "internal_node"},
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
# Thevenin equivalent (S2b)
# --------------------------------------------------------------------------- #


def _thevenin_rows(th: TheveninEquivalent) -> Table:
    t = Table(f"Thevenin equivalent at bus {th.label}", ["Quantity", "Value"])
    t.add("U_th", fmt_polar(th.u_th, "pu"))
    t.add("", fmt_polar(th.u_th * th.u_base_kv, "kV"))
    t.add("Z_th", fmt_rect(th.z_th, unit="pu"))
    t.add("", fmt_rect(th.z_th * th.z_base_ohm, unit="ohm"))
    t.add("I_sc = U_th / Z_th", fmt_polar(th.i_sc, "pu"))
    t.add("", fmt_polar(th.i_sc_ka, "kA"))
    t.add("S_sc = U_th conj(I_sc) S_base", fmt_rect(th.s_sc_mva, unit="MVA"))
    t.add("|S_sc|", f"{fmt_real(abs(th.s_sc_mva))} MVA")
    t.add("sqrt(3) U_base |I_sc|", f"{fmt_real(th.s_sc_nominal_mva)} MVA")
    t.note = (f"U base = {fmt_real(th.u_base_kv)} kV, Z base = "
              f"{fmt_real(th.z_base_ohm)} ohm, I base = {fmt_real(th.i_base_ka)} kA")
    return t


def _thevenin_scalars(res: Result, th: TheveninEquivalent) -> None:
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


def _checks_table(checks) -> Table:
    t = Table("Cross-checks", ["Comparison", "Residual"])
    for label, value in checks:
        t.add(label, f"{value:.3e}")
    return t


def _run_thevenin(ctx: StudyContext) -> Result:
    bus = ctx.options.get("bus") or _default_bus(ctx.net)
    th = thevenin_at(_energized(ctx), bus)
    res = Result("thevenin", f"Thevenin equivalent at bus {th.label}")
    res.tables.append(_thevenin_rows(th))
    res.tables.append(_checks_table(th.checks))
    _thevenin_scalars(res, th)
    res.buses[bus] = {"u_th": res.scalars["U_th"], "z_th": res.scalars["Z_th"]}
    res.headline = (f"U_th = {fmt_polar(th.u_th, 'pu')}   "
                    f"Z_th = {fmt_rect(th.z_th, unit='pu')}")
    return res


def _annotate_thevenin(res: Result, mode: str = "") -> list[Annotation]:
    out = []
    for bus, q in res.buses.items():
        mag, ang = polar_pair(q["u_th"].value)
        z = q["z_th"].value
        out.append(Annotation(bus, (f"U_th {mag:.4f} pu < {ang:.3f}\N{DEGREE SIGN}",
                                    f"Z_th {z.real:.4f}{z.imag:+.4f}j pu"), "bus", "warn"))
    return out


register(Study(
    key="thevenin",
    title="Thevenin equivalent",
    menu_group="Network",
    run=_run_thevenin,
    option_fields=(OptionSpec("bus", "Seen from bus", "bus"),),
    description=("U_th from the pre-fault nodal solve and Z_th from the impedance "
                 "matrix with the sources switched off, each cross-checked."),
    defaults=lambda net: {"bus": _default_bus(net)},
    annotate=_annotate_thevenin,
))


# --------------------------------------------------------------------------- #
# Short circuit (S2c)
# --------------------------------------------------------------------------- #


def _run_short_circuit(ctx: StudyContext) -> Result:
    net = ctx.net
    bus = ctx.options.get("bus") or _default_bus(net)
    kind = ctx.options.get("kind") or "3ph"
    model = _energized(ctx)
    sm = model.seq(Seq.POS)
    zf_ohm = complex(ctx.options.get("zf_ohm") or 0j)
    zf = zf_ohm / sm.bases.z(bus) if bus in sm.bases.z_base_ohm else 0j
    fr = solve_fault(model, bus, kind, zf)
    th = fr.thevenin

    res = Result("short_circuit", f"Three-phase short circuit at bus {th.label}")
    f = Table("Fault", ["Quantity", "Value"])
    f.add("Bus", th.label)
    f.add("Type", {"3ph": "three-phase"}.get(kind, kind))
    f.add("Z_f", fmt_rect(zf_ohm, unit="ohm") + "   " + fmt_rect(zf, unit="pu"))
    f.add("U_pre = U_th", fmt_polar(th.u_th, "pu"))
    f.add("Z_th", fmt_rect(th.z_th, unit="pu"))
    f.add("I_f = U_th / (Z_th + Z_f)", fmt_polar(fr.i_f, "pu"))
    f.add("", fmt_polar(fr.i_f * th.i_base_ka, "kA"))
    f.add("S_f = U_th conj(I_f) S_base", fmt_rect(fr.s_f_mva, unit="MVA"))
    f.add("|S_f|", f"{fmt_real(abs(fr.s_f_mva))} MVA")
    f.add("sqrt(3) U_base |I_f|",
          f"{fmt_real(3 ** 0.5 * th.u_base_kv * abs(fr.i_f * th.i_base_ka))} MVA")
    res.tables.append(f)

    v = Table("Bus voltages during the fault",
              ["Bus", "U pre [pu]", "U fault [pu]", "|U| fault [kV]"])
    for b, u in fr.u_post.items():
        v.add(_bus_label(net, b), fmt_polar(fr.u_pre[b]), fmt_polar(u),
              fmt_real(abs(u) * sm.bases.u(b)))
        res.buses[b] = {"u_pre": Quantity(fr.u_pre[b], "voltage_pu", "U pre"),
                        "u_fault": Quantity(u, "voltage_pu", "U fault")}
    res.tables.append(v)

    c = Table("Branch currents during the fault",
              ["Branch", "From", "To", "I from [pu]", "|I| from [kA]"])
    for br in fr.branches:
        name = display_name(net.components[br.comp_id])
        i_ka = abs(br.i_i) * sm.bases.i(br.bus_i)
        c.add(name, _bus_label(net, br.bus_i), _bus_label(net, br.bus_j),
              fmt_polar(br.i_i), fmt_real(i_ka))
        res.elements[br.comp_id] = {"i_fault": Quantity(br.i_i, "current_pu", "I from"),
                                    "i_fault_ka": Quantity(i_ka, "ratio", "|I| [kA]")}
    res.tables.append(c)
    res.tables.append(_checks_table(th.checks))

    _thevenin_scalars(res, th)
    res.scalars["I_f"] = Quantity(fr.i_f, "current_pu", "I_f")
    res.scalars["I_f_ka"] = Quantity(fr.i_f * th.i_base_ka, "current_ka", "I_f")
    res.scalars["S_f"] = Quantity(fr.s_f_mva, "power_mva", "S_f")
    res.info["fault_bus"] = bus
    res.headline = (f"I_f = {fmt_polar(fr.i_f * th.i_base_ka, 'kA')}   "
                    f"S_f = {fmt_rect(fr.s_f_mva, unit='MVA')}")
    return res


def _annotate_short_circuit(res: Result, mode: str = "") -> list[Annotation]:
    fault_bus = res.info.get("fault_bus", "")
    out = []
    for bus, q in res.buses.items():
        mag, ang = polar_pair(q["u_fault"].value)
        if bus == fault_bus:
            i_mag, i_ang = polar_pair(res.scalars["I_f_ka"].value)
            out.append(Annotation(bus, (f"I_f {i_mag:.4f} kA < {i_ang:.2f}\N{DEGREE SIGN}",
                                        f"U {mag:.4f} pu"), "bus", "warn"))
        else:
            out.append(Annotation(bus, (f"U {mag:.4f} pu < {ang:.2f}\N{DEGREE SIGN}",),
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
        OptionSpec("kind", "Fault type", "choice", choices=("3ph",)),
        OptionSpec("zf_ohm", "Fault impedance Z_f", "complex", "ohm",
                   tooltip="0 is a solid fault"),
    ),
    description=("Fault current from the Thevenin equivalent, and every bus voltage "
                 "and branch current during the fault (pre-fault state: the "
                 "linear network, no c-factor)."),
    defaults=lambda net: {"bus": _default_bus(net), "kind": "3ph", "zf_ohm": 0j},
    annotate=_annotate_short_circuit,
))
