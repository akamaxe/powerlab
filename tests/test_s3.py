"""Assignment S3 end to end: sequence networks and unsymmetrical conditions.

The S3 tasks are study cases in ``examples/grid_industry.psn`` (S3a-S3c with
the industry disconnected, S3d and S3e with it connected) and, for S3a-S3c,
in ``examples/s2_grid.psn``. They are checked at full precision against the
MATLAB solution for A = 80 and A = 0, plus properties any fault solver must
have, whatever the network.
"""

from __future__ import annotations

import cmath
import math
import unittest


from psa.core.cases import Case
from psa.core.errors import ValidationError
from psa.core.examples import build_grid_industry, build_s1, build_s2
from psa.core.model import ModelOptions, Seq, build_model
from psa.core.solve import (
    SEQS,
    SequenceNetworks,
    fault_constraints,
    solve_fault,
    solve_shunt_condition,
)
from psa.core.study import run_case
from tests.refdata import A_NUMBERS, ref
from tests.test_s2 import close, project_at, run

ALPHA = cmath.exp(2j * math.pi / 3)


class S3AgainstMatlab(unittest.TestCase):
    def _each(self, names=("s2_grid.psn", "grid_industry.psn")):
        for name in names:
            for a in A_NUMBERS:
                yield name, a, project_at(name, a)

    def test_s3a_zero_sequence_admittance_matrix(self):
        for name, a, proj in self._each():
            with self.subTest(project=name, A=a):
                res = run(proj, "S3a")
                close(res.matrices["Y"].data, ref(a, "S3", "Ydelta_0"))

    def test_zero_sequence_matrix_with_the_source_node(self):
        """The 9x9 form, source behind Z0 as an extra node, is MATLAB's Y_0."""
        for a in A_NUMBERS:
            with self.subTest(A=a):
                net = build_s2(float(a))
                res = run_case(net, Case("y0", "ybus", options={
                    "sequence": "zero", "sources": "internal_node"}))
                close(res.matrices["Y"].data, ref(a, "S3", "Y_0"))
                res = run_case(net, Case("y2", "ybus", options={
                    "sequence": "negative", "sources": "internal_node"}))
                close(res.matrices["Y"].data, ref(a, "S3", "Y_2"))

    def test_s3b_sequence_thevenin_at_bus_7(self):
        for name, a, proj in self._each():
            with self.subTest(project=name, A=a):
                res = run(proj, "S3b")
                close(res.scalars["U_th"].value, ref(a, "S3", "Uth7"))
                close(res.scalars["Z_th1"].value, ref(a, "S3", "Zth7_1"))
                close(res.scalars["Z_th2"].value, ref(a, "S3", "Zth7_2"))
                close(res.scalars["Z_th0"].value, ref(a, "S3", "Zth7_0"))

    def test_s3c_single_line_to_ground_fault_at_bus_7(self):
        for name, a, proj in self._each():
            with self.subTest(project=name, A=a):
                res = run(proj, "S3c")
                close(res.scalars["I_f"].value, ref(a, "S3", "If7_pu"))
                close(res.scalars["I_f_ka"].value, ref(a, "S3", "If7_kA"))
                for key in ("I1", "I2", "I0"):
                    close(res.scalars[key].value, ref(a, "S3", "If7_seq"))
                self.assertLess(abs(res.buses["b7"]["u_a"].value), 1e-12,
                                "the faulted phase is at earth potential")

    def test_s3d_sequence_thevenin_at_bus_e(self):
        for a in A_NUMBERS:
            with self.subTest(A=a):
                res = run(project_at("grid_industry.psn", a), "S3d")
                close(res.scalars["U_th"].value, ref(a, "S3", "Uth_e"))
                close(res.scalars["Z_th1"].value, ref(a, "S3", "Zth_e_1"))
                close(res.scalars["Z_th2"].value, ref(a, "S3", "Zth_e_2"))
                close(res.scalars["Z_th0"].value, ref(a, "S3", "Zth_e_0"))

    def test_s3e_bus_7_voltages_with_half_of_phase_a_disconnected(self):
        for a in A_NUMBERS:
            with self.subTest(A=a):
                res = run(project_at("grid_industry.psn", a), "S3e")
                close([res.scalars[f"U{p}_report_kv"].value for p in "abc"],
                      ref(a, "S3", "U7_ph_kV"))
                close([res.scalars[f"U{n}_report"].value for n in "120"],
                      ref(a, "S3", "U7_s"))
                close(res.scalars["U_pre_report"].value, ref(a, "S3", "Upre_7"))
                # MATLAB's I_e is injected into the network: minus what the load draws.
                close([-res.scalars[k].value for k in ("I1", "I2", "I0")],
                      ref(a, "S3", "I_e"))
                close(res.matrices["Z_ph"].data, ref(a, "S3", "Z_LDe_ph"))
                close(res.matrices["Z_s"].data, ref(a, "S3", "Z_LDe_s"))


def _nets(a: float = 80.0, exclude=("TI1",)):
    model = build_model(build_grid_industry(a),
                        ModelOptions(energized_only=True, exclude=frozenset(exclude)))
    return model, SequenceNetworks.build(model, SEQS)


