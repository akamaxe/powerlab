"""Reference networks used by the tests and shipped as example projects.

Each assignment network has a builder parameterised by the A-number, the
bindings that keep the assignment's own algebra in the project file, a layout
that follows the figure in the assignment, and the study cases that answer its
tasks. :func:`example_projects` assembles them; ``tools/make_examples.py``
writes them to ``examples/`` and a test checks the files still match.
"""

from __future__ import annotations

from .cases import Case
from .components import (
    SVC,
    Bus,
    ExternalGrid,
    Generator,
    Line,
    Load,
    Shunt,
    Source,
    Transformer,
)
from .network import Network
from .params import safe_eval


def build_s1(a_number: float = 80.0) -> Network:
    """EG2100 Assignment S1: a factory fed from a 40 kV grid.

    ``a_number`` is the student's A-number, which parameterises the cable
    lengths and both load powers exactly as the assignment states them::

        bus a --TI1-- bus b == LIbc (2 cables) == bus c -- LIcd -- bus d --TI2-- bus e
                        |                                                        |
                       LDb                                                      LDe

    The published answer for ``a_number = 0`` is ``Z_I = 22.5156 + 11.0767i pu``,
    which makes this an independent check of the whole pipeline.
    """
    a = float(a_number)
    net = Network(
        s_base_mva=10.0,
        f_hz=50.0,
        base_ref_bus="bus_a",
        base_ref_u_kv=40.0,
        meta={"title": f"EG2100 Assignment S1, A = {a:g}"},
    )

    net.add(Bus("bus_a", name="a"))
    net.add(Bus("bus_b", name="b"))
    net.add(Bus("bus_c", name="c", kind="junction"))
    net.add(Bus("bus_d", name="d", kind="junction"))
    net.add(Bus("bus_e", name="e"))

    net.add(Source("SRC", bus="bus_a", name="Grid", u_pu=1.0, theta_deg=0.0))

    net.add(Transformer("TI1", bus_hv="bus_a", bus_lv="bus_b", name="TI1",
                        sn_mva=6.0, u1n_kv=40.0, u2n_kv=12.0, x_pct=7.0))
    net.add(Transformer("TI2", bus_hv="bus_d", bus_lv="bus_e", name="TI2",
                        sn_mva=5.0, u1n_kv=12.0, u2n_kv=6.0, x_pct=8.0))

    cable = dict(r_ohm_per_km=0.16, x_ohm_per_km=0.08, b_s_per_km=3.0e-6)
    net.add(Line("LIbc", bus_from="bus_b", bus_to="bus_c", name="LIbc",
                 length_km=(217.0 + a) / 1000.0, n_parallel=2, **cable))
    net.add(Line("LIcd", bus_from="bus_c", bus_to="bus_d", name="LIcd",
                 length_km=(217.0 + a) / 1000.0, n_parallel=1, **cable))

    net.add(Load("LDb", bus="bus_b", name="LDb",
                 p_mw=(117.0 - a) / 1000.0, cos_phi=0.9, pf_sign="lag",
                 u_nominal_kv=12.0))
    net.add(Load("LDe", bus="bus_e", name="LDe",
                 p_mw=(243.0 + a) / 1000.0, cos_phi=0.9, pf_sign="lag",
                 u_nominal_kv=6.0))

    return net


def s1_parameter_bindings(a_number: float = 80.0) -> tuple[list[tuple[str, str]],
                                                           dict[str, str]]:
    """Project variables and field bindings that reproduce :func:`build_s1`.

    Returns ``(project_vars, bindings)`` ready to drop into
    :class:`~psa.core.params.Parameters`, so the example project carries the
    assignment's own algebra rather than pre-evaluated numbers.
    """
    project_vars = [("A", str(float(a_number)))]
    bindings = {
        "LIbc.length_km": "(217 + A)/1000",
        "LIcd.length_km": "(217 + A)/1000",
        "LIbc.n_parallel": "2",
        "LDb.p_mw": "(117 - A)/1000",
        "LDe.p_mw": "(243 + A)/1000",
    }
    return project_vars, bindings


#: Backwards-compatible alias used by the tests.
def build_s1_a80() -> Network:
    return build_s1(80.0)


# --------------------------------------------------------------------------- #
# Assignment S2: the meshed 40 kV grid
# --------------------------------------------------------------------------- #

