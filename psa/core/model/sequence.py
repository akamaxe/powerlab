"""Sequence networks built from element stamps.

:func:`build_model` validates the network, propagates the per-unit bases and
returns a :class:`NetworkModel`; :meth:`NetworkModel.seq` builds (and caches)
the :class:`SequenceModel` of one sequence by asking every active component's
element model for its stamps. Every solver reads a :class:`SequenceModel`.

Only the positive sequence exists so far; the negative and zero sequences
arrive with the unsymmetrical studies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import networkx as nx
import numpy as np

from ..bases import BaseTable, propagate_bases
from ..components import Source, buses_of, display_name
from ..errors import INFO, Issue, ValidationError
from ..network import Network
from .elements import StampContext, Stamps, model_for
from .stamps import BranchStamp, ConversionStep, Seq, ShuntStamp, SourceStamp

SourceMode = Literal["exclude", "grounded", "internal_node"]

#: Suffix of the extra node that ``internal_node`` adds behind a source impedance.
INTERNAL_NODE_SUFFIX = ":int"


@dataclass(frozen=True)
class ModelOptions:
    """How a network is turned into sequence models."""

    #: Component ids left out entirely (a study's exclusions).
    exclude: frozenset[str] = frozenset()
    #: Keep only the buses of islands that contain a source. When false every
    #: active bus with a base is kept, which the S1 studies need: they analyse
    #: the passive network with its source switched off.
    energized_only: bool = False


@dataclass
class SequenceModel:
    """One sequence network in per unit."""

    seq: Seq
    s_base: float
    bases: BaseTable
    buses: list[str]
    labels: dict[str, str]
    branches: list[BranchStamp] = field(default_factory=list)
    shunts: list[ShuntStamp] = field(default_factory=list)
    sources: list[SourceStamp] = field(default_factory=list)
    trace: list[ConversionStep] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def ybus(self, sources: SourceMode = "exclude",
             bus_order: list[str] | None = None,
             exclude: set[str] | frozenset[str] = frozenset(),
             ) -> tuple[np.ndarray, dict[str, int]]:
        """Bus admittance matrix and the bus-to-index map.

        ``sources`` decides what a source with a finite impedance contributes:
        nothing (``exclude``, the passive network), a shunt ``1/z`` at its bus
        (``grounded``: the Norton form, with the source node tied to ground),
        or an extra node ``<id>:int`` behind a series branch ``z``
        (``internal_node``, the way the MATLAB S2 solution writes its 9x9
        matrix). Ideal sources never contribute: as an admittance they are a
        short circuit to ground and must be boundary conditions.
        """
        order = list(bus_order) if bus_order is not None else list(self.buses)
        if sources == "internal_node":
            order += [s.comp_id + INTERNAL_NODE_SUFFIX for s in self.sources
                      if s.comp_id not in exclude and not s.is_ideal
                      and s.bus in order]
        index = {b: i for i, b in enumerate(order)}
        Y = np.zeros((len(order), len(order)), dtype=complex)

        def add_branch(i: int, j: int, m: np.ndarray) -> None:
            Y[i, i] += m[0, 0]
            Y[j, j] += m[1, 1]
            Y[i, j] += m[0, 1]
            Y[j, i] += m[1, 0]

        for br in self.branches:
            if br.comp_id in exclude or br.bus_i not in index or br.bus_j not in index:
                continue
            add_branch(index[br.bus_i], index[br.bus_j], br.matrix())

        for sh in self.shunts:
            if sh.comp_id in exclude or sh.bus not in index:
                continue
            Y[index[sh.bus], index[sh.bus]] += sh.y

        if sources != "exclude":
            for src in self.sources:
                if src.comp_id in exclude or src.is_ideal or src.bus not in index:
                    continue
                k = index[src.bus]
                if sources == "grounded":
                    Y[k, k] += 1.0 / src.z
                else:
                    add_branch(index[src.comp_id + INTERNAL_NODE_SUFFIX], k,
                               BranchStamp(src.comp_id, "", "", z=src.z).matrix())
        return Y, index


@dataclass
class NetworkModel:
    """A validated network with its bases, ready to build sequence models."""

    net: Network
    bases: BaseTable
    options: ModelOptions
    buses: list[str]
    #: Non-blocking findings of :meth:`Network.validate` and of the island
    #: analysis. Kept apart from :attr:`SequenceModel.issues`, which only
    #: holds what the conversion itself found.
    validation_issues: list[Issue] = field(default_factory=list)
    _cache: dict[Seq, SequenceModel] = field(default_factory=dict, repr=False)

    def seq(self, s: Seq = Seq.POS) -> SequenceModel:
        if s not in self._cache:
            self._cache[s] = self._build(s)
        return self._cache[s]

    def _build(self, s: Seq) -> SequenceModel:
        ctx = StampContext(bases=self.bases, s_base=self.net.s_base_mva, seq=s)
        out = Stamps()
        keep = set(self.buses)
        for comp in self.net.active_components(set(self.options.exclude)):
            if not all(b in keep for b in buses_of(comp)):
                continue
            model_for(comp).stamp(comp, ctx, out)
        return SequenceModel(
            seq=s, s_base=self.net.s_base_mva, bases=self.bases,
            buses=list(self.buses),
            labels={b.id: display_name(b) for b in self.net.buses.values()},
            branches=out.branches, shunts=out.shunts, sources=out.sources,
            trace=out.trace, issues=out.issues)


def energized_buses(net: Network, exclude: set[str] | frozenset[str] = frozenset()
                    ) -> set[str]:
    """Buses in an island (over in-service branches) that contains a source."""
    g = net.graph(set(exclude))
    source_buses = {c.bus for c in net.active_components(set(exclude))
                    if isinstance(c, Source)}
    out: set[str] = set()
    for island in nx.connected_components(g):
        if island & source_buses:
            out |= island
    return out


def build_model(net: Network, options: ModelOptions | None = None,
                bases: BaseTable | None = None) -> NetworkModel:
    """Validate ``net``, propagate its bases and prepare its sequence models.

    Raises :class:`~psa.core.errors.ValidationError` for structural errors and
    for base errors on the buses the model keeps.
    """
    options = options or ModelOptions()
    issues = net.validate()
    errors = [i for i in issues if i.is_error]
    if errors:
        raise ValidationError(
            "The network is not ready to analyse:\n  "
            + "\n  ".join(str(e) for e in errors),
            [i for e in errors for i in e.ids])
    warnings = [i for i in issues if not i.is_error]

    bases = bases or propagate_bases(net)
    active = net.active_bus_ids()
    if options.energized_only:
        live = energized_buses(net, options.exclude)
        dropped = [b for b in active if b not in live]
        for b in dropped:
            warnings.append(Issue(
                INFO, "de_energized",
                f"Bus '{display_name(net.buses[b])}' is not connected to any "
                f"source, so the studies leave it out.", [b]))
        active = [b for b in active if b in live]

    base_errors = [i for i in bases.issues()
                   if i.is_error and (i.code != "unreachable" or i.ids[0] in active)]
    if base_errors:
        raise ValidationError(
            "Per-unit bases could not be established:\n  "
            + "\n  ".join(str(i) for i in base_errors),
            [i for issue in base_errors for i in issue.ids])

    buses = [b for b in active if b in bases.u_base_kv]
    return NetworkModel(net=net, bases=bases, options=options, buses=buses,
                        validation_issues=warnings)
