"""Load flow (Assignment LF): Newton-Raphson with the course's bus types.

The study reports what LFa-LFc ask for: the bus types and voltages, the slack
generation and the reactive power of the voltage-controlled buses (with the
susceptance ``B = Q / U^2`` an SVC amounts to), the branch flows and the total
losses, cross-checked by the power balance. LFd is a case that overrides a
load; the constant-B variant of the shunt is a case option.
"""

from __future__ import annotations

from ..components import display_name
from ..model.sequence import ModelOptions, build_model
from ..present import matrix_table
from ..results import Annotation, LabeledMatrix, Quantity, Result, Table
from ..solve.loadflow import MAX_ITERATIONS, SLACK, TOLERANCE, solve_load_flow
from ..study import OptionSpec, Study, StudyContext, register
from ..units import polar_pair

LOAD_MODELS = ("as_set", "constant_power", "constant_impedance")
SHUNT_MODELS = ("as_set", "constant_q", "constant_b")
#: Bus voltages outside this band are flagged on the diagram.
U_BAND = (0.95, 1.05)

CONTROL = {"slack": "slack", "pv": "PU (U set)", "pq": "PQ"}


def _choice(value, allowed, default):
    return value if value in allowed else default


def _f4(x: float) -> str:
    """Four decimals, without the sign of a rounded-off zero."""
    return f"{x:.4f}" if abs(x) >= 5e-5 else "0.0000"