#: (bus i, bus j, length expression in km) for the eight 40 kV lines.
S2_LINES = (
    (2, 8, "28.5 + 0.5*A"), (2, 3, "15 + 0.4*A"), (3, 4, "15 + 0.6*A"),
    (4, 5, "20 + 0.2*A"), (5, 6, "15 + 0.5*A"), (6, 7, "17 + 0.6*A"),
    (7, 8, "12 + 0.2*A"), (2, 7, "25 + 0.5*A"),
)
#: (bus, P expression in MW, cos phi) for the three impedance loads.
S2_LOADS = ((4, "(2017 + 2*A)/1000", 0.95), (6, "(2517 - 2*A)/1000", 0.98),
            (8, "(3017 - 2*A)/1000", 0.95))


def _eval_a(expr: str, a: float) -> float:
    return float(safe_eval(expr, {"A": a}))


def build_s2(a_number: float = 80.0) -> Network:
    """EG2100 Assignment S2: the meshed 40 kV grid fed from 70 kV through T1.

    Bus 7 is bus a of S1. The 70 kV system is an external grid given by its
    fault level, 25 kA at cos phi 0.15 lagging with 70 kV at bus 1, exactly as
    the assignment states it. Published answer for A = 0:
    ``U_Th7 = 0.9732 < -1.4224 pu``, ``Z_Th7 = 0.0649 + 0.0356i pu``.
    """
    a = float(a_number)
    net = Network(s_base_mva=10.0, f_hz=50.0, base_ref_bus="b7", base_ref_u_kv=40.0,
                  meta={"title": f"EG2100 Assignment S2, A = {a:g}"})
    for n in range(1, 9):
        net.add(Bus(f"b{n}", name=str(n)))
    net.add(ExternalGrid("GRID", bus="b1", name="Grid 70 kV", u_pu=1.0,
                         z_mode="ik", ik_ka=25.0, cos_phi=0.15, un_kv=70.0,
                         z0_mode="ik1", ik1_ka=13.0, cos_phi0=0.15))
    net.add(Transformer("T1", bus_hv="b1", bus_lv="b2", name="T1",
                        sn_mva=81.5, u1n_kv=70.0, u2n_kv=40.0, x_pct=8.0))
    # S3: grid lines have r0 = 2.5 r, x0 = 2 x and b0 = 0.5 b.
    line = dict(r_ohm_per_km=0.8, x_ohm_per_km=0.3, b_s_per_km=3e-6,
                z0_mode="factors", r0_factor=2.5, x0_factor=2.0, b0_factor=0.5)
    for i, j, length in S2_LINES:
        net.add(Line(f"L{i}{j}", bus_from=f"b{i}", bus_to=f"b{j}", name=f"L{i}{j}",
                     length_km=_eval_a(length, a), **line))
    # S3 connections: LD6 and the bus-3 shunt are delta, the rest grounded star.
    for bus, p_mw, pf in S2_LOADS:
        net.add(Load(f"LD{bus}", bus=f"b{bus}", name=f"LD-bus{bus}",
                     p_mw=_eval_a(p_mw, a), cos_phi=pf, u_nominal_kv=40.0,
                     connection="D" if bus == 6 else "Y0"))
    for bus in (3, 5):
        net.add(Shunt(f"SH{bus}", bus=f"b{bus}", name=f"Shunt-bus{bus}",
                      q_mvar=2.0, u_nominal_kv=40.0,
                      connection="D" if bus == 3 else "Y0"))
    return net


def s2_parameter_bindings(a_number: float = 80.0) -> tuple[list[tuple[str, str]],
                                                           dict[str, str]]:
    """Project variables and bindings that reproduce :func:`build_s2`."""
    bindings = {f"L{i}{j}.length_km": length for i, j, length in S2_LINES}
    bindings.update({f"LD{bus}.p_mw": p_mw for bus, p_mw, _ in S2_LOADS})
    return [("A", str(float(a_number)))], bindings


