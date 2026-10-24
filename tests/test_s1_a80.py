"""Acceptance test: the whole pipeline against Assignment S1 with A = 80.

Reference values come from the working MATLAB solution
(``Assignment1/latex/S1_output.txt``). The A = 0 case is checked against the
answer printed in the assignment itself, which makes it an independent
validation rather than a comparison against our own earlier output.
"""

from __future__ import annotations

import cmath
import math
import unittest

import numpy as np

from psa.core.analysis import equivalent_impedance, solve_voltages
from psa.core.bases import propagate_bases
from psa.core.examples import build_s1
from psa.core.perunit import to_per_unit
from psa.core.twoport import resolve_port_exclusions, two_port


def polar(z: complex) -> tuple[float, float]:
    m, a = cmath.polar(z)
    return m, math.degrees(a)


class S1Reference(unittest.TestCase):
    """Every number the report prints, to the precision it prints it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.net = build_s1(80.0)
        cls.bases = propagate_bases(cls.net)
        cls.pu = to_per_unit(cls.net, cls.bases)
        cls.tp = two_port(cls.net, "bus_a", "bus_e", pu=cls.pu)
        cls.eq = equivalent_impedance(cls.net, "bus_a", pu=cls.pu, far_bus="bus_e")
        cls.lf = solve_voltages(cls.net, cls.pu)

    # -- structure ----------------------------------------------------- #

    def test_network_validates_clean(self):
        errors = [i for i in self.net.validate() if i.is_error]
        self.assertEqual(errors, [], f"unexpected validation errors: {errors}")

    # -- bases --------------------------------------------------------- #

    def test_voltage_bases(self):
        expected = {"bus_a": 40.0, "bus_b": 12.0, "bus_c": 12.0,
                    "bus_d": 12.0, "bus_e": 6.0}
        for bus, u in expected.items():
            self.assertAlmostEqual(self.bases.u(bus), u, places=9, msg=bus)

    def test_impedance_bases(self):
        for bus, z in {"bus_a": 160.0, "bus_b": 14.4, "bus_e": 3.6}.items():
            self.assertAlmostEqual(self.bases.z(bus), z, places=9, msg=bus)

    def test_current_bases(self):
        for bus, i in {"bus_a": 0.1443, "bus_b": 0.4811, "bus_e": 0.9623}.items():
            self.assertAlmostEqual(self.bases.i(bus), i, places=4, msg=bus)

    def test_voltage_zones(self):
        self.assertEqual([u for u, _ in self.bases.zones], [40.0, 12.0, 6.0])

    # -- per unit ------------------------------------------------------ #

    def assertComplexAlmostEqual(self, got, want, places=4, msg=""):
        self.assertAlmostEqual(got.real, want.real, places=places,
                               msg=f"{msg} (real)")
        self.assertAlmostEqual(got.imag, want.imag, places=places,
                               msg=f"{msg} (imag)")

    def test_transformer_impedances(self):
        self.assertComplexAlmostEqual(
            self.pu.series_of("TI1").z_pu, 0.1166667j, 7, "Zt_TI1")
        self.assertComplexAlmostEqual(
            self.pu.series_of("TI2").z_pu, 0.16j, 9, "Zt_TI2")

    def test_line_impedances_and_charging(self):
        bc = self.pu.series_of("LIbc")
        self.assertComplexAlmostEqual(bc.z_pu, 0.00165 + 0.000825j, 9, "Z_bc")
        self.assertComplexAlmostEqual(bc.ysh_pu, 1.28304e-05j, 12, "ysh_bc")
        cd = self.pu.series_of("LIcd")
        self.assertComplexAlmostEqual(cd.z_pu, 0.0033 + 0.00165j, 9, "Z_cd")
        self.assertComplexAlmostEqual(cd.ysh_pu, 6.4152e-06j, 12, "ysh_cd")

    def test_load_impedances(self):
        self.assertComplexAlmostEqual(
            1.0 / self.pu.shunt_of("LDb").y_pu, 218.9189 + 106.0273j, 4, "Z_LDb")
        self.assertComplexAlmostEqual(
            1.0 / self.pu.shunt_of("LDe").y_pu, 25.0774 + 12.1455j, 4, "Z_LDe")

    def test_load_powers(self):
        self.assertComplexAlmostEqual(
            self.pu.step("LDb").s_mva, 0.0370 + 0.0179j, 4, "S_LDb")
        self.assertComplexAlmostEqual(
            self.pu.step("LDe").s_mva, 0.3230 + 0.1564j, 4, "S_LDe")

    def test_base_factor_is_reported(self):
        # (U_base/U_n)**2 is 1 here, so a bug in it would be invisible in the
        # numbers; assert it is at least computed and exposed.
        for comp_id in ("LDb", "LDe"):
            self.assertAlmostEqual(self.pu.step(comp_id).base_factor, 1.0, places=12)

    # -- S1a ----------------------------------------------------------- #

    def test_f_matrix(self):
        want = np.array([[1.0002 + 0.0004j, 0.0049 + 0.2792j],
                         [0.0037 - 0.0018j, 1.0003 + 0.0006j]])
        got = self.tp.f
        for i in range(2):
            for j in range(2):
                self.assertComplexAlmostEqual(
                    complex(got[i, j]), complex(want[i, j]), 4, f"F[{i},{j}]")

    def test_f_matrix_full_precision(self):
        want = np.array([[1.00020454 + 0.00043173j, 0.00488087 + 0.27917704j],
                         [0.00370000 - 0.00175350j, 1.00030325 + 0.00059241j]])
        self.assertLess(float(np.max(np.abs(self.tp.f - want))), 1e-8)

    def test_determinant_is_one(self):
        self.assertTrue(self.tp.reciprocity_applies)
        self.assertAlmostEqual(abs(self.tp.det - 1.0), 0.0, places=6)

    def test_cascade_and_ybus_methods_agree(self):
        self.assertIsNotNone(self.tp.f_cascade)
        self.assertEqual(self.tp.cascade_status, "ok")
        self.assertLess(self.tp.residual_rel, 1e-9)

    def test_cascade_path_and_elements(self):
        self.assertEqual(self.tp.path,
                         ["bus_a", "bus_b", "bus_c", "bus_d", "bus_e"])
        self.assertEqual([n for n, _ in self.tp.per_element],
                         ["TI1", "LDb", "LIbc", "LIcd", "TI2"])

    def test_termination_rule(self):
        excluded = resolve_port_exclusions(self.net, "bus_a", "bus_e")
        self.assertEqual(excluded, {"SRC", "LDe"})

    def test_ideal_source_cannot_be_reincluded(self):
        excluded = resolve_port_exclusions(
            self.net, "bus_a", "bus_e", overrides={"SRC", "LDe"})
        self.assertIn("SRC", excluded, "an ideal source at a port must stay excluded")
        self.assertNotIn("LDe", excluded, "a load override must be honoured")

    # -- S1b ----------------------------------------------------------- #

    def test_equivalent_impedance(self):
        self.assertComplexAlmostEqual(
            self.eq.z_pu, 22.5232 + 11.1298j, 4, "Z_I")

    def test_equivalent_impedance_methods_agree(self):
        self.assertIsNotNone(self.eq.z_pu_twoport)
        self.assertTrue(np.isclose(self.eq.z_pu, self.eq.z_pu_twoport,
                                   rtol=1e-9, atol=1e-12),
                        f"Kron {self.eq.z_pu} vs two-port {self.eq.z_pu_twoport}")

    def test_equivalent_impedance_in_ohm(self):
        self.assertAlmostEqual(self.eq.z_ohm.real, 3603.70, places=1)
        self.assertAlmostEqual(self.eq.z_ohm.imag, 1780.77, places=1)

    # -- S1c ----------------------------------------------------------- #

    def test_phasor_chain(self):
        u_a = 1.0 + 0j
        i_a = u_a / self.eq.z_pu
        u_e, i_e = self.tp.solve_downstream(u_a, i_a)

        m, a = polar(i_a)
        self.assertAlmostEqual(m, 0.0398, places=4)
        self.assertAlmostEqual(a, -26.2963, places=4)

        m, a = polar(i_e)
        self.assertAlmostEqual(m, 0.0357, places=4)
        self.assertAlmostEqual(a, -26.3764, places=4)

        m, a = polar(u_e)
        self.assertAlmostEqual(m, 0.9952, places=4)
        self.assertAlmostEqual(a, -0.5345, places=4)
        self.assertAlmostEqual(m * self.bases.u("bus_e"), 5.9715, places=4)

    def test_nodal_solve_matches_two_port(self):
        u_a = self.lf.u_pu["bus_a"]
        i_a = u_a / self.eq.z_pu
        u_e, _ = self.tp.solve_downstream(u_a, i_a)
        self.assertLess(abs(self.lf.u_pu["bus_e"] - u_e), 1e-12)
        self.assertLess(abs(self.lf.i_source_pu["SRC"] - i_a), 1e-12)

    def test_all_bus_voltages(self):
        expected_kv = {"bus_a": 40.0000, "bus_b": 11.9754, "bus_c": 11.9746,
                       "bus_d": 11.9730, "bus_e": 5.9715}
        for bus, kv in expected_kv.items():
            self.assertAlmostEqual(self.lf.u_kv[bus], kv, places=4, msg=bus)

    def test_source_power(self):
        s = self.lf.s_source_mva["SRC"]
        self.assertComplexAlmostEqual(s, 0.35685 + 0.17634j, 4, "S_slack")


class ParameterSweep(unittest.TestCase):
    """The invariants must hold for any A, and A = 0 has a published answer."""

    def test_published_answer_for_a_zero(self):
        net = build_s1(0.0)
        eq = equivalent_impedance(net, "bus_a")
        # Printed in the assignment: Z_I = 22.5156 + 11.0767i pu
        self.assertAlmostEqual(eq.z_pu.real, 22.5156, places=4)
        self.assertAlmostEqual(eq.z_pu.imag, 11.0767, places=4)

    def test_invariants_across_a(self):
        for a in (0.0, 80.0, 150.0):
            with self.subTest(A=a):
                net = build_s1(a)
                tp = two_port(net, "bus_a", "bus_e")
                self.assertAlmostEqual(abs(tp.det - 1.0), 0.0, places=9)
                self.assertLess(tp.residual_rel, 1e-9)
                eq = equivalent_impedance(net, "bus_a", far_bus="bus_e")
                self.assertTrue(np.isclose(eq.z_pu, eq.z_pu_twoport,
                                           rtol=1e-9, atol=1e-12))


class TwoPortDirection(unittest.TestCase):
    """Reversing the ports gives a different matrix, not the same one."""

    def test_reversing_the_ports_swaps_a_and_d(self):
        """For a reciprocal two-port, looking the other way swaps A and D."""
        net = build_s1(80.0)
        fwd = two_port(net, "bus_a", "bus_e")
        rev = two_port(net, "bus_e", "bus_a")

        # They really are different matrices: getting the direction wrong is
        # the easiest way to obtain a plausible but incorrect answer.
        self.assertGreater(float(np.max(np.abs(fwd.f - rev.f))), 1e-5)

        a, b, c, d = fwd.abcd
        ra, rb, rc, rd = rev.abcd
        for got, want, name in ((ra, d, "A"), (rd, a, "D"),
                                (rb, b, "B"), (rc, c, "C")):
            self.assertAlmostEqual(got.real, want.real, places=9, msg=name)
            self.assertAlmostEqual(got.imag, want.imag, places=9, msg=name)

        self.assertAlmostEqual(abs(rev.det - 1.0), 0.0, places=9)
        self.assertEqual(
            resolve_port_exclusions(net, "bus_e", "bus_a"), {"SRC", "LDe"})


if __name__ == "__main__":
    unittest.main()