def _run_loadflow(ctx: StudyContext) -> Result:
    net, opts = ctx.net, ctx.options
    load_model = _choice(opts.get("load_model"), LOAD_MODELS, "as_set")
    shunt_model = _choice(opts.get("shunt_model"), SHUNT_MODELS, "as_set")
    tol = float(opts.get("tolerance") or TOLERANCE)
    max_iter = int(opts.get("max_iter") or MAX_ITERATIONS)
    model = build_model(net, ModelOptions(energized_only=True, load_model=load_model,
                                          shunt_model=shunt_model))
    sol = solve_load_flow(model, tol=tol, max_iter=max_iter,
                          q_limits=bool(opts.get("q_limits")))
    sm, sb = sol.sm, net.s_base_mva
    lab = sm.labels

    def name(comp_id: str) -> str:
        comp = net.components.get(comp_id)
        return display_name(comp) if comp is not None else comp_id

    res = Result("loadflow", "Load flow (Newton-Raphson)")
    res.warnings.extend(sol.warnings)
    if not sol.converged:
        res.warnings.append(
            f"The load flow did not converge: largest mismatch "
            f"{sol.max_mismatch:.3e} pu after {sol.iterations} iterations. The values "
            f"below are the last iterate, not a solution.")

    # Bus types and voltages (LFa)
    s_bus = sol.s_bus()
    t = Table("Bus types and voltages",
              ["Bus", "Type", "U [pu]", "U [kV]", "Angle [deg]", "P inj [MW]", "Q inj [MVAr]"])
    for b in sol.buses:
        u = sol.u[b]
        mag, ang = polar_pair(u)
        u_kv = mag * sm.bases.u(b)
        t.add(lab.get(b, b), sol.types[b], f"{mag:.4f}", f"{u_kv:.4f}", _f4(ang),
              _f4(s_bus[b].real * sb), _f4(s_bus[b].imag * sb))
        res.buses[b] = {
            "u": Quantity(u, "voltage_pu", "U"),
            "u_kv": Quantity(u_kv, "ratio", "|U| [kV]"),
            "theta_deg": Quantity(ang, "ratio", "angle [deg]"),
            "s_inj": Quantity(s_bus[b] * sb, "power_mva", "S injected"),
        }
        res.info[f"type:{b}"] = sol.types[b]
    t.note = "P inj, Q inj: net power the bus injects into the branches and the shunts"
    res.tables.append(t)

    # Generation and voltage control (LFb)
    gen = Table("Generation and voltage control",
                ["Component", "Bus", "Bus type", "P [MW]", "Q [MVAr]", "Note"])
    loads = Table("Loads", ["Component", "Bus", "Model", "P [MW]", "Q [MVAr]"])
    shunts = Table("Shunts", ["Component", "Bus", "Model", "Q delivered [MVAr]", "B [pu]"])
    p_gen = p_load = q_gen = q_load = q_shunt = 0.0
    for d in sol.devices:
        inj, s = d.inj, d.s
        mw, mvar = s.real * sb, s.imag * sb
        if inj.reports in ("generator", "grid", "svc"):
            note = CONTROL.get(inj.role, inj.role)
            el = {"s": Quantity(s * sb, "power_mva", "S"),
                  "p_mw": Quantity(mw, "ratio", "P [MW]"),
                  "q_mvar": Quantity(mvar, "ratio", "Q [MVAr]")}
            if inj.reports == "svc":
                u2 = abs(sol.u[inj.bus]) ** 2
                b_pu = s.imag / u2
                b_s = b_pu / sm.bases.z(inj.bus)
                note = f"B = Q/U^2 = {b_pu:.4f} pu = {b_s:.6g} S"
                el["b_pu"] = Quantity(b_pu, "ratio", "B [pu]")
                el["b_s"] = Quantity(b_s, "ratio", "B [S]")
            if inj.bus in sol.limited:
                note += f", held at Q {sol.limited[inj.bus]}"
            gen.add(name(inj.comp_id), lab.get(inj.bus, inj.bus), d.bus_type,
                    _f4(mw), _f4(mvar), note)
            res.elements[inj.comp_id] = el
            p_gen += mw
            q_gen += mvar
        elif inj.reports == "load":
            loads.add(name(inj.comp_id), lab.get(inj.bus, inj.bus), "constant power",
                      f"{-mw:.4f}", f"{-mvar:.4f}")
            res.elements[inj.comp_id] = {"s": Quantity(-s * sb, "power_mva", "S")}
            p_load -= mw
            q_load -= mvar
        elif inj.reports == "shunt":
            u2 = abs(sol.u[inj.bus]) ** 2
            shunts.add(name(inj.comp_id), lab.get(inj.bus, inj.bus), "constant Q",
                       f"{mvar:.4f}", f"{s.imag / u2:.4f}")
            res.elements[inj.comp_id] = {"q_mvar": Quantity(mvar, "ratio", "Q delivered [MVAr]")}
            q_shunt += mvar

    for sh in sm.shunts:
        if sh.bus not in sol.u:
            continue
        taken = abs(sol.u[sh.bus]) ** 2 * sh.y.conjugate() * sb
        if sh.kind == "load":
            loads.add(name(sh.comp_id), lab.get(sh.bus, sh.bus), "constant impedance",
                      f"{taken.real:.4f}", f"{taken.imag:.4f}")
            res.elements[sh.comp_id] = {"s": Quantity(taken, "power_mva", "S")}
            p_load += taken.real
            q_load += taken.imag
        else:
            shunts.add(name(sh.comp_id), lab.get(sh.bus, sh.bus), "constant B",
                       f"{-taken.imag:.4f}", f"{sh.y.imag:.4f}")
            res.elements[sh.comp_id] = {"q_mvar": Quantity(-taken.imag, "ratio",
                                                           "Q delivered [MVAr]")}
            q_shunt -= taken.imag
            p_load += taken.real
    res.tables.append(gen)
    if loads.rows:
        res.tables.append(loads)
    if shunts.rows:
        shunts.note = ("constant Q injects its rating whatever the voltage; constant B "
                       "delivers B U^2")
        res.tables.append(shunts)

    # Branch flows and losses (LFc)
    fl = Table("Branch flows",
               ["Branch", "From", "To", "P from [MW]", "Q from [MVAr]", "P to [MW]",
                "Q to [MVAr]", "P loss [MW]", "Q loss [MVAr]", "I max [kA]"])
    loss = 0j
    for br in sol.branch_flows():
        i_ka = max(abs(br.i_i) * sm.bases.i(br.bus_i), abs(br.i_j) * sm.bases.i(br.bus_j))
        si, sj, sl = br.s_i * sb, br.s_j * sb, br.losses * sb
        fl.add(name(br.comp_id), lab.get(br.bus_i, br.bus_i), lab.get(br.bus_j, br.bus_j),
               f"{si.real:.4f}", f"{si.imag:.4f}", f"{sj.real:.4f}", f"{sj.imag:.4f}",
               f"{sl.real:.4f}", f"{sl.imag:.4f}", f"{i_ka:.4f}")
        res.elements[br.comp_id] = {
            "s_from": Quantity(si, "power_mva", "S from"),
            "s_to": Quantity(sj, "power_mva", "S to"),
            "losses": Quantity(sl, "power_mva", "losses"),
            "i_max_ka": Quantity(i_ka, "ratio", "I max [kA]"),
        }
        loss += sl
    fl.note = "S from / S to: power flowing into the branch at that end"
    res.tables.append(fl)

    bal = Table("Losses and power balance", ["Quantity", "Value"])
    bal.add("P_loss (sum of branch losses)", f"{loss.real:.4f} MW")
    bal.add("Q_loss (series I^2 X minus line charging)", f"{loss.imag:.4f} MVAr")
    bal.add("P generated", f"{p_gen:.4f} MW")
    bal.add("P consumed by loads", f"{p_load:.4f} MW")
    bal.add("balance P_gen - P_load - P_loss", f"{p_gen - p_load - loss.real:.3e} MW")
    bal.add("balance Q_gen + Q_shunts - Q_load - Q_loss",
            f"{q_gen + q_shunt - q_load - loss.imag:.3e} MVAr")
    res.tables.append(bal)

    # The iterations and the Jacobian
    it = Table("Newton-Raphson iterations",
               ["Iteration", "max |dP| [pu]", "max |dQ| [pu]", "at bus", "max step"])
    for row in sol.log:
        it.add(str(row.number), f"{row.max_dp:.3e}", f"{row.max_dq:.3e}",
               lab.get(row.worst_bus, row.worst_bus), f"{row.max_step:.3e}" if row.max_step else "")
    it.note = (f"flat start, tolerance {tol:g} pu; the step is the largest d theta [rad] "
               f"or dU/U applied after that row")
    res.tables.append(it)
    if sol.jacobian_0 is not None and sol.jacobian_0.size:
        j0 = LabeledMatrix(sol.jacobian_0, sol.row_labels, sol.col_labels, kind="ratio",
                           title="Jacobian [H N; J L] at the flat start")
        res.matrices["J0"] = j0
        res.tables.append(matrix_table(j0))
        res.matrices["J"] = LabeledMatrix(sol.jacobian, sol.row_labels, sol.col_labels,
                                          kind="ratio", title="Jacobian at the solution")
    bus_labels = [lab.get(b, b) for b in sol.buses]
    res.matrices["Y"] = LabeledMatrix(sol.Y, bus_labels, bus_labels, kind="admittance_pu",
                                      title="Y-bus of the load flow [pu]")

    res.scalars.update({
        "P_loss_MW": Quantity(loss.real, "ratio", "P_loss [MW]"),
        "Q_loss_MVAr": Quantity(loss.imag, "ratio", "Q_loss [MVAr]"),
        "S_loss": Quantity(loss, "power_mva", "losses"),
        "balance_MW": Quantity(p_gen - p_load - loss.real, "ratio", "P balance [MW]"),
        "iterations": Quantity(float(sol.iterations), "ratio", "iterations"),
        "max_mismatch": Quantity(sol.max_mismatch, "ratio", "max mismatch [pu]"),
    })
    res.info["converged"] = "yes" if sol.converged else "no"

    slack = [d for d in sol.devices if d.bus_type == SLACK and d.inj.role == "slack"]
    head = (f"Converged in {sol.iterations} iterations" if sol.converged
            else f"NOT converged after {sol.iterations} iterations")
    head += f".   P_loss = {loss.real:.4f} MW"
    for d in slack:
        head += (f"   {name(d.inj.comp_id)}: P = {d.s.real * sb:.4f} MW, "
                 f"Q = {d.s.imag * sb:.4f} MVAr")
    res.headline = head
    return res


