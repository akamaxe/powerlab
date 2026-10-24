"""Reference networks used by the tests and shipped as example projects.

Each assignment network has a builder parameterised by the A-number, the
bindings that keep the assignment's own algebra in the project file, a layout
that follows the figure in the assignment, and the study cases that answer its
tasks. :func:`example_projects` assembles them; ``tools/make_examples.py``
writes them to ``examples/`` and a test checks the files still match.
"""

from __future__ import annotations

from .cases import Case
from .components import Bus, ExternalGrid, Line, Load, Shunt, Source, Transformer
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
                         z_mode="ik", ik_ka=25.0, cos_phi=0.15, un_kv=70.0))
    net.add(Transformer("T1", bus_hv="b1", bus_lv="b2", name="T1",
                        sn_mva=81.5, u1n_kv=70.0, u2n_kv=40.0, x_pct=8.0))
    line = dict(r_ohm_per_km=0.8, x_ohm_per_km=0.3, b_s_per_km=3e-6)
    for i, j, length in S2_LINES:
        net.add(Line(f"L{i}{j}", bus_from=f"b{i}", bus_to=f"b{j}", name=f"L{i}{j}",
                     length_km=_eval_a(length, a), **line))
    for bus, p_mw, pf in S2_LOADS:
        net.add(Load(f"LD{bus}", bus=f"b{bus}", name=f"LD-bus{bus}",
                     p_mw=_eval_a(p_mw, a), cos_phi=pf, u_nominal_kv=40.0))
    for bus in (3, 5):
        net.add(Shunt(f"SH{bus}", bus=f"b{bus}", name=f"Shunt-bus{bus}",
                      q_mvar=2.0, u_nominal_kv=40.0))
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
        net.add(comp)
    return net


def grid_industry_parameter_bindings(a_number: float = 80.0
                                     ) -> tuple[list[tuple[str, str]], dict[str, str]]:
    project_vars, bindings = s2_parameter_bindings(a_number)
    bindings.update(s1_parameter_bindings(a_number)[1])
    return project_vars, bindings


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
        "L28": _sym(180, 255, 90), "L27": _sym(300, 255, 90),
        "LD4": _sym(740, 100), "LD6": _sym(740, 610), "LD8": _sym(120, 610),
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


def example_projects(a_number: float = 80.0) -> dict:
    """The example projects, by file name: ``{name: Project}``."""
    from .params import Parameters
    from .serialize import Project

    a = float(a_number)
    pv, b = s2_parameter_bindings(a)
    s2 = Project(
        network=build_s2(a), params=Parameters(project_vars=pv, bindings=b),
        layout=s2_layout(), cases=s2_cases(),
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
        ],
        meta={"title": f"EG2100 Assignments S2 and S3, grid and industry, A = {a:g}",
              "notes": "The S2 grid with the S1 industry connected at bus 7. Cases "
                       "S2a-S2c take TI1 out of service (industry disconnected). "
                       "S2d reference (A = 80): U_LDe = 0.9289 < -3.2857 pu = 5.5734 kV."})
    return {"s2_grid.psn": s2, "grid_industry.psn": gi}
