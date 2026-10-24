"""Typed errors and validation issues for the PowerLab core.

Every error carries the ids of the offending network objects so the GUI can
highlight them instead of showing a bare traceback.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class PsaError(Exception):
    """Base class for every error raised by :mod:`psa.core`."""


class ValidationError(PsaError):
    """The network is structurally invalid and cannot be analysed."""

    def __init__(self, message: str, ids: list[str] | None = None) -> None:
        super().__init__(message)
        self.ids = list(ids or [])


class TopologyError(PsaError):
    """The requested operation is impossible for this topology."""

    def __init__(self, message: str, ids: list[str] | None = None) -> None:
        super().__init__(message)
        self.ids = list(ids or [])


class SingularReductionError(PsaError):
    """Kron elimination hit a singular (or hopelessly ill-conditioned) block.

    Usually means one or more buses are floating: no shunt to ground and fewer
    than two branches.
    """

    def __init__(self, message: str, buses: list[str] | None = None,
                 cond: float | None = None) -> None:
        super().__init__(message)
        self.buses = list(buses or [])
        self.cond = cond


class DecoupledPortsError(PsaError):
    """The two port buses are not connected through the included element set."""

    def __init__(self, message: str, bus_i: str = "", bus_j: str = "") -> None:
        super().__init__(message)
        self.bus_i = bus_i
        self.bus_j = bus_j


class ExpressionError(PsaError):
    """A parameter expression could not be parsed or evaluated."""

    def __init__(self, message: str, expr: str = "", target: str = "") -> None:
        super().__init__(message)
        self.expr = expr
        self.target = target


# --------------------------------------------------------------------------- #
# Non-fatal issues
# --------------------------------------------------------------------------- #

ERROR = "error"
WARNING = "warning"
INFO = "info"


@dataclass
class Issue:
    """A non-fatal validation finding, collected rather than raised."""

    severity: str
    code: str
    message: str
    ids: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        where = f" [{', '.join(self.ids)}]" if self.ids else ""
        return f"{self.severity.upper()}: {self.message}{where}"

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR


@dataclass
class BaseConflict:
    """A bus reached with two different voltage bases through different paths."""

    bus: str
    via: str
    expected: float
    found: float

    def as_issue(self) -> Issue:
        return Issue(
            ERROR,
            "base_conflict",
            f"Bus '{self.bus}' already has U_base = {self.expected:g} kV but is also "
            f"reached with {self.found:g} kV through '{self.via}'. Check the "
            f"transformer ratios around this loop.",
            [self.bus, self.via],
        )