def _annotate_loadflow(res: Result, mode: str = "") -> list[Annotation]:
    out: list[Annotation] = []
    for bus, q in res.buses.items():
        mag, ang = polar_pair(q["u"].value)
        kind = res.info.get(f"type:{bus}", "")
        colour = "warn" if not (U_BAND[0] <= mag <= U_BAND[1]) else "text"
        out.append(Annotation(bus, (f"{mag:.4f} pu < {ang:.2f}\N{DEGREE SIGN}  {kind}",
                                    f"{q['u_kv'].value:.2f} kV"), "bus", colour))
    for comp_id, q in res.elements.items():
        if "s_from" in q:
            s = q["s_from"].value
            out.append(Annotation(comp_id, (f"{s.real:+.2f} MW {s.imag:+.2f} MVAr",
                                            f"loss {q['losses'].value.real:.3f} MW"),
                                  "branch", "flow"))
        elif "p_mw" in q:
            lines = [f"P {q['p_mw'].value:.2f} MW", f"Q {q['q_mvar'].value:.2f} MVAr"]
            if "b_pu" in q:
                lines = [lines[1], f"B {q['b_pu'].value:.4f} pu"]
            out.append(Annotation(comp_id, tuple(lines), "element", "flow"))
    return out


register(Study(
    key="loadflow",
    title="Load flow (Newton-Raphson)",
    menu_group="Network",
    run=_run_loadflow,
    option_fields=(
        OptionSpec("load_model", "Load model", "choice", choices=LOAD_MODELS,
                   tooltip="as_set: each load's own model. constant_power: every load "
                           "is a PQ injection. constant_impedance: every load is an "
                           "admittance at its nominal voltage."),
        OptionSpec("shunt_model", "Shunt model", "choice", choices=SHUNT_MODELS,
                   tooltip="as_set: each shunt's own model. constant_q: the shunt "
                           "injects its rated Q whatever the voltage. constant_b: the "
                           "rating fixes B at U nominal, so it delivers B U^2."),
        OptionSpec("q_limits", "Enforce reactive limits", "bool",
                   tooltip="A PU bus whose generators or SVC would leave their Q limits "
                           "becomes a PQ bus held at the limit"),
        OptionSpec("tolerance", "Tolerance", "float", "pu", minimum=0.0,
                   tooltip="Largest power mismatch accepted"),
        OptionSpec("max_iter", "Maximum iterations", "int", "", minimum=1),
    ),
    description=("Newton-Raphson in polar form from a flat start: bus types from the "
                 "connected devices (Slack, PU, PQ), every bus voltage, the slack "
                 "generation and the reactive power of the voltage-controlled buses, "
                 "branch flows and losses, with the iteration log and the Jacobian."),
    defaults=lambda net: {"load_model": "as_set", "shunt_model": "as_set",
                          "q_limits": False, "tolerance": TOLERANCE,
                          "max_iter": MAX_ITERATIONS},
    annotate=_annotate_loadflow,
))
