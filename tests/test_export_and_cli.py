"""Result export (text, CSV, LaTeX, JSON, MATLAB) and the headless runner."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import run_headless
from psa.core.cases import Case
from psa.core.examples import build_s2
from psa.core.export import (
    export_results,
    latex_escape,
    matlab_literal,
    result_to_dict,
    to_csv,
    to_latex,
)
from psa.core.serialize import load
from psa.core.study import run_case

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _parse_matlab(text: str) -> np.ndarray:
    body = text.split("=", 1)[-1].strip().rstrip(";").strip("[]")
    rows = [r.replace("...", "").strip() for r in body.split(";")]
    return np.array([[complex(x.replace(" ", "").replace("i", "j")) for x in r.split(",")]
                     for r in rows])


def _cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_headless.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Export(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ybus = run_case(build_s2(80.0), Case("y", "ybus"))

    def test_matlab_literal_keeps_every_digit(self):
        Y = self.ybus.matrices["Y"].data
        text = matlab_literal(Y, "Y")
        self.assertTrue(text.startswith("Y = [") and text.endswith("];"))
        np.testing.assert_array_equal(_parse_matlab(text), Y)

    def test_matlab_literal_of_a_real_matrix(self):
        text = matlab_literal(np.array([[1.0, -0.5], [0.0, 2.25]]))
        self.assertEqual(text, "[1.0, -0.5; ...\n     0, 2.25]")

    def test_json_carries_the_full_precision_numbers(self):
        d = json.loads(json.dumps(result_to_dict(self.ybus)))
        m = d["matrices"]["Y"]
        Y = np.array(m["re"]) + 1j * np.array(m["im"])
        np.testing.assert_array_equal(Y, self.ybus.matrices["Y"].data)
        self.assertEqual(m["rows"][0], "1")
        self.assertEqual(d["key"], "ybus")

    def test_matrix_tables_point_at_their_matrix(self):
        self.assertEqual(self.ybus.tables[0].source, "Y")

    def test_csv_blocks_hold_title_header_and_rows(self):
        rows = list(csv.reader(io.StringIO(to_csv([self.ybus]))))
        self.assertEqual(rows[0], [self.ybus.title])
        nodes = rows.index(["Nodes"])
        self.assertEqual(rows[nodes + 1], ["Row", "Node", "Id", "U base [kV]"])

    def test_latex_escapes_and_draws_angles(self):
        self.assertEqual(latex_escape("P_loss 5% at 0.98 < -3.2\N{DEGREE SIGN}"),
                         r"P\_loss 5\% at 0.98 $\angle$ -3.2$^\circ$")
        tex = to_latex([self.ybus])
        self.assertIn(r"\begin{tabular}", tex)
        self.assertIn(r"\toprule", tex)

    def test_export_picks_the_format_from_the_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ext in (".txt", ".csv", ".tex", ".json"):
                path = export_results([self.ybus], Path(tmp) / f"out{ext}")
                self.assertGreater(path.stat().st_size, 100)
            with self.assertRaises(ValueError):
                export_results([self.ybus], Path(tmp) / "out.xlsx")


class HeadlessRunner(unittest.TestCase):
    def test_every_case_of_every_example_runs(self):
        for path in sorted(EXAMPLES.glob("*.psn")):
            for case in load(path).cases:
                with self.subTest(project=path.name, case=case.id):
                    code, _, err = _cli(str(path), "--case", case.id, "--quiet")
                    self.assertEqual(code, 0, err)

    def test_load_flow_for_a_0_prints_the_published_loss(self):
        code, out, _ = _cli(str(EXAMPLES / "lf_220kv.psn"), "--case", "LFa", "--set", "A=0")
        self.assertEqual(code, 0)
        self.assertIn("P_loss = 26.4240 MW", out)
        self.assertIn("Variables set: A=0", out)

    def test_a_study_runs_with_its_defaults(self):
        code, out, _ = _cli(str(EXAMPLES / "s2_grid.psn"), "--study", "thevenin")
        self.assertEqual(code, 0)
        self.assertIn("Thevenin equivalent at bus", out)

    def test_export_writes_one_result_per_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "lf.json"
            code, _, _ = _cli(str(EXAMPLES / "lf_220kv.psn"), "--quiet", "--export", str(target))
            self.assertEqual(code, 0)
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual([r["case_id"] for r in data["results"]], ["LFa", "LFd", "LFb_rated"])

    def test_list_shows_cases_and_studies(self):
        code, out, _ = _cli(str(EXAMPLES / "lf_220kv.psn"), "--list")
        self.assertEqual(code, 0)
        self.assertIn("LFb_rated", out)
        self.assertIn("loadflow", out)

    def test_a_failing_case_does_not_stop_the_others(self):
        # The LF network has no external grid, so a Thevenin study cannot run.
        code, out, err = _cli(str(EXAMPLES / "lf_220kv.psn"), "--case", "LFa",
                              "--study", "thevenin")
        self.assertEqual(code, 1)
        self.assertIn("Case thevenin (thevenin) failed", err)
        self.assertIn("P_loss = 22.1658 MW", out)

    def test_bad_input_gives_exit_code_2(self):
        lf = str(EXAMPLES / "lf_220kv.psn")
        self.assertEqual(_cli(lf, "--case", "NOPE")[0], 2)
        self.assertEqual(_cli(lf, "--set", "A")[0], 2)
        self.assertEqual(_cli(lf, "--set", "A=1/0")[0], 2)
        self.assertEqual(_cli(lf, "--export", "out.xlsx")[0], 2)
        self.assertEqual(_cli(str(EXAMPLES / "missing.psn"))[0], 2)


if __name__ == "__main__":
    unittest.main()
