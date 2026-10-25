"""Bus admittance matrix assembly and Kron reduction.

This is the general foundation the rest of the analysis rests on: the two-port
extraction, the equivalent impedance, the nodal solve and (later) S2/S3/LF all
go through :func:`build_ybus`.
"""

from __future__ import annotations

import numpy as np

from .errors import SingularReductionError
from .perunit import PuModel

#: Above this condition number the eliminated block is treated as singular.
COND_LIMIT = 1e12


def build_ybus(pu: PuModel,
               bus_order: list[str] | None = None,
               exclude: set[str] | None = None,
               include_sources: bool = False,
               ) -> tuple[np.ndarray, dict[str, int]]:
    """Assemble the bus admittance matrix.

    Parameters
    ----------
    pu:
        The per-unit model.
    bus_order:
        Explicit bus ordering. Defaults to every bus that has a base, in the
        network's insertion order, which keeps matrix indices deterministic.
    exclude:
        Component ids to leave out entirely (used by the two-port termination
        rule).
    include_sources:
        When true, sources with a finite Thevenin impedance contribute a shunt
        ``1/z_th``. Ideal sources never contribute a shunt: as an admittance
        they are an infinite short to ground and must be handled by the caller
        as a boundary condition instead.

    Returns
    -------
    ``(Y, index)`` where ``index`` maps a bus id onto its row/column.
    """
    skip = exclude or set()
    if bus_order is None:
        bus_order = list(pu.buses) or list(pu.bases.u_base_kv.keys())
    index = {b: i for i, b in enumerate(bus_order)}
    n = len(bus_order)
    Y = np.zeros((n, n), dtype=complex)

    for br in pu.series:
        if br.comp_id in skip:
            continue
        if br.bus_i not in index or br.bus_j not in index:
            continue
        i, j = index[br.bus_i], index[br.bus_j]
        y = 1.0 / br.z_pu
        Y[i, i] += y + br.ysh_pu
        Y[j, j] += y + br.ysh_pu
        Y[i, j] -= y
        Y[j, i] -= y

    for sh in pu.shunts:
        if sh.comp_id in skip or sh.bus not in index:
            continue
        Y[index[sh.bus], index[sh.bus]] += sh.y_pu

    if include_sources:
        for src in pu.sources:
            if src.comp_id in skip or src.bus not in index or src.is_ideal:
                continue
            Y[index[src.bus], index[src.bus]] += 1.0 / src.z_th_pu

    return Y, index


def kron_reduce(Y: np.ndarray, keep: list[int],
                labels: list[str] | None = None,
                cond_limit: float = COND_LIMIT) -> np.ndarray:
    """Eliminate every bus not in ``keep``.

    ``Y_red = Y_kk - Y_km @ solve(Y_mm, Y_mk)``, using a solve rather than an
    explicit inverse: better conditioned, and it lets us detect a singular
    block before it turns into silent ``inf``/``nan``.
    """
    n = Y.shape[0]
    keep = list(keep)
    elim = [i for i in range(n) if i not in set(keep)]
    if not elim:
        return Y[np.ix_(keep, keep)].copy()

    Ykk = Y[np.ix_(keep, keep)]
    Ykm = Y[np.ix_(keep, elim)]
    Ymk = Y[np.ix_(elim, keep)]
    Ymm = Y[np.ix_(elim, elim)]

    cond = np.linalg.cond(Ymm)
    if not np.isfinite(cond) or cond > cond_limit:
        names = ([labels[i] for i in elim] if labels else
                 [str(i) for i in elim])
        raise SingularReductionError(
            "Cannot eliminate these buses: the reduced block is singular. "
            "This normally means a bus is floating (no shunt to ground and "
            "fewer than two branches). Buses involved: " + ", ".join(names),
            buses=names, cond=float(cond) if np.isfinite(cond) else None)

    return Ykk - Ykm @ np.linalg.solve(Ymm, Ymk)
