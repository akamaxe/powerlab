"""Assignment S2 end to end: the example projects, their cases, the MATLAB references.

Every S2 task is a study case in ``examples/s2_grid.psn`` (the grid alone) and
``examples/grid_industry.psn`` (the grid with the S1 industry, which S2a-S2c
take out of service). Both are checked at full precision against the MATLAB
solution for A = 80 and, by changing the project variable A, for A = 0.
"""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np

from psa.core.components import ExternalGrid
from psa.core.examples import build_s2, example_projects
from psa.core.serialize import load, project_to_dict
from psa.core.study import run_case
from tests.refdata import A_NUMBERS, ref

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
RTOL = 1e-9


def close(actual, desired, rtol=RTOL):
    np.testing.assert_allclose(actual, desired, rtol=rtol, atol=1e-12)


def project_at(name: str, a: float):
    """An example project with the variable A set to ``a`` and re-evaluated."""
    proj = load(EXAMPLES / name)
    proj.params.set_var("A", str(a))
    issues = proj.apply_parameters()
    assert not [i for i in issues if i.is_error], issues
    return proj


def run(proj, case_id: str):
    case = next(c for c in proj.cases if c.id == case_id)
    return run_case(proj.network, case, proj.params)


class ExampleFilesMatchTheirBuilders(unittest.TestCase):
    def test_files_are_up_to_date(self):
        """If this fails, run ``python tools/make_examples.py``."""
        for name, built in example_projects().items():
            with self.subTest(name=name):
                saved = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
                fresh = json.loads(json.dumps(project_to_dict(built)))
                self.assertEqual(saved, fresh)


class ExternalGridFromFaultLevel(unittest.TestCase):
    def test_25_ka_at_cos_phi_0_15(self):
        grid = build_s2(80.0).get("GRID")
        self.assertIsInstance(grid, ExternalGrid)
        z = grid.impedance_ohm(70.0)
        self.assertAlmostEqual(abs(z), 70.0 / (math.sqrt(3.0) * 25.0), places=12)
        self.assertAlmostEqual(z.real / abs(z), 0.15, places=12)
        close(z, 0.2425 + 1.5983j, rtol=1e-4)

    def test_short_circuit_power_mode_gives_the_same_impedance(self):
        grid = build_s2(80.0).get("GRID")
        z_ik = grid.impedance_ohm(70.0)
        grid.z_mode, grid.sk_mva = "sk", math.sqrt(3.0) * 70.0 * 25.0
        close(grid.impedance_ohm(70.0), z_ik, rtol=1e-12)


class S2AgainstMatlab(unittest.TestCase):
    def _each(self):
        for name in ("s2_grid.psn", "grid_industry.psn"):
            for a in A_NUMBERS:
                yield name, a, project_at(name, a)

    def test_s2a_admittance_matrix(self):
        for name, a, proj in self._each():
            with self.subTest(project=name, A=a):
                res = run(proj, "S2a")
                close(res.matrices["Y"].data, ref(a, "S2", "Y"))
                self.assertEqual(res.matrices["Y"].rows[-1], "Grid 70 kV (internal)")

    def test_s2b_thevenin_at_bus_7(self):
        for name, a, proj in self._each():
            with self.subTest(project=name, A=a):
                res = run(proj, "S2b")
                close(res.scalars["U_th"].value, ref(a, "S2", "Uth7_pu"))
                close(abs(res.scalars["U_th_kv"].value), ref(a, "S2", "Uth7_kV"))
                close(res.scalars["Z_th"].value, ref(a, "S2", "Zth7_pu"))
                for label, residual in res.tables[-1].rows:
                    self.assertLess(float(residual), 1e-9, label)

    def test_s2c_short_circuit_at_bus_7(self):
        for name, a, proj in self._each():
            with self.subTest(project=name, A=a):
                res = run(proj, "S2c")
                close(res.scalars["I_f"].value, ref(a, "S2", "Isc7_pu"))
                close(res.scalars["I_f_ka"].value, ref(a, "S2", "Isc7_kA"))
                close(res.scalars["S_f"].value, ref(a, "S2", "Ssc7_MVA"))
                # The faulted bus is at zero volts during a solid fault.
                self.assertLess(abs(res.buses["b7"]["u_fault"].value), 1e-12)

    def test_s2d_industry_voltage_with_lde_halved(self):
        for a in A_NUMBERS:
            with self.subTest(A=a):
                proj = project_at("grid_industry.psn", a)
                res = run(proj, "S2d")
                close(res.buses["bus_e"]["u"].value, ref(a, "S2", "U_LDe_pu"))
                close(res.buses["bus_e"]["u_kv"].value, ref(a, "S2", "U_LDe_kV"))

    def test_published_answer_for_a_0(self):
        res = run(project_at("s2_grid.psn", 0.0), "S2b")
        mag, ang = abs(res.scalars["U_th"].value), np.degrees(np.angle(res.scalars["U_th"].value))
        self.assertAlmostEqual(mag, 0.9732, places=4)
        self.assertAlmostEqual(ang, -1.4224, places=4)
        z = res.scalars["Z_th"].value
        self.assertAlmostEqual(z.real, 0.0649, places=4)
        self.assertAlmostEqual(z.imag, 0.0356, places=4)


class CasesOnTheCombinedNetwork(unittest.TestCase):
    def test_disconnecting_the_industry_leaves_the_grid_alone(self):
        """S2a on the combined project drops the de-energized industry buses."""
        proj = project_at("grid_industry.psn", 80.0)
        res = run(proj, "S2a")
        self.assertEqual(res.matrices["Y"].data.shape, (9, 9))

    def test_industry_connected_changes_the_thevenin(self):
        proj = project_at("grid_industry.psn", 80.0)
        case = next(c for c in proj.cases if c.id == "S2b")
        case.exclude = []
        res = run_case(proj.network, case, proj.params)
        self.assertGreater(abs(res.scalars["U_th"].value - ref(80, "S2", "Uth7_pu")), 1e-4)


if __name__ == "__main__":
    unittest.main()
