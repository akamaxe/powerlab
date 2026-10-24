"""The primitives element models write into a sequence network.

Every component contributes, per sequence, some of:

* :class:`BranchStamp`: a two-port admittance between two buses, either as a
  PI section (series ``z`` plus a shunt at each end) or as a general 2x2
  matrix ``[I_i; I_j] = y @ [U_i; U_j]``. The general form is what off-nominal
  taps, phase shifters and Δ windings in the zero sequence need.
* :class:`ShuntStamp`: an admittance from a bus to ground.
* :class:`SourceStamp`: a voltage source ``e`` behind an impedance ``z``
  (``z == 0`` is an ideal source and has to be handled as a boundary
  condition, never as an admittance).

plus one :class:`ConversionStep` per component for the audit trail that the
per-unit table shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np


class Seq(IntEnum):
    """Symmetrical-component sequence, numbered as in the course."""

    ZERO = 0
    POS = 1
    NEG = 2


@dataclass(eq=False)
class BranchStamp:
    """A two-port admittance between ``bus_i`` and ``bus_j``."""

    comp_id: str
    bus_i: str
    bus_j: str
    z: complex | None = None        # series impedance of a PI section
    ysh_i: complex = 0j             # PI shunt at the bus_i end
    ysh_j: complex = 0j             # PI shunt at the bus_j end
    y: np.ndarray | None = None     # general 2x2 form; overrides the PI fields

    @property
    def is_pi(self) -> bool:
        return self.y is None

    def matrix(self) -> np.ndarray:
        """The 2x2 admittance matrix ``[[y_ii, y_ij], [y_ji, y_jj]]``."""
        if self.y is not None:
            return self.y
        ys = 1.0 / self.z
        return np.array([[ys + self.ysh_i, -ys], [-ys, ys + self.ysh_j]],
                        dtype=complex)


@dataclass(eq=False)
class ShuntStamp:
    """An admittance from ``bus`` to ground."""

    comp_id: str
    bus: str
    y: complex
    kind: str = "load"              # load | shunt | impedance


@dataclass(eq=False)
class SourceStamp:
    """A voltage source ``e`` behind the impedance ``z`` at ``bus``."""

    comp_id: str
    bus: str
    e: complex
    z: complex
    is_slack: bool = True

    @property
    def is_ideal(self) -> bool:
        return self.z == 0


@dataclass
class ConversionStep:
    """One row of the "component values in per unit" results table."""

    comp_id: str
    name: str
    comp_type: str
    bus_ref: str = ""
    u_base_kv: float = float("nan")
    z_base_ohm: float = float("nan")
    u_nominal_kv: float | None = None
    base_factor: float = 1.0        # (U_base / U_nominal)**2, must be auditable
    z_pu: complex | None = None
    ysh_pu: complex | None = None
    y_pu: complex | None = None
    s_mva: complex | None = None
    z_ohm: complex | None = None
    note: str = ""
    seq: Seq = Seq.POS
