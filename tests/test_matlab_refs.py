"""The engine against the MATLAB reference solutions at full precision.

The other assignment tests compare against the four-decimal values printed in
the reports. These compare against every digit MATLAB computed (see
``tests/refdata.py``), for A = 80 and A = 0, so a refactor that shifts a result
in the eighth digit is caught.
"""

from __future__ import annotations

import unittest

import numpy as np

from psa.core.analysis import equivalent_impedance, solve_voltages
from psa.core.examples import build_s1
from psa.core.model import ModelOptions, build_model
from psa.core.perunit import to_per_unit
from psa.core.solve import thevenin_at
from psa.core.twoport import two_port
from tests.refdata import A_NUMBERS, ref, refs
from tests.test_s2_thevenin import build_s2

RTOL = 1e-9


def assert_close(actual, desired, rtol=RTOL):
    np.testing.assert_allclose(actual, desired, rtol=rtol, atol=1e-12)


class Fixtures(unittest.TestCase):
    def test_every_fixture_has_all_sections(self):
        for a in A_NUMBERS:
            with self.subTest(A=a):
                data = refs(a)
                self.assertEqual(data["meta"]["A"], a)
                for section in ("S1", "S2", "S3", "LF", "LF_q_rated"):
                    self.assertEqual(ref(a, section, "A"), a, section)


class S1AgainstMatlab(unittest.TestCase):
    def test_two_port_impedance_and_voltage(self):
        for a in A_NUMBERS:
            with self.subTest(A=a):
                net = build_s1(float(a))
                pu = to_per_unit(net)
                tp = two_port(net, "bus_a", "bus_e", pu=pu)
                assert_close(tp.f, ref(a, "S1", "F_ae"))
                eq = equivalent_impedance(net, "bus_a", pu=pu)
                assert_close(eq.z_pu, ref(a, "S1", "ZI_pu"))
                lf = solve_voltages(net, pu)
                assert_close(lf.u_pu["bus_e"], ref(a, "S1", "U_e_pu"))


class S2bAgainstMatlab(unittest.TestCase):
    def test_thevenin_at_bus_7(self):
        for a in A_NUMBERS:
            with self.subTest(A=a):
                model = build_model(build_s2(float(a)), ModelOptions(energized_only=True))
                th = thevenin_at(model, "b7")
                assert_close(th.u_th, ref(a, "S2", "Uth7_pu"))
                assert_close(th.z_th, ref(a, "S2", "Zth7_pu"))
                assert_close(th.i_sc, ref(a, "S2", "Isc7_pu"))


if __name__ == "__main__":
    unittest.main()
