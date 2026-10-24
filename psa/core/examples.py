"""Reference networks used by the tests and shipped as example projects."""

from __future__ import annotations

from .components import Bus, Line, Load, Source, Transformer
from .network import Network


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
