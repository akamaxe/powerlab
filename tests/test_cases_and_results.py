"""Study cases, the v2 project format and the numeric result payload."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from psa.core.analysis import solve_voltages
from psa.core.cases import Case, prepare_network, unique_case_id
from psa.core.examples import build_s1, s1_parameter_bindings
from psa.core.model import Seq, build_model
from psa.core.params import Parameters
from psa.core.perunit import to_per_unit
from psa.core.present import fmt_matrix_scaled, matrix_scale, matrix_table
from psa.core.results import LabeledMatrix, annotate_voltages
from psa.core.serialize import VERSION, Project, load, save
from psa.core.study import STUDY_REGISTRY, default_cases, run_case
from tests.test_s2_thevenin import build_s2

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def s1_params(a: float = 80.0) -> Parameters:
    project_vars, bindings = s1_parameter_bindings(a)
    return Parameters(project_vars=project_vars, bindings=bindings)


class Cases(unittest.TestCase):
    def test_round_trip(self):
        case = Case("half", "s1c", title="LDe at 50 %", options={"bus": "bus_e"},
                    overrides={"LDe.p_mw": "0.5*value"}, exclude=["LDb"])
        self.assertEqual(Case.from_dict(case.to_dict()), case)
        self.assertEqual(Case.from_dict({"id": "s1a"}).study, "s1a")

    def test_a_plain_case_uses_the_network_itself(self):
        net = build_s1(80.0)
        work, issues = prepare_network(net, Case("s1c", "s1c"))
        self.assertIs(work, net)
        self.assertEqual(issues, [])

    def test_overrides_and_exclusions_work_on_a_copy(self):
        net = build_s1(80.0)
        p_lde = net.get("LDe").p_mw
        case = Case("v", "s1c", overrides={"LDe.p_mw": "0.5*value",
                                           "LIbc.length_km": "(217 + A)/1000/2"},
                    exclude=["LDb"])
        work, issues = prepare_network(net, case, s1_params())
        self.assertEqual(issues, [])
        self.assertEqual(work.get("LDe").p_mw, 0.5 * p_lde)
        self.assertAlmostEqual(work.get("LIbc").length_km, 0.297 / 2)
        self.assertFalse(work.get("LDb").in_service)
        # The original is untouched.
        self.assertEqual(net.get("LDe").p_mw, p_lde)
        self.assertTrue(net.get("LDb").in_service)

    def test_stale_overrides_are_reported_not_fatal(self):
        work, issues = prepare_network(
            build_s1(80.0), Case("v", "s1c", overrides={"GONE.p_mw": "1"},
                                 exclude=["NOPE"]))
        self.assertEqual(sorted(i.code for i in issues),
                         ["stale_exclusion", "stale_override"])

    def test_run_case_matches_editing_the_network(self):
        net = build_s1(80.0)
        res = run_case(net, Case("half", "s1c", title="Half",
                                 overrides={"LDe.p_mw": "0.5*value"}))
        edited = build_s1(80.0)
        edited.get("LDe").p_mw *= 0.5
        lf = solve_voltages(edited)
        self.assertEqual(res.buses["bus_e"]["u"].value, lf.u_pu["bus_e"])
        self.assertEqual(res.case_id, "half")
        self.assertEqual(res.key, "s1c")
        self.assertTrue(res.title.startswith("Half: "))

    def test_case_options_lay_over_the_study_defaults(self):
        res = run_case(build_s1(80.0), Case("s1a", "s1a", options={"bus_j": "bus_d"}))
        self.assertIn("bus a -> bus d", res.title)

    def test_default_cases_cover_every_study(self):
        self.assertEqual([c.study for c in default_cases()], list(STUDY_REGISTRY))

    def test_unique_ids(self):
        cases = [Case("s1c", "s1c"), Case("s1c_2", "s1c")]
        self.assertEqual(unique_case_id(cases, "s1c"), "s1c_3")
        self.assertEqual(unique_case_id(cases, "s1a"), "s1a")


class ProjectFormatV2(unittest.TestCase):
    def test_version_1_files_are_migrated(self):
        proj = load(FIXTURES / "s1_a80_v1.psn")
        self.assertEqual([(c.id, c.study) for c in proj.cases],
                         [("s1a", "s1a"), ("s1b", "s1b"), ("s1c", "s1c")])
        self.assertEqual(proj.cases[0].options, {"bus_i": "bus_a", "bus_j": "bus_e"})
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "upgraded.psn"
            save(proj, out)
            data = json.loads(out.read_text(encoding="utf-8"))
            raw = out.read_bytes()
        self.assertEqual(data["version"], VERSION)
        self.assertNotIn("studies", data)
        self.assertEqual(len(data["cases"]), 3)
        self.assertNotIn(b"\r\n", raw, "project files are written with LF")

    def test_cases_round_trip(self):
        proj = Project(network=build_s1(80.0), cases=[
            Case("half", "s1c", title="Half", overrides={"LDe.p_mw": "0.5*value"},
                 exclude=["LDb"])])
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "p.psn"
            save(proj, out)
            back = load(out)
        self.assertEqual(back.cases, proj.cases)


class Annotations(unittest.TestCase):
    def test_s1c_annotations_reproduce_the_previous_overlay(self):
        """The badges the GUI drew before the refactor, from solve_voltages."""
        net = build_s1(80.0)
        res = STUDY_REGISTRY["s1c"](net)
        anns = annotate_voltages(res)
        lf = solve_voltages(net, to_per_unit(net))
        expected = []
        for bus_id in lf.u_pu:
            mag, ang = lf.u_polar(bus_id)
            expected.append((bus_id, (f"{lf.u_kv[bus_id]:.4f} kV",
                                      f"{mag:.4f} pu < {ang:.3f}\N{DEGREE SIGN}"), "bus"))
        for comp_id, flow in lf.branch.items():
            s = flow.s_from_pu * lf.s_base_mva
            expected.append((comp_id, (f"{abs(flow.i_from_pu):.4f} pu",
                                       f"{s.real:+.4f}{s.imag:+.4f}j MVA"), "branch"))
        self.assertEqual([(a.target, a.lines, a.slot) for a in anns], expected)

    def test_kv_mode_drops_the_phasor(self):
        res = STUDY_REGISTRY["s1c"](build_s1(80.0))
        bus_badges = [a for a in annotate_voltages(res, "kv") if a.slot == "bus"]
        self.assertTrue(all(len(a.lines) == 1 for a in bus_badges))

    def test_numeric_payload(self):
        net = build_s1(80.0)
        z = STUDY_REGISTRY["s1b"](net).scalars["Z"]
        self.assertEqual(z.kind, "impedance_pu")
        self.assertEqual(z.text(), "22.5232 + 11.1298i pu")
        f = STUDY_REGISTRY["s1a"](net).matrices["F"]
        self.assertEqual(f.data.shape, (2, 2))


class MatlabMatrixPrint(unittest.TestCase):
    def test_s2_admittance_matrix_prints_with_a_common_factor(self):
        Y, index = build_model(build_s2(80.0)).seq(Seq.POS).ybus("internal_node")
        factor, rows = fmt_matrix_scaled(Y)
        self.assertEqual(factor, "1.0e+02 *")
        # The first entries of the MATLAB printout (S2_output.txt, A = 80).
        first = rows[0].split()
        self.assertEqual(" ".join(first[:9]),
                         "0.4547 - 4.0155i 0.0000 + 1.0188i 0.0000 + 0.0000i")

    def test_no_factor_inside_the_fixed_point_window(self):
        self.assertEqual(matrix_scale(np.array([[1 + 2j, 0.5j]])), 0)
        self.assertEqual(fmt_matrix_scaled(np.eye(2))[0], "")

    def test_matrix_table_labels(self):
        lm = LabeledMatrix(np.array([[150 + 0j, 2j], [2j, 150 + 0j]]),
                           ["1", "2"], ["1", "2"], title="Y")
        t = matrix_table(lm)
        self.assertIn("1.0e+02 *", t.title)
        self.assertEqual(t.rows[0][0], "1")
        self.assertEqual(t.rows[0][1], "1.5000 + 0.0000i")


if __name__ == "__main__":
    unittest.main()
