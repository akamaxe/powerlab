"""The per-unit model of the S1 studies, built from the positive sequence.

:func:`to_per_unit` returns a :class:`PuModel`: series branches with their PI
half-shunts, node shunts and sources, plus the :class:`ConversionStep` audit
trail behind the per-unit table. It is a view of the positive-sequence
:class:`~psa.core.model.SequenceModel`, which is where components actually
become numbers (see :mod:`psa.core.model.elements` for the formulas); the
two-port, equivalent-impedance and nodal-solve code still reads this view.

It keeps the strict behaviour of the S1 studies: every active bus must have a
per-unit base, and the network must validate without errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bases import BaseTable
from .errors import Issue
from .model.elements import (  # noqa: F401 - re-exported for callers and tests
    REFER_RTOL,
    line_pu,
    load_power_mva,
    load_y_pu,
    shunt_y_pu,
    transformer_z_pu,
)
from .model.sequence import ModelOptions, SequenceModel, build_model
from .model.stamps import ConversionStep, Seq
from .network import Network


# --------------------------------------------------------------------------- #
# Per-unit primitives
# --------------------------------------------------------------------------- #


@dataclass
class PuSeries:
    """A series branch between two buses, with optional PI half-shunts."""

    comp_id: str
    bus_i: str
    bus_j: str
    z_pu: complex
    ysh_pu: complex = 0j        # at EACH end

    @property
    def y_pu(self) -> complex:
        return 1.0 / self.z_pu


@dataclass
class PuShunt:
    """An admittance from one bus to ground."""

    comp_id: str
    bus: str
    y_pu: complex
    kind: str = "load"          # load | shunt | impedance | source_th


@dataclass
class PuSource:
    """A voltage source. Not a passive element, handled per study."""

    comp_id: str
    bus: str
    u_pu: complex               # magnitude and angle combined
    z_th_pu: complex
    is_slack: bool

    @property
    def is_ideal(self) -> bool:
        return self.z_th_pu == 0


@dataclass
class PuModel:
    """The whole network expressed in per unit."""

    s_base: float
    bases: BaseTable
    series: list[PuSeries] = field(default_factory=list)
    shunts: list[PuShunt] = field(default_factory=list)
    sources: list[PuSource] = field(default_factory=list)
    trace: list[ConversionStep] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    buses: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @classmethod
    def from_sequence(cls, sm: SequenceModel) -> PuModel:
        """The PI-branch view of a sequence model."""
        series = []
        for br in sm.branches:
            if br.is_pi:
                series.append(PuSeries(br.comp_id, br.bus_i, br.bus_j, br.z, br.ysh_i))
            else:
                y = br.matrix()
                z = -1.0 / y[0, 1]
                series.append(PuSeries(br.comp_id, br.bus_i, br.bus_j, complex(z),
                                       complex(y[0, 0] + y[0, 1])))
        return cls(
            s_base=sm.s_base, bases=sm.bases, series=series,
            shunts=[PuShunt(s.comp_id, s.bus, s.y, s.kind) for s in sm.shunts],
            sources=[PuSource(s.comp_id, s.bus, s.e, s.z, s.is_slack)
                     for s in sm.sources],
            trace=list(sm.trace), labels=dict(sm.labels), buses=list(sm.buses),
            issues=list(sm.issues))

    def step(self, comp_id: str) -> ConversionStep | None:
        for s in self.trace:
            if s.comp_id == comp_id:
                return s
        return None

    def series_of(self, comp_id: str) -> PuSeries | None:
        for s in self.series:
            if s.comp_id == comp_id:
                return s
        return None

    def shunt_of(self, comp_id: str) -> PuShunt | None:
        for s in self.shunts:
            if s.comp_id == comp_id:
                return s
        return None


# --------------------------------------------------------------------------- #
# Whole-network conversion
# --------------------------------------------------------------------------- #


def to_per_unit(net: Network, bases: BaseTable | None = None,
                exclude: set[str] | None = None) -> PuModel:
    """Convert every active component of ``net`` into per-unit primitives."""
    model = build_model(net, ModelOptions(exclude=frozenset(exclude or ())),
                        bases=bases)
    return PuModel.from_sequence(model.seq(Seq.POS))
