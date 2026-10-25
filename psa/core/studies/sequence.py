"""Studies of unsymmetrical conditions: the sequence model and an unbalanced load.

S3e is ``unbalanced_load``: a load of the network draws a different power on
each phase (``factor_a = 0.5`` is half of phase a disconnected). The load is
taken out of the sequence networks, which then give the voltage at its bus
without it and the sequence Thevenin impedances there; its phase admittance
matrix closes the problem, solved like a fault. The voltages it causes
elsewhere (bus 7 in S3e) follow from the impedance-matrix columns.
"""

from __future__ import annotations

import math

import numpy as np

from ..components import Load, display_name
from ..errors import ValidationError
from ..model.elements import load_y_pu
from ..model.sequence import ModelOptions, build_model
from ..model.stamps import Seq
from ..present import matrix_table
from ..results import Annotation, LabeledMatrix, Quantity, Result, Table
from ..solve import SEQS, SequenceNetworks, phase_admittance, solve_shunt_condition, to_phases
from ..study import OptionSpec, Study, StudyContext, register
from ..units import fmt_polar, fmt_real, fmt_rect
from .grid import PHASES, SEQ_INDEX, SEQ_NAME, _bus_label

T_LABELS = ["1", "2", "0"]


# --------------------------------------------------------------------------- #
# Sequence model
# --------------------------------------------------------------------------- #


def _run_seqmodel(ctx: StudyContext) -> Result:
    model = build_model(ctx.net, ModelOptions(energized_only=True))
    res = Result("seqmodel", "Sequence model")
    for s in (Seq.POS, Seq.ZERO):
        try:
            sm = model.seq(s)
        except ValidationError as exc:
            res.warnings.append(f"No {SEQ_NAME[s]}-sequence model: {exc}")
            continue
        t = Table(f"{SEQ_NAME[s].capitalize()} sequence [pu]",
                  ["Component", "Type", "Z [pu]", "y shunt [pu]", "Note"])
        for step in sm.trace:
            t.add(step.name, step.comp_type,
                  fmt_rect(step.z_pu) if step.z_pu is not None else "",
                  fmt_rect(step.ysh_pu) if step.ysh_pu is not None else (
                      fmt_rect(step.y_pu) if step.y_pu is not None else ""),
                  step.note)
        if s == Seq.POS:
            t.note = "the negative sequence is the same, with no source voltages"
        res.tables.append(t)
    return res


register(Study(
    key="seqmodel",
    title="Sequence model",
    menu_group="Model",
    run=_run_seqmodel,
    description=("Every component in the positive- and zero-sequence networks: "
                 "which path it gives and with what impedance."),
))


# --------------------------------------------------------------------------- #
# Unbalanced load (S3e)
# --------------------------------------------------------------------------- #


def _default_load(net) -> str:
    loads = [c.id for c in net.active_components() if isinstance(c, Load)]
    return loads[-1] if loads else ""