def build_grid_industry(a_number: float = 80.0) -> Network:
    """The S2 grid with the S1 industry connected at bus 7 (= bus a).

    The network of S2d and S3d-S3e. The industry's own source of S1 is gone:
    the grid feeds it. Cases that need the industry disconnected (S2a-S2c,
    S3a-S3c) take TI1 out of service.
    """
    a = float(a_number)
    net = build_s2(a)
    net.meta = {"title": f"EG2100 Assignments S2 and S3, grid and industry, A = {a:g}"}
    s1 = build_s1(a)
    for bus in list(s1.buses.values())[1:]:          # bus a is bus 7
        net.add(bus)
    for comp in s1.components.values():
        if comp.TYPE == "source":
            continue
        if getattr(comp, "bus_hv", None) == "bus_a":
            comp.bus_hv = "b7"
        if comp.TYPE == "line":                  # S3: cables Z0 = 3 Z1, y0 = 0.5 y1
            comp.z0_mode = "factors"
            comp.r0_factor, comp.x0_factor, comp.b0_factor = 3.0, 3.0, 0.5
        net.add(comp)
    net.get("TI2").vector_group = "Dyn"          # S3: TI2 is delta-grounded star
    return net


def grid_industry_parameter_bindings(a_number: float = 80.0
                                     ) -> tuple[list[tuple[str, str]], dict[str, str]]:
    project_vars, bindings = s2_parameter_bindings(a_number)
    bindings.update(s1_parameter_bindings(a_number)[1])
    return project_vars, bindings


# --------------------------------------------------------------------------- #
# Assignment LF: the 220 kV transmission network
# --------------------------------------------------------------------------- #

#: (bus i, bus j, length in km) for the nine 220 kV lines.
LF_LINES = ((1, 2, 58.5), (2, 8, 108.5), (2, 3, 100.0), (3, 4, 100.0), (4, 5, 150.0),
            (5, 6, 150.0), (6, 7, 90.0), (7, 8, 90.0), (2, 7, 200.0))
#: (bus, P expression in MW, Q in MVAr) for the three constant-power loads.
LF_LOADS = ((4, "158.5 + 0.1*A", 30.0), (6, "230 - 0.5*A", 10.0), (8, "210", 30.0))
LF_SHUNT_Q = "0.2 + 0.1*A"


def build_lf(a_number: float = 80.0) -> Network:
    """EG2100 Assignment LF: eight 220 kV buses, slack at bus 1.

    Bus 3 holds 220 kV with a controllable shunt (an SVC, a PU bus with
    P = 0), the generator at bus 7 delivers 208.5 MW at 220 kV, and the loads
    at buses 4, 6 and 8 are constant power. The shunt at bus 5 injects its
    rated Q whatever the voltage, as the assignment reads; ``shunt_model =
    constant_b`` in a case makes it a susceptance rated at 220 kV instead.
    Reference (A = 80): P_G1 = 380.1658 MW, P_loss = 22.1658 MW; A = 0:
    P_loss = 26.4240 MW.
    """
    a = float(a_number)
    net = Network(s_base_mva=100.0, f_hz=50.0, base_ref_bus="b1", base_ref_u_kv=220.0,
                  meta={"title": f"EG2100 Assignment LF, A = {a:g}"})
    for n in range(1, 9):
        net.add(Bus(f"b{n}", name=str(n)))
    net.add(Generator("G1", bus="b1", name="G1", control="slack", u_set_pu=1.0,
                      theta_deg=0.0))
    net.add(Generator("G7", bus="b7", name="G7", control="pv", p_mw=208.5, u_set_pu=1.0))
    net.add(SVC("SVC3", bus="b3", name="Sh3", u_set_pu=1.0))
    line = dict(r_ohm_per_km=0.05, x_ohm_per_km=0.5, b_s_per_km=3.6e-6)
    for i, j, length in LF_LINES:
        net.add(Line(f"L{i}{j}", bus_from=f"b{i}", bus_to=f"b{j}", name=f"L{i}{j}",
                     length_km=length, **line))
    for bus, p_mw, q_mvar in LF_LOADS:
        net.add(Load(f"LD{bus}", bus=f"b{bus}", name=f"LD{bus}", p_mw=_eval_a(p_mw, a),
                     spec="p_q", q_mvar=q_mvar, model="constant_power", u_nominal_kv=220.0))
    net.add(Shunt("SH5", bus="b5", name="Sh5", q_mvar=_eval_a(LF_SHUNT_Q, a),
                  u_nominal_kv=220.0, model="constant_q"))
    return net


