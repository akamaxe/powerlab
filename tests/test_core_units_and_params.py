"""Formatting, parametric expressions and the Qt-free guarantee."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from psa.core.errors import ExpressionError
from psa.core.examples import build_s1, s1_parameter_bindings
from psa.core.params import Parameters, load_script_vars, safe_eval
from psa.core.units import fmt_matrix, fmt_polar, fmt_real, fmt_rect, short_style

ROOT = Path(__file__).resolve().parent.parent


class CoreIsQtFree(unittest.TestCase):
    """The whole point of the core/gui split, and the only thing that enforces it."""

    def test_importing_core_does_not_import_qt(self):
        code = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, r"{ROOT}")
            import psa.core.study, psa.core.serialize, psa.core.analysis
            bad = [m for m in sys.modules if m.split('.')[0] in
                   ('PyQt5', 'PyQt6', 'PySide2', 'PySide6')]
            assert not bad, bad
            print("clean")
        """)
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("clean", out.stdout)


class Formatting(unittest.TestCase):
    """MATLAB format-short rendering, as the course requires."""

    def test_impedance_rectangular(self):
        self.assertEqual(fmt_rect(22.523150957 + 11.129836281j, unit="pu"),
                         "22.5232 + 11.1298i pu")

    def test_large_magnitude_goes_scientific(self):
        self.assertEqual(fmt_rect(218.9189 + 106.0273j),
                         "2.1892e+02 + 1.0603e+02i")

    def test_tiny_magnitude_goes_scientific(self):
        self.assertEqual(fmt_rect(1.28304e-5j), "0.0000e+00 + 1.2830e-05i")

    def test_small_but_in_window_stays_fixed(self):
        self.assertEqual(fmt_rect(0.00165 + 0.000825j), "0.0016 + 0.0008i")

    def test_matrix_style_follows_the_largest_element(self):
        rows = fmt_matrix([[1.00020454 + 0.00043173j, 0.00488087 + 0.27917704j],
                           [0.0037 - 0.0017535j, 1.00030325 + 0.00059241j]])
        self.assertEqual(rows[0], "1.0002 + 0.0004i  0.0049 + 0.2792i")
        self.assertEqual(rows[1], "0.0037 - 0.0018i  1.0003 + 0.0006i")

    def test_voltage_is_polar_in_degrees(self):
        self.assertEqual(fmt_polar(1.0 + 0j, "pu"), "1 pu < 0.0000 deg")

    def test_integer_group_stays_integer(self):
        style = short_style(40.0, 12.0, 6.0)
        self.assertEqual([fmt_real(v, style) for v in (40.0, 12.0, 6.0)],
                         ["40", "12", "6"])


class SafeEval(unittest.TestCase):
    def test_arithmetic_and_variables(self):
        self.assertAlmostEqual(safe_eval("(217 + A)/1000", {"A": 80}), 0.297)
        self.assertAlmostEqual(safe_eval("sqrt(3)"), 3 ** 0.5)
        self.assertAlmostEqual(safe_eval("cos(radians(60))"), 0.5, places=12)

    def test_unknown_name_is_reported(self):
        with self.assertRaises(ExpressionError) as ctx:
            safe_eval("2*B", {"A": 1})
        self.assertIn("Unknown name 'B'", str(ctx.exception))

    def test_attribute_access_is_blocked(self):
        for expr in ("().__class__", "A.real", "__import__('os')",
                     "[1,2][0]", "(lambda: 1)()"):
            with self.subTest(expr=expr), self.assertRaises(ExpressionError):
                safe_eval(expr, {"A": 1})

    def test_division_by_zero_is_readable(self):
        with self.assertRaises(ExpressionError) as ctx:
            safe_eval("1/0")
        self.assertIn("Division by zero", str(ctx.exception))