def _run_unbalanced_load(ctx: StudyContext) -> Result:
    net, opts = ctx.net, ctx.options
    load_id = opts.get("element") or _default_load(net)
    load = net.components.get(load_id)
    if not isinstance(load, Load):
        raise ValidationError(f"'{load_id}' is not a load.", [load_id] if load_id else [])
    factors = tuple(float(opts.get(f"factor_{p}", 1.0) if opts.get(f"factor_{p}")
                          is not None else 1.0) for p in PHASES)
    bus = load.bus
    report = opts.get("bus") or bus

    model = build_model(net, ModelOptions(energized_only=True, exclude=frozenset({load_id})))
    nets = SequenceNetworks.build(model, SEQS)
    bases = model.bases
    y, _ = load_y_pu(load, bases, net.s_base_mva)
    zn = complex(load.zn_ohm) / bases.z(bus)
    Y_ph = phase_admittance(y, load.connection, factors, zn)
    cond = solve_shunt_condition(nets, bus, Y_ph, -np.eye(3))
    th = cond.thevenin
    label = display_name(load)

    res = Result("unbalanced_load", f"Unbalanced load {label} at bus {th.label}")
    d = Table("Load", ["Quantity", "Value"])
    d.add("Connection", load.connection)
    d.add("Phase factors a, b, c", ", ".join(f"{f:g}" for f in factors))
    d.add("Balanced Z per phase", fmt_rect(1.0 / y, unit="pu") if y else "open")
    d.add("U at its bus without it", fmt_polar(th.u_th, "pu"))
    for s in SEQS:
        z = th.z.get(s)
        d.add(f"Z_th,{SEQ_INDEX[s]} at that bus", "open" if z is None else fmt_rect(z, unit="pu"))
    res.tables.append(d)

    with np.errstate(divide="ignore"):
        Z_ph = np.linalg.inv(Y_ph) if abs(np.linalg.det(Y_ph)) > 0 else None
    if Z_ph is not None:
        from ..solve.shunt import T, T_INV
        res.matrices["Z_ph"] = LabeledMatrix(Z_ph, list(PHASES), list(PHASES),
                                             "impedance_pu", "Z_ph, phase impedances [pu]")
        Z_s = T_INV @ Z_ph @ T
        res.matrices["Z_s"] = LabeledMatrix(Z_s, T_LABELS, T_LABELS, "impedance_pu",
                                            "Z_s = T^-1 Z_ph T [pu]")
        res.tables.append(matrix_table(res.matrices["Z_ph"], "Z_ph"))
        res.tables.append(matrix_table(res.matrices["Z_s"], "Z_s"))

    i = Table(f"Load currents and voltage at bus {th.label}", ["Quantity", "pu", "SI"])
    i_base, u_ph = th.i_base_ka, th.u_base_kv / math.sqrt(3.0)
    for n, s in enumerate(SEQS):
        i.add(f"I_{SEQ_INDEX[s]}", fmt_polar(cond.i_s[n], "pu"),
              fmt_polar(cond.i_s[n] * i_base, "kA"))
    for name, value in zip(PHASES, cond.i_abc):
        i.add(f"I_{name}", fmt_polar(value, "pu"), fmt_polar(value * i_base, "kA"))
    for name, value in zip(PHASES, cond.u_abc):
        i.add(f"U_{name}", fmt_polar(value, "pu"), fmt_polar(value * u_ph, "kV"))
    i.note = "currents drawn by the load; voltages line-to-neutral"
    res.tables.append(i)

    if report not in cond.u_bus_s:
        raise ValidationError(f"Bus '{report}' is not energized in this case.", [report])
    u_s = cond.u_bus_s[report]
    u_abc = to_phases(u_s)
    base = bases.u(report) / math.sqrt(3.0)
    r = Table(f"Voltages at bus {_bus_label(net, report)}", ["Quantity", "pu", "kV"])
    r.add("U pre (load disconnected)",
          fmt_polar(nets.u_pre(report, Seq.POS), "pu"),
          fmt_polar(nets.u_pre(report, Seq.POS) * base, "kV"))
    for n, s in enumerate(SEQS):
        r.add(f"U_{SEQ_INDEX[s]}", fmt_polar(u_s[n], "pu"), "")
    for name, value in zip(PHASES, u_abc):
        r.add(f"U_{name}", fmt_polar(value, "pu"), fmt_polar(value * base, "kV"))
    r.note = "phase voltages line-to-neutral"
    res.tables.append(r)

    v = Table("Bus voltages", ["Bus", "U_a [pu]", "U_b [pu]", "U_c [pu]",
                               "|U_a| [kV]", "|U_b| [kV]", "|U_c| [kV]"])
    for b, us in cond.u_bus_s.items():
        abc = to_phases(us)
        bb = bases.u(b) / math.sqrt(3.0)
        v.add(_bus_label(net, b), *(fmt_polar(x) for x in abc),
              *(fmt_real(abs(x) * bb) for x in abc))
        res.buses[b] = {f"u_{p}": Quantity(x, "voltage_pu", f"U_{p}")
                        for p, x in zip(PHASES, abc)}
        res.buses[b]["u_s"] = Quantity(complex(us[0]), "voltage_pu", "U_1")
    v.note = "line-to-neutral"
    res.tables.append(v)

    res.scalars.update({
        "I1": Quantity(cond.i_s[0], "current_pu", "I_1"),
        "I2": Quantity(cond.i_s[1], "current_pu", "I_2"),
        "I0": Quantity(cond.i_s[2], "current_pu", "I_0"),
        "U_pre_report": Quantity(nets.u_pre(report, Seq.POS), "voltage_pu", "U pre"),
    })
    for n, s in enumerate(SEQS):
        res.scalars[f"U{SEQ_INDEX[s]}_report"] = Quantity(u_s[n], "voltage_pu",
                                                          f"U_{SEQ_INDEX[s]}")
    for name, value in zip(PHASES, u_abc):
        res.scalars[f"U{name}_report_kv"] = Quantity(value * base, "voltage_kv", f"U_{name}")
    res.info.update({"element": load_id, "report_bus": report, "load_bus": bus})
    res.headline = (f"U at bus {_bus_label(net, report)}: "
                    + "   ".join(f"U_{p} = {fmt_polar(x * base, 'kV')}"
                                 for p, x in zip(PHASES, u_abc)))
    return res


def _annotate_unbalanced(res: Result, mode: str = "") -> list[Annotation]:
    out = []
    for bus, q in res.buses.items():
        mags = [abs(q[f"u_{p}"].value) for p in PHASES]
        colour = "warn" if bus in (res.info.get("report_bus"), res.info.get("load_bus")) \
            else "text"
        out.append(Annotation(bus, ("U " + " / ".join(f"{m:.4f}" for m in mags) + " pu",),
                              "bus", colour))
    return out


register(Study(
    key="unbalanced_load",
    title="Unbalanced load",
    menu_group="Network",
    run=_run_unbalanced_load,
    option_fields=(
        OptionSpec("element", "Load", "element", element_types=("load",)),
        OptionSpec("factor_a", "Phase a factor", "float", minimum=0.0,
                   tooltip="Share of the load's phase-a power still connected; "
                           "0.5 is half of it disconnected"),
        OptionSpec("factor_b", "Phase b factor", "float", minimum=0.0),
        OptionSpec("factor_c", "Phase c factor", "float", minimum=0.0),
        OptionSpec("bus", "Report voltages at bus", "bus", optional=True),
    ),
    description=("A load drawing a different power on each phase, solved with the "
                 "sequence networks; the phase voltages it causes at every bus."),
    defaults=lambda net: {"element": _default_load(net), "factor_a": 1.0,
                          "factor_b": 1.0, "factor_c": 1.0, "bus": ""},
    annotate=_annotate_unbalanced,
))
