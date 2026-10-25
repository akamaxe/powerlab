"""Columns of the bus impedance matrix from one LU factorisation.

``Z = Y^-1`` of the network with every source switched off: an ideal source's
bus is short-circuited to ground (its voltage cannot change), a source behind
an impedance leaves ``1/z`` to ground. Column ``k`` is the voltage change at
every bus per unit of current drawn from bus ``k``, which is all a Thevenin
equivalent or a shunt fault at ``k`` needs; ``Z_kk`` is the Thevenin impedance.

A group of buses with no admittance to ground at all (in the zero sequence,
say, the delta side of a transformer with nothing grounded behind it) has no
impedance matrix: its buses are *open*, ``Z = infinity``, and no current of
that sequence can flow into them.
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import lu_factor, lu_solve

from ..errors import SingularReductionError, TopologyError
from ..model.sequence import SequenceModel

COND_LIMIT = 1e14


class ZBus:
    """Impedance-matrix columns of one sequence network."""

    def __init__(self, sm: SequenceModel):
        Y, index = sm.ybus("grounded")
        ideal = {s.bus for s in sm.sources if s.is_ideal}
        candidates = [b for b in index if b not in ideal]
        self.open = _floating(Y, index, candidates)
        self.buses = [b for b in candidates if b not in self.open]
        self.grounded = sorted(ideal)
        keep = [index[b] for b in self.buses]
        self._pos = {b: i for i, b in enumerate(self.buses)}
        Yr = Y[np.ix_(keep, keep)]
        cond = np.linalg.cond(Yr) if keep else 1.0
        if not np.isfinite(cond) or cond > COND_LIMIT:
            raise SingularReductionError(
                "The impedance matrix does not exist: part of the network has no "
                "path to ground (no source, load or shunt).",
                buses=list(self.buses), cond=float(cond) if np.isfinite(cond) else None)
        self._lu = lu_factor(Yr) if keep else None
        self._cache: dict[str, np.ndarray] = {}

    def is_open(self, bus: str) -> bool:
        """True when ``bus`` has no path to ground in this sequence."""
        return bus in self.open

    def _check(self, bus: str) -> int:
        if bus in self.open:
            raise TopologyError(
                f"Bus '{bus}' has no path to ground in this sequence network, so "
                f"its impedance to ground is infinite.", [bus])
        if bus in self.grounded:
            raise TopologyError(
                f"Bus '{bus}' holds an ideal source, so its impedance to ground is "
                f"zero by construction. Give the source a finite impedance.", [bus])
        if bus not in self._pos:
            raise TopologyError(f"Bus '{bus}' is not part of this network.", [bus])
        return self._pos[bus]

    def column(self, bus: str) -> dict[str, complex]:
        """``{i: Z_ik}`` for every bus ``i``; grounded buses give 0."""
        if bus not in self._cache:
            k = self._check(bus)
            e = np.zeros(len(self.buses), dtype=complex)
            e[k] = 1.0
            self._cache[bus] = lu_solve(self._lu, e)
        col = self._cache[bus]
        out = {b: complex(col[i]) for i, b in enumerate(self.buses)}
        out.update({b: 0j for b in self.grounded})
        out.update({b: 0j for b in self.open})
        return out

    def entry(self, i: str, k: str) -> complex:
        return self.column(k)[i]

    def diagonal(self, k: str) -> complex:
        return self.entry(k, k)


def _floating(Y: np.ndarray, index: dict[str, int], buses: list[str]) -> set[str]:
    """Buses in groups that have no admittance to ground (rows summing to zero)."""
    if not buses:
        return set()
    sub = [index[b] for b in buses]
    Ys = Y[np.ix_(sub, sub)]
    scale = float(np.max(np.abs(Y))) or 1.0
    tol = 1e-12 * scale
    to_ground = np.abs(Ys.sum(axis=1)) + np.abs(Y[sub].sum(axis=1) - Ys.sum(axis=1))
    seen: set[int] = set()
    floating: set[str] = set()
    for start in range(len(buses)):
        if start in seen:
            continue
        group, stack = [], [start]
        seen.add(start)
        while stack:
            i = stack.pop()
            group.append(i)
            for j in np.nonzero(np.abs(Ys[i]) > tol)[0]:
                if j not in seen:
                    seen.add(int(j))
                    stack.append(int(j))
        if float(sum(to_ground[i] for i in group)) <= tol:
            floating.update(buses[i] for i in group)
    return floating
