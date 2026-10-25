"""The S2b Thevenin equivalent on a network built by hand, independent of the examples.

The grid here is a source behind ``Z_th`` in ohm (not an external grid given by
its fault level, as in ``examples/s2_grid.psn``), so this pins
:func:`psa.core.solve.thevenin_at` against two independent references through
another construction: the answer printed in the assignment for A = 0, and the
MATLAB solution for A = 80 (``Assignment2/latex/S2_output.txt``).
"""

from __future__ import annotations

import cmath
import math
import unittest

from psa.core.components import Bus, Line, Load, Shunt, Source, Transformer
from psa.core.model import ModelOptions, build_model
from psa.core.network import Network
from psa.core.solve import thevenin_at


def build_s2(a_number: float) -> Network:
    """Figure 2 of the assignment, with the industry of S1 disconnected."""
    a = float(a_number)
    net = Network(s_base_mva=10.0, f_hz=50.0,
                  base_ref_bus="b7", base_ref_u_kv=40.0)   # bus a of S1 = bus 7
    for n in range(1, 9):
        net.add(Bus(f"b{n}", name=str(n)))

    # 70 kV grid: 25 kA three-phase fault current at cos phi = 0.15 lagging.
    z_th = cmath.rect(70.0 / (math.sqrt(3.0) * 25.0), math.acos(0.15))
    net.add(Source("GRID", bus="b1", name="Grid", u_pu=1.0, z_th_ohm=z_th))
    net.add(Transformer("T1", bus_hv="b1", bus_lv="b2", name="T1",
                        sn_mva=81.5, u1n_kv=70.0, u2n_kv=40.0, x_pct=8.0))

    line = dict(r_ohm_per_km=0.8, x_ohm_per_km=0.3, b_s_per_km=3e-6)
    for i, j, length in ((2, 8, 28.5 + 0.5 * a), (2, 3, 15 + 0.4 * a),
                         (3, 4, 15 + 0.6 * a), (4, 5, 20 + 0.2 * a),
                         (5, 6, 15 + 0.5 * a), (6, 7, 17 + 0.6 * a),
                         (7, 8, 12 + 0.2 * a), (2, 7, 25 + 0.5 * a)):
        net.add(Line(f"L{i}{j}", bus_from=f"b{i}", bus_to=f"b{j}",
                     name=f"L{i}{j}", length_km=length, **line))

    for bus, p_kw, pf in ((4, 2017 + 2 * a, 0.95), (6, 2517 - 2 * a, 0.98),
                          (8, 3017 - 2 * a, 0.95)):
        net.add(Load(f"LD{bus}", bus=f"b{bus}", name=f"LD-bus{bus}",
                     p_mw=p_kw / 1000.0, cos_phi=pf, u_nominal_kv=40.0))
    for bus in (3, 5):
        net.add(Shunt(f"SH{bus}", bus=f"b{bus}", name=f"Shunt-bus{bus}",
                      q_mvar=2.0, u_nominal_kv=40.0))
    return net


class TheveninAtBus7(unittest.TestCase):
    def _check(self, a_number, u_mag, u_deg, z_th):
        model = build_model(build_s2(a_number), ModelOptions(energized_only=True))
        th = thevenin_at(model, "b7")
        mag, ang = cmath.polar(th.u_th)
        self.assertAlmostEqual(mag, u_mag, places=4)
        self.assertAlmostEqual(math.degrees(ang), u_deg, places=4)
        self.assertAlmostEqual(th.z_th.real, z_th.real, places=4)
        self.assertAlmostEqual(th.z_th.imag, z_th.imag, places=4)
        return th

    def test_published_answer_for_a_0(self):
        # "Solutions to S2 with A = 0": U_Th7 = 0.9732 < -1.4224, Z_Th7 = 0.0649 + 0.0356i
        self._check(0.0, 0.9732, -1.4224, 0.0649 + 0.0356j)

    def test_matlab_solution_for_a_80(self):
        th = self._check(80.0, 0.9348, -3.0196, 0.1645 + 0.0677j)
        i_base_ka = 10.0 / (math.sqrt(3.0) * 40.0)
        self.assertAlmostEqual(abs(th.i_sc) * i_base_ka,
                               0.7584, places=4)


if __name__ == "__main__":
    unittest.main()
