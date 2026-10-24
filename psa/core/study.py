"""The study registry: the extension point for S2, S3 and LF.

A study declares its option fields and knows how to run itself. The GUI builds
its *Calculate* menu and every option dialog from :data:`STUDY_REGISTRY`, so
adding an assignment means writing one core module and calling
:func:`register` -- no GUI edits at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .analysis import equivalent_impedance, solve_voltages
from .components import FieldSpec
from .network import Network
from .perunit import PuModel, to_per_unit
from .results import (
    Result,
    Table,
    base_tables,
    equivalent_z_result,
    per_unit_table,
    phasor_chain_table,
    two_port_result,
    voltage_result,
)
from .twoport import two_port


@dataclass
class Study:
    """One runnable analysis."""

    key: str
    title: str
    menu_group: str
    run: Callable[[Network, dict, PuModel | None], Result]
    option_fields: tuple[FieldSpec, ...] = ()
    description: str = ""

    def __call__(self, net: Network, opts: dict | None = None,
                 pu: PuModel | None = None) -> Result:
        return self.run(net, dict(opts or {}), pu)


STUDY_REGISTRY: dict[str, Study] = {}


def register(study: Study) -> Study:
    STUDY_REGISTRY[study.key] = study
    return study


def studies_by_group() -> dict[str, list[Study]]:
    out: dict[str, list[Study]] = {}
    for s in STUDY_REGISTRY.values():
        out.setdefault(s.menu_group, []).append(s)
    return out


# --------------------------------------------------------------------------- #
# Model summary (bases + per-unit values)
# --------------------------------------------------------------------------- #


def _run_model(net: Network, opts: dict, pu: PuModel | None = None) -> Result:
    pu = pu or to_per_unit(net)
    res = Result("model", "Per-unit model")
    res.tables.extend(base_tables(pu))
    res.tables.append(per_unit_table(pu))
    res.warnings.extend(str(i) for i in pu.issues if not i.severity == "info")
    return res


register(Study(
    key="model",
    title="Per-unit model",
    menu_group="Model",
    run=_run_model,
    description="Base values per bus and every component converted to per unit.",
))


# --------------------------------------------------------------------------- #
# S1a: two-port matrix
# --------------------------------------------------------------------------- #


def _run_two_port(net: Network, opts: dict, pu: PuModel | None = None) -> Result:
    pu = pu or to_per_unit(net)
    bus_i = opts.get("bus_i") or net.base_ref_bus
    bus_j = opts.get("bus_j") or _far_bus(net, bus_i)
    overrides = set(opts.get("include_at_ports") or ())
    tp = two_port(net, bus_i, bus_j, overrides, pu=pu)
    return two_port_result(tp)


register(Study(
    key="s1a",
    title="S1a) Two-port matrix",
    menu_group="Assignment S1",
    run=_run_two_port,
    option_fields=(
        FieldSpec("bus_i", "From bus (port 1, source side)", "bus"),
        FieldSpec("bus_j", "To bus (port 2, load side)", "bus"),
    ),
    description=("ABCD matrix between two buses, with the convention "
                 "[U1; I1] = F [U2; I2]."),
))


# --------------------------------------------------------------------------- #
# S1b: equivalent impedance
# --------------------------------------------------------------------------- #


def _run_equivalent_z(net: Network, opts: dict, pu: PuModel | None = None) -> Result:
    pu = pu or to_per_unit(net)
    bus = opts.get("bus") or net.base_ref_bus
    far = opts.get("far_bus") or _far_bus(net, bus)
    eq = equivalent_impedance(net, bus, pu=pu, far_bus=far)
    return equivalent_z_result(eq)


register(Study(
    key="s1b",
    title="S1b) Equivalent impedance",
    menu_group="Assignment S1",
    run=_run_equivalent_z,
    option_fields=(
        FieldSpec("bus", "Seen from bus", "bus"),
        FieldSpec("far_bus", "Cross-check against port", "bus",
                  tooltip="Optional: recompute through the two-port relation "
                          "and report the residual"),
    ),
    description="Z = U/I looking into the passive network from one bus.",
))


# --------------------------------------------------------------------------- #
# S1c: bus voltages
# --------------------------------------------------------------------------- #


def _run_voltages(net: Network, opts: dict, pu: PuModel | None = None) -> Result:
    pu = pu or to_per_unit(net)
    lf = solve_voltages(net, pu)
    focus = opts.get("bus") or _far_bus(net, net.base_ref_bus)
    res = voltage_result(lf, pu, focus_bus=focus)

    slack = lf.slack_buses[0] if lf.slack_buses else None
    if len(pu.sources) > 1:
        # I = U/Z_I only holds when this source feeds the passive network alone.
        res.warnings.append(
            "The phasor chain and the two-port cross-check are skipped: the "
            "network has more than one source, so the current at the feeding "
            "bus is not U/Z seen from it. The nodal solve above is still exact.")
    elif slack and focus and focus in lf.u_pu:
        try:
            tp = two_port(net, slack, focus, pu=pu)
            eq = equivalent_impedance(net, slack, pu=pu)
            u_a = lf.u_pu[slack]
            i_a = u_a / eq.z_pu
            u_e, i_e = tp.solve_downstream(u_a, i_a)
            res.tables.insert(0, phasor_chain_table(
                u_a, i_a, u_e, i_e,
                pu.bases.u(slack), pu.bases.u(focus),
                pu.bases.i(slack), pu.bases.i(focus),
                pu.labels.get(slack, slack),
                pu.labels.get(focus, focus)))
            chk = Table("Cross-check", ["Comparison", "Residual"])
            chk.add("U at the far bus: nodal solve vs two-port",
                    f"{abs(lf.u_pu[focus] - u_e):.3e}")
            chk.add("I at the source: nodal solve vs U/Z",
                    f"{abs(lf.i_source_pu[pu.sources[0].comp_id] - i_a):.3e}")
            res.tables.append(chk)
        except Exception as exc:  # noqa: BLE001 - the nodal answer still stands
            res.warnings.append(f"Two-port cross-check unavailable: {exc}")
    return res


register(Study(
    key="s1c",
    title="S1c) Bus voltages",
    menu_group="Assignment S1",
    run=_run_voltages,
    option_fields=(
        FieldSpec("bus", "Report voltage at bus", "bus"),
    ),
    description=("Solves the linear network driven by its sources and reports "
                 "every bus voltage and branch flow."),
))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _far_bus(net: Network, from_bus: str) -> str:
    """The bus furthest from ``from_bus``, a sensible default second port."""
    import networkx as nx

    g = net.graph()
    if from_bus not in g:
        ids = net.active_bus_ids()
        return ids[-1] if ids else ""
    try:
        dist = nx.single_source_shortest_path_length(g, from_bus)
    except Exception:  # noqa: BLE001
        return from_bus
    if not dist:
        return from_bus
    return max(sorted(dist), key=lambda b: dist[b])


def run_all(net: Network, opts: dict | None = None) -> list[Result]:
    """Run every registered study once, sharing a single per-unit conversion."""
    pu = to_per_unit(net)
    out = []
    for study in STUDY_REGISTRY.values():
        out.append(study(net, opts, pu))
    return out