def lf_parameter_bindings(a_number: float = 80.0) -> tuple[list[tuple[str, str]],
                                                           dict[str, str]]:
    """Project variables and bindings that reproduce :func:`build_lf`."""
    bindings = {f"LD{bus}.p_mw": p_mw for bus, p_mw, _ in LF_LOADS if p_mw != "210"}
    bindings["SH5.q_mvar"] = LF_SHUNT_Q
    return [("A", str(float(a_number)))], bindings


# --------------------------------------------------------------------------- #
# Layouts (the figures of the assignment) and cases (its tasks)
# --------------------------------------------------------------------------- #


def _bus(x, y, orientation="V", length=60):
    return {"x": x, "y": y, "orientation": orientation, "length": length}


def _sym(x, y, rotation=0):
    return {"x": x, "y": y, "rotation": rotation, "mirrored": False}


def s2_layout() -> dict[str, dict]:
    """Figure 2: bus 1 and T1 top left, the ring 2-3-4 / 4-5-6 / 6-7-8 clockwise."""
    return {
        "b1": _bus(0, 0), "b2": _bus(180, 0), "b3": _bus(420, 0), "b4": _bus(660, 0),
        "b5": _bus(660, 270, "H", 80), "b6": _bus(660, 510), "b7": _bus(420, 510),
        "b8": _bus(180, 510),
        "GRID": _sym(-90, 0), "T1": _sym(90, 0),
        "L23": _sym(300, 0), "L34": _sym(540, 0),
        "L45": _sym(660, 140, 90), "L56": _sym(660, 390, 90),
        "L67": _sym(540, 510, 180), "L78": _sym(300, 510, 180),
        "L28": _sym(60, 255, 90), "L27": _sym(300, 255, 90),
        "LD4": _sym(760, 40), "LD6": _sym(740, 610), "LD8": _sym(120, 610),
        "SH3": _sym(420, 110), "SH5": _sym(560, 350),
    }


def industry_layout() -> dict[str, dict]:
    """Figure 1 below bus 7: TI1 down to bus b, then the feeder to bus e."""
    return {
        "TI1": _sym(420, 630, 90),
        "bus_b": _bus(420, 760), "bus_c": _bus(660, 760), "bus_d": _bus(900, 760),
        "bus_e": _bus(1140, 760),
        "LIbc": _sym(540, 760), "LIcd": _sym(780, 760), "TI2": _sym(1020, 760),
        "LDb": _sym(420, 870), "LDe": _sym(1140, 870),
    }


def lf_layout() -> dict[str, dict]:
    """The LF figure: buses 1-2-3-4 on top, 8-7-6 below, bus 5 between 4 and 6."""
    return {
        "b1": _bus(0, 0), "b2": _bus(300, 0, length=80), "b3": _bus(540, 0),
        "b4": _bus(780, 0), "b5": _bus(780, 240, "H", 80), "b6": _bus(780, 480),
        "b7": _bus(540, 480, length=80), "b8": _bus(300, 480, length=80),
        "G1": _sym(-70, 90), "G7": _sym(600, 580), "SVC3": _sym(600, 110),
        "L12": _sym(150, 0), "L23": _sym(420, 0), "L34": _sym(660, 0),
        "L45": _sym(780, 125, 90), "L56": _sym(780, 360, 90),
        "L67": _sym(660, 480, 180), "L78": _sym(420, 500, 180),
        "L28": _sym(300, 240, 90), "L27": _sym(520, 240, 90),
        "LD4": _sym(900, 60), "LD6": _sym(900, 580), "LD8": _sym(240, 580),
        "SH5": _sym(720, 340),
    }


def lf_cases() -> list[Case]:
    """LFa-LFc in one run, LFd, and the constant-B reading of the bus-5 shunt."""
    return [
        Case("LFa", "loadflow", "LFa-LFc) Load flow", {}),
        Case("LFd", "loadflow", "LFd) Load flow with P_LD4 increased by 10 %", {},
             overrides={"LD4.p_mw": "1.1*value"}),
        Case("LFb_rated", "loadflow",
             "LF variant) Shunt at bus 5 as a susceptance rated at 220 kV",
             {"shunt_model": "constant_b"}),
    ]