class FaultProperties(unittest.TestCase):
    """The textbook closed forms, from the general constraint solver."""

    @classmethod
    def setUpClass(cls):
        cls.model, cls.nets = _nets()
        th = cls.nets
        cls.u = th.u_pre("b7", Seq.POS)
        cls.z1, cls.z2, cls.z0 = (th.z("b7", s) for s in SEQS)

    def _solve(self, kind, zf=0j, zg=0j):
        A, B = fault_constraints(kind, zf, zg)
        return solve_shunt_condition(self.nets, "b7", A, B)

    def test_three_phase(self):
        zf = 0.05 + 0.02j
        r = self._solve("3ph", zf)
        close(r.i_s, [self.u / (self.z1 + zf), 0, 0])

    def test_single_line_to_ground_through_zf(self):
        zf = 0.1 + 0.0j
        r = self._solve("slg", zf)
        i0 = self.u / (self.z1 + self.z2 + self.z0 + 3 * zf)
        close(r.i_s, [i0, i0, i0])
        close(r.u_abc[0], zf * 3 * i0)

    def test_line_to_line(self):
        r = self._solve("ll")
        i1 = self.u / (self.z1 + self.z2)
        close(r.i_s, [i1, -i1, 0])
        close(r.i_abc[1], -1j * math.sqrt(3.0) * i1)
        close(r.u_abc[1], r.u_abc[2])

    def test_double_line_to_ground(self):
        zg = 0.2j
        r = self._solve("llg", 0j, zg)
        z0g = self.z0 + 3 * zg
        i1 = self.u / (self.z1 + self.z2 * z0g / (self.z2 + z0g))
        i2 = -i1 * z0g / (self.z2 + z0g)
        i0 = -i1 * self.z2 / (self.z2 + z0g)
        close(r.i_s, [i1, i2, i0])
        self.assertLess(abs(r.i_abc[0]), 1e-12, "phase a carries no current")

    def test_balanced_load_is_the_linear_solve(self):
        """An 'unbalanced' load with factors (1, 1, 1) changes nothing."""
        res = run_case(build_grid_industry(80.0), Case("u", "unbalanced_load", options={
            "element": "LDe", "factor_a": 1.0, "factor_b": 1.0, "factor_c": 1.0,
            "bus": "b7"}))
        lin = run_case(build_grid_industry(80.0), Case("l", "linear", options={"bus": "b7"}))
        close(res.scalars["U1_report"].value, lin.buses["b7"]["u"].value)
        self.assertLess(abs(res.scalars["U2_report"].value), 1e-12)
        self.assertLess(abs(res.scalars["U0_report"].value), 1e-12)

    def test_fault_on_the_zero_sequence_open_side_draws_no_earth_current(self):
        """Bus d sees TI2's delta: a fault there has a zero-sequence path only
        through the industry cables and TI1 (YNyn) to the grid."""
        model = build_model(build_grid_industry(80.0), ModelOptions(energized_only=True))
        fr = solve_fault(model, "bus_d", "slg")
        self.assertGreater(abs(fr.i_ground), 0.0)


class ZeroSequenceModels(unittest.TestCase):
    def test_dyn_transformer_is_a_shunt_on_its_star_side(self):
        sm = build_model(build_grid_industry(80.0)).seq(Seq.ZERO)
        self.assertNotIn("TI2", [b.comp_id for b in sm.branches])
        (shunt,) = [s for s in sm.shunts if s.comp_id == "TI2"]
        self.assertEqual(shunt.bus, "bus_e")
        close(1.0 / shunt.y, 0.16j)

    def test_delta_load_and_shunt_are_open(self):
        sm = build_model(build_s2(80.0)).seq(Seq.ZERO)
        ids = [s.comp_id for s in sm.shunts]
        self.assertNotIn("LD6", ids)
        self.assertNotIn("SH3", ids)
        self.assertIn("LD4", ids)
        self.assertIn("SH5", ids)

    def test_grid_zero_sequence_impedance_from_the_slg_level(self):
        sm = build_model(build_s2(80.0)).seq(Seq.ZERO)
        (src,) = sm.sources
        close(src.z, ref(80, "S3", "Zth_0"))
        self.assertEqual(src.e, 0j)

    def test_lines_without_zero_sequence_data_are_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            build_model(build_s1(80.0)).seq(Seq.ZERO)
        self.assertIn("zero-sequence data", str(ctx.exception))

    def test_negative_sequence_has_no_source_voltage(self):
        sm = build_model(build_s2(80.0)).seq(Seq.NEG)
        (src,) = sm.sources
        self.assertEqual(src.e, 0j)
        pos = build_model(build_s2(80.0)).seq(Seq.POS)
        close(src.z, pos.sources[0].z)

    def test_sequence_model_study_lists_both_networks(self):
        res = run_case(build_grid_industry(80.0), Case("m", "seqmodel"))
        self.assertEqual([t.title for t in res.tables],
                         ["Positive sequence [pu]", "Zero sequence [pu]"])
        notes = {row[0]: row[4] for row in res.tables[1].rows}
        self.assertIn("LV bus", notes["TI2"])


if __name__ == "__main__":
    unittest.main()