class ParametricBindings(unittest.TestCase):
    def test_bindings_reproduce_the_reference_network(self):
        net = build_s1(80.0)
        # Wipe the values, then rebuild them from the assignment's own algebra.
        net.get("LIbc").length_km = 0.0
        net.get("LDb").p_mw = 0.0
        net.get("LDe").p_mw = 0.0

        project_vars, bindings = s1_parameter_bindings(80.0)
        params = Parameters(project_vars=project_vars, bindings=bindings)
        issues = params.apply(net)
        self.assertEqual([str(i) for i in issues if i.is_error], [])

        self.assertAlmostEqual(net.get("LIbc").length_km, 0.297, places=12)
        self.assertAlmostEqual(net.get("LDb").p_mw, 0.037, places=12)
        self.assertAlmostEqual(net.get("LDe").p_mw, 0.323, places=12)

    def test_changing_a_reevaluates_everything(self):
        net = build_s1(80.0)
        project_vars, bindings = s1_parameter_bindings(80.0)
        params = Parameters(project_vars=project_vars, bindings=bindings)
        params.set_var("A", "0")
        params.apply(net)
        self.assertAlmostEqual(net.get("LIbc").length_km, 0.217, places=12)
        self.assertAlmostEqual(net.get("LDb").p_mw, 0.117, places=12)

        from psa.core.analysis import equivalent_impedance
        z = equivalent_impedance(net, "bus_a").z_pu
        self.assertAlmostEqual(z.real, 22.5156, places=4)
        self.assertAlmostEqual(z.imag, 11.0767, places=4)

    def test_integer_field_stays_integer(self):
        net = build_s1(80.0)
        params = Parameters(bindings={"LIbc.n_parallel": "1 + 1"})
        params.apply(net)
        self.assertIsInstance(net.get("LIbc").n_parallel, int)
        self.assertEqual(net.get("LIbc").n_parallel, 2)

    def test_bad_binding_leaves_the_value_untouched(self):
        net = build_s1(80.0)
        before = net.get("LDb").p_mw
        params = Parameters(bindings={"LDb.p_mw": "(117 - NOPE)/1000"})
        issues = params.apply(net)
        self.assertTrue(any(i.is_error for i in issues))
        self.assertEqual(net.get("LDb").p_mw, before)

    def test_stale_binding_is_a_warning_not_a_crash(self):
        net = build_s1(80.0)
        params = Parameters(bindings={"GONE.p_mw": "1"})
        issues = params.apply(net)
        self.assertTrue(any(i.code == "stale_binding" for i in issues))


class ScriptVariables(unittest.TestCase):
    """Variables defined in the user's own Python file."""

    def test_numeric_globals_are_picked_up(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "vars.py"
            p.write_text(textwrap.dedent("""
                import math
                A = 80
                cos_phi = 0.9
                _private = 5
                label = "not numeric"
                def helper():
                    return 1
                derived = 217 + A
            """), encoding="utf-8")
            got = load_script_vars(p)

        self.assertEqual(got["A"], 80)
        self.assertEqual(got["cos_phi"], 0.9)
        self.assertEqual(got["derived"], 297)
        self.assertNotIn("_private", got)
        self.assertNotIn("label", got)
        self.assertNotIn("helper", got)
        self.assertNotIn("math", got)

    def test_script_variables_feed_expressions(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "vars.py"
            p.write_text("A = 80\n", encoding="utf-8")
            params = Parameters(script_path=str(p),
                                project_vars=[("len_m", "217 + A")],
                                bindings={"LIbc.length_km": "len_m/1000"})
            params.reload_script()
            net = build_s1(0.0)
            params.apply(net)
            self.assertAlmostEqual(net.get("LIbc").length_km, 0.297, places=12)

    def test_missing_script_is_an_issue_not_a_crash(self):
        params = Parameters(script_path="does_not_exist.py")
        issues = params.reload_script()
        self.assertTrue(any(i.is_error for i in issues))


if __name__ == "__main__":
    unittest.main()
