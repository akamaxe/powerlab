"""The study registry: the extension point for S2, S3 and LF.

A :class:`Study` declares its options (:class:`OptionSpec`), their defaults for
a given network, and how to run itself and annotate the diagram with its
result. The GUI builds its *Calculate* menu, every option dialog and the
diagram badges from :data:`STUDY_REGISTRY`, so adding a study means writing
one core function and calling :func:`register`.

A study runs on a :class:`StudyContext`; :func:`run_case` runs one
:class:`~psa.core.cases.Case` (the study, its options and a network variant).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .analysis import equivalent_impedance, solve_voltages
from .cases import Case, prepare_network
from .components import FieldSpec, Source, buses_of, display_name, is_node_shunt
from .network import Network
from .params import Parameters
from .perunit import PuModel, to_per_unit
from .results import (
    Annotation,
    Result,
    Table,
    annotate_voltages,
    base_tables,
    equivalent_z_result,
    per_unit_table,
    phasor_chain_table,
    two_port_result,
    voltage_result,
)
from .twoport import two_port


@dataclass(frozen=True)
class OptionSpec(FieldSpec):
    """One study option.

    ``kind`` extends :class:`FieldSpec`'s with ``element`` (one component,
    filtered by ``element_types``) and ``elements`` (a set of them, whose
    choices may depend on the other options, see :attr:`Study.dynamic_choices`).
    """

    element_types: tuple[str, ...] = ()
    optional: bool = False          # may stay empty ("(none)")


@dataclass(frozen=True)
class Choice:
    """One entry of a dynamic ``elements`` option."""

    value: str
    label: str
    enabled: bool = True
    tooltip: str = ""


@dataclass
class StudyContext:
    """What a study run sees: the network (already varied by its case) and options."""

    net: Network
    options: dict[str, Any] = field(default_factory=dict)
    _pu: PuModel | None = None

    @property
    def pu(self) -> PuModel:
        """The S1 per-unit model, converted once per run."""
        if self._pu is None:
            self._pu = to_per_unit(self.net)
        return self._pu


@dataclass
class Study:
    """One runnable analysis."""

    key: str
    title: str
    menu_group: str
    run: Callable[[StudyContext], Result]
    option_fields: tuple[FieldSpec, ...] = ()
    description: str = ""
    #: Extra text the options dialog shows above the fields.
    note: str = ""
    #: Sensible options for a network, used to pre-fill the dialog.
    defaults: Callable[[Network], dict] | None = None
    #: ``(net, options, field_name) -> [Choice]`` for ``elements`` options.
    dynamic_choices: Callable[[Network, dict, str], list[Choice]] | None = None
    #: ``(result, mode) -> [Annotation]``: the diagram badges for a result.
    annotate: Callable[[Result, str], list[Annotation]] | None = None

    def __call__(self, net: Network, opts: dict | None = None,
                 pu: PuModel | None = None) -> Result:
        return self.run(StudyContext(net, dict(opts or {}), pu))

    def default_options(self, net: Network) -> dict:
        return dict(self.defaults(net)) if self.defaults else {}


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


def _run_model(ctx: StudyContext) -> Result:
    pu = ctx.pu
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


def _run_two_port(ctx: StudyContext) -> Result:
    net, opts = ctx.net, ctx.options
    bus_i = opts.get("bus_i") or net.base_ref_bus
    bus_j = opts.get("bus_j") or _far_bus(net, bus_i)
    overrides = set(opts.get("include_at_ports") or ())
    tp = two_port(net, bus_i, bus_j, overrides, pu=ctx.pu)
    return two_port_result(tp)


def _two_port_defaults(net: Network) -> dict:
    return {"bus_i": net.base_ref_bus, "bus_j": _far_bus(net, net.base_ref_bus)}


def _port_shunt_choices(net: Network, opts: dict, name: str) -> list[Choice]:
    """The node shunts on the two port buses, which the termination rule excludes."""
    ports = {opts.get("bus_i"), opts.get("bus_j")} - {None, ""}
    out = []
    for comp in net.active_components():
        bs = buses_of(comp)
        if not is_node_shunt(comp) or not bs or bs[0] not in ports:
            continue
        ideal = isinstance(comp, Source) and comp.is_ideal
        out.append(Choice(
            comp.id,
            f"Include {display_name(comp)} ({comp.TYPE}) at bus "
            f"{display_name(net.buses[bs[0]])}",
            enabled=not ideal,
            tooltip=("An ideal voltage source at a port is a short circuit "
                     "across it, so it can never be included." if ideal else "")))
    return out


register(Study(
    key="s1a",
    title="S1a) Two-port matrix",
    menu_group="Assignment S1",
    run=_run_two_port,
    option_fields=(
        OptionSpec("bus_i", "From bus (port 1, source side)", "bus"),
        OptionSpec("bus_j", "To bus (port 2, load side)", "bus"),
        OptionSpec("include_at_ports", "Shunts at the ports", "elements",
                   tooltip="A load or source sitting on a port bus is the "
                           "termination, not part of the two-port, so it is "
                           "excluded. Tick one to include it anyway. Line "
                           "charging is always kept."),
    ),
    description=("ABCD matrix between two buses, with the convention "
                 "[U1; I1] = F [U2; I2]."),
    note=("Convention:  [U₁; I₁] = F · [U₂; I₂]\n"
          "Port 1 is the source side, port 2 the load side. Swapping them "
          "gives a different matrix."),
    defaults=_two_port_defaults,
    dynamic_choices=_port_shunt_choices,
))


# --------------------------------------------------------------------------- #
# S1b: equivalent impedance
# --------------------------------------------------------------------------- #


def _run_equivalent_z(ctx: StudyContext) -> Result:
    net, opts = ctx.net, ctx.options
    bus = opts.get("bus") or net.base_ref_bus
    far = opts.get("far_bus") or _far_bus(net, bus)
    eq = equivalent_impedance(net, bus, pu=ctx.pu, far_bus=far)
    return equivalent_z_result(eq)


register(Study(
    key="s1b",
    title="S1b) Equivalent impedance",
    menu_group="Assignment S1",
    run=_run_equivalent_z,
    option_fields=(
        OptionSpec("bus", "Seen from bus", "bus"),
        OptionSpec("far_bus", "Cross-check against port", "bus", optional=True,
                   tooltip="Optional: recompute through the two-port relation "
                           "and report the residual"),
    ),
    description="Z = U/I looking into the passive network from one bus.",
    defaults=lambda net: {"bus": net.base_ref_bus,
                          "far_bus": _far_bus(net, net.base_ref_bus)},
))


# --------------------------------------------------------------------------- #
# S1c: bus voltages
# --------------------------------------------------------------------------- #


def _run_voltages(ctx: StudyContext) -> Result:
    net, opts, pu = ctx.net, ctx.options, ctx.pu
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
        OptionSpec("bus", "Report voltage at bus", "bus"),
    ),
    description=("Solves the linear network driven by its sources and reports "
                 "every bus voltage and branch flow."),
    defaults=lambda net: {"bus": _far_bus(net, net.base_ref_bus)},
    annotate=annotate_voltages,
))


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #


def run_case(net: Network, case: Case, params: Parameters | None = None) -> Result:
    """Run one case on (a variant of) ``net``.

    The case's options are laid over the study's defaults for the varied
    network; its warnings about stale overrides come first in the result.
    """
    study = STUDY_REGISTRY[case.study]
    work, issues = prepare_network(net, case, params)
    options = study.default_options(work)
    options.update(case.options)
    res = study(work, options)
    res.key = study.key
    res.case_id = case.id
    if case.title:
        res.title = f"{case.title}: {res.title}"
    res.warnings[:0] = [str(i) for i in issues]
    return res


def default_cases() -> list[Case]:
    """One case per registered study, with its default options."""
    return [Case(id=key, study=key) for key in STUDY_REGISTRY]


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