def s2_cases(exclude: tuple[str, ...] = ()) -> list[Case]:
    """S2a-S2c, with ``exclude`` taking the industry out where it is connected."""
    ex = list(exclude)
    return [
        Case("S2a", "ybus", "S2a) Admittance matrix with the 70 kV Thevenin source",
             {"sources": "internal_node"}, exclude=ex),
        Case("S2b", "thevenin", "S2b) Thevenin equivalent at bus 7", {"bus": "b7"},
             exclude=ex),
        Case("S2c", "short_circuit", "S2c) Three-phase short circuit at bus 7",
             {"bus": "b7", "kind": "3ph", "zf_ohm": 0j}, exclude=ex),
    ]


def s3_cases(exclude: tuple[str, ...] = ()) -> list[Case]:
    """S3a-S3c, with ``exclude`` taking the industry out where it is connected."""
    ex = list(exclude)
    return [
        Case("S3a", "ybus", "S3a) Zero-sequence admittance matrix",
             {"sequence": "zero", "sources": "grounded"}, exclude=ex),
        Case("S3b", "thevenin", "S3b) Sequence Thevenin equivalents at bus 7",
             {"bus": "b7"}, exclude=ex),
        Case("S3c", "short_circuit", "S3c) Single line-to-ground fault at bus 7",
             {"bus": "b7", "kind": "slg", "zf_ohm": 0j, "zg_ohm": 0j}, exclude=ex),
    ]


def example_projects(a_number: float = 80.0) -> dict:
    """The example projects, by file name: ``{name: Project}``."""
    from .params import Parameters
    from .serialize import Project

    a = float(a_number)
    pv, b = s2_parameter_bindings(a)
    s2 = Project(
        network=build_s2(a), params=Parameters(project_vars=pv, bindings=b),
        layout=s2_layout(), cases=s2_cases() + s3_cases(),
        meta={"title": f"EG2100 Assignment S2, A = {a:g}",
              "notes": "Meshed 40 kV grid fed from 70 kV. Reference answers (A = 80): "
                       "U_Th7 = 0.9348 < -3.0196 pu, Z_Th7 = 0.1645 + 0.0677i pu, "
                       "I_sc7 = 0.7584 kA. Set A = 0 for the answer printed in the "
                       "assignment."})
    pv, b = grid_industry_parameter_bindings(a)
    gi = Project(
        network=build_grid_industry(a), params=Parameters(project_vars=pv, bindings=b),
        layout={**s2_layout(), **industry_layout()},
        cases=s2_cases(exclude=("TI1",)) + [
            Case("S2d", "linear", "S2d) Industry voltages with LDe at 50 %",
                 {"bus": "bus_e"}, overrides={"LDe.p_mw": "0.5*value"}),
        ] + s3_cases(exclude=("TI1",)) + [
            Case("S3d", "thevenin", "S3d) Sequence Thevenin equivalents at bus e",
                 {"bus": "bus_e"}, exclude=["LDe"]),
            Case("S3e", "unbalanced_load",
                 "S3e) Bus 7 voltages, half of phase a of LDe disconnected",
                 {"element": "LDe", "factor_a": 0.5, "factor_b": 1.0, "factor_c": 1.0,
                  "bus": "b7"}),
        ],
        meta={"title": f"EG2100 Assignments S2 and S3, grid and industry, A = {a:g}",
              "notes": "The S2 grid with the S1 industry connected at bus 7. Cases "
                       "S2a-S2c and S3a-S3c take TI1 out of service (industry "
                       "disconnected). References (A = 80): S2d U_LDe = 0.9289 < "
                       "-3.2857 pu = 5.5734 kV; S3c I_f = 0.5214 kA; S3e U7 = 21.4825, "
                       "21.4474, 21.4503 kV."})
    pv, b = lf_parameter_bindings(a)
    lf = Project(
        network=build_lf(a), params=Parameters(project_vars=pv, bindings=b),
        layout=lf_layout(), cases=lf_cases(),
        meta={"title": f"EG2100 Assignment LF, A = {a:g}",
              "notes": "220 kV network for the Newton-Raphson load flow. References "
                       "(A = 80): P_G1 = 380.1658 MW, Q_G1 = 28.8537 MVAr, Q_sh3 = "
                       "46.6418 MVAr, Q_G7 = 28.9567 MVAr, P_loss = 22.1658 MW; LFd "
                       "P_loss = 24.3993 MW; shunt as a susceptance: P_loss = 22.1662 MW. "
                       "A = 0: P_loss = 26.4240 MW."})
    return {"s2_grid.psn": s2, "grid_industry.psn": gi, "lf_220kv.psn": lf}
