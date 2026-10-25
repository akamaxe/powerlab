"""Assignment LF end to end: the Newton-Raphson load flow.

The LF tasks are study cases in ``examples/lf_220kv.psn``: LFa-LFc in one run,
LFd (load at bus 4 up 10 %) and the reading of the bus-5 shunt as a
susceptance rated at 220 kV. They are checked against the MATLAB solution for
A = 80 and A = 0 at ``rtol = 1e-8``, against pandapower as an independent
solver, and the Jacobian against finite differences.
"""

from __future__ import annotations

import math
import os
import unittest

import numpy as np

from psa.core.cases import Case
from psa.core.components import Generator
from psa.core.errors import TopologyError, ValidationError
from psa.core.examples import LF_LINES, build_lf
from psa.core.model import ModelOptions, build_model
from psa.core.solve.loadflow import bus_powers, jacobian, solve_load_flow
from psa.core.study import run_case
from tests.refdata import A_NUMBERS, ref
from tests.test_s2 import project_at, run

RTOL = 1e-8
BUS_TYPES = ["Slack", "PQ", "PU", "PQ", "PQ", "PQ", "PU", "PQ"]
BUSES = [f"b{k}" for k in range(1, 9)]


def close(actual, desired, rtol=RTOL):
    np.testing.assert_allclose(actual, desired, rtol=rtol, atol=1e-10)


def voltages(res) -> np.ndarray:
    return np.array([res.buses[b]["u"].value for b in BUSES])


class LoadFlowAgainstMatlab(unittest.TestCase):
    def _each(self):
        for a in A_NUMBERS:
            yield a, project_at("lf_220kv.psn", a)

    def test_lfa_bus_types_and_voltages(self):
        for a, proj in self._each():
            with self.subTest(A=a):
                res = run(proj, "LFa")
                self.assertEqual([res.info[f"type:{b}"] for b in BUSES], BUS_TYPES)
                close(voltages(res), ref(a, "LF", "U"))
                close([res.buses[b]["u_kv"].value for b in BUSES], ref(a, "LF", "U_kV"))
                close([res.buses[b]["theta_deg"].value for b in BUSES],
                      ref(a, "LF", "theta_deg"))
                close(res.matrices["Y"].data, ref(a, "LF", "Y"))

    def test_lfb_slack_generation_and_pu_bus_reactive_power(self):
        for a, proj in self._each():
            with self.subTest(A=a):
                res = run(proj, "LFa")
                close(res.elements["G1"]["p_mw"].value, ref(a, "LF", "P_G1_MW"))
                close(res.elements["G1"]["q_mvar"].value, ref(a, "LF", "Q_G1_MVAr"))
                close(res.elements["SVC3"]["q_mvar"].value, ref(a, "LF", "Q_sh3_MVAr"))
                close(res.elements["G7"]["q_mvar"].value, ref(a, "LF", "Q_G7_MVAr"))
                close(res.elements["G7"]["p_mw"].value, 208.5)
                close(res.elements["SVC3"]["p_mw"].value, 0.0)

    def test_lfc_losses_and_balance(self):
        for a, proj in self._each():
            with self.subTest(A=a):
                res = run(proj, "LFa")
                close(res.scalars["P_loss_MW"].value, ref(a, "LF", "P_loss_MW"))
                self.assertLess(abs(res.scalars["balance_MW"].value), 1e-6)
                self.assertEqual(res.info["converged"], "yes")

    def test_published_loss_for_a_0(self):
        res = run(project_at("lf_220kv.psn", 0), "LFa")
        self.assertEqual(f"{res.scalars['P_loss_MW'].value:.4f}", "26.4240")

    def test_lfd_load_at_bus_4_up_ten_percent(self):
        for a, proj in self._each():
            with self.subTest(A=a):
                res = run(proj, "LFd")
                close(res.scalars["P_loss_MW"].value, ref(a, "LF", "P_loss_d_MW"))
                close(res.elements["G1"]["p_mw"].value, ref(a, "LF", "P_G1_d_MW"))
                close(voltages(res), ref(a, "LF", "U_d"))
                self.assertFalse([w for w in res.warnings if "override" in w.lower()])

    def test_shunt_as_a_rated_susceptance(self):
        for a, proj in self._each():
            with self.subTest(A=a):
                res = run(proj, "LFb_rated")
                close(voltages(res), ref(a, "LF_q_rated", "U"))
                close(res.matrices["Y"].data, ref(a, "LF_q_rated", "Y"))
                close(res.scalars["P_loss_MW"].value, ref(a, "LF_q_rated", "P_loss_MW"))
                close(res.elements["G1"]["p_mw"].value, ref(a, "LF_q_rated", "P_G1_MW"))
                close(res.elements["SVC3"]["q_mvar"].value,
                      ref(a, "LF_q_rated", "Q_sh3_MVAr"))
                close(res.elements["G7"]["q_mvar"].value, ref(a, "LF_q_rated", "Q_G7_MVAr"))
                close(res.elements["SVC3"]["b_pu"].value, ref(a, "LF_q_rated", "Bsh3_pu"))
                close(res.elements["SVC3"]["b_s"].value, ref(a, "LF_q_rated", "Bsh3_S"))
                close(res.elements["SH5"]["q_mvar"].value,
                      ref(a, "LF_q_rated", "Q_sh5_out_MVAr"))


class Jacobian(unittest.TestCase):
    def setUp(self):
        model = build_model(build_lf(80.0), ModelOptions(
            energized_only=True, load_model="as_set", shunt_model="as_set"))
        self.Y, _ = model.seq().ybus("exclude")
        rng = np.random.default_rng(7)
        n = self.Y.shape[0]
        self.u = (0.95 + 0.1 * rng.random(n)) * np.exp(1j * rng.uniform(-0.5, 0.5, n))

    def test_blocks_against_finite_differences(self):
        Y, u = self.Y, self.u
        H, N, J, L = jacobian(Y, u)
        h = 1e-7
        for k in range(len(u)):
            step = np.zeros(len(u))
            step[k] = h
            ds_dth = (bus_powers(Y, u * np.exp(1j * step))
                      - bus_powers(Y, u * np.exp(-1j * step))) / (2 * h)
            ds_du = (bus_powers(Y, u * (1 + step)) - bus_powers(Y, u * (1 - step))) / (2 * h)
            np.testing.assert_allclose(H[:, k], ds_dth.real, atol=1e-6)
            np.testing.assert_allclose(J[:, k], ds_dth.imag, atol=1e-6)
            np.testing.assert_allclose(N[:, k], ds_du.real, atol=1e-6)
            np.testing.assert_allclose(L[:, k], ds_du.imag, atol=1e-6)

    def test_course_formulas_on_the_diagonal(self):
        Y, u = self.Y, self.u
        H, N, J, L = jacobian(Y, u)
        s = bus_powers(Y, u)
        G, B, U2 = Y.real, Y.imag, np.abs(u) ** 2
        close(np.diag(H), -s.imag - np.diag(B) * U2, rtol=1e-12)
        close(np.diag(N), s.real + np.diag(G) * U2, rtol=1e-12)
        close(np.diag(J), s.real - np.diag(G) * U2, rtol=1e-12)
        close(np.diag(L), s.imag - np.diag(B) * U2, rtol=1e-12)

    def test_flat_start_jacobian_is_labelled(self):
        res = run_case(build_lf(80.0), Case("lf", "loadflow"))
        j0 = res.matrices["J0"]
        self.assertEqual(j0.data.shape, (12, 12))        # 7 angles + 5 magnitudes
        self.assertEqual(j0.rows[0], "P 2")
        self.assertEqual(j0.rows[7], "Q 2")
        self.assertEqual(j0.cols[7], "U/U 2")


class LoadFlowBehaviour(unittest.TestCase):
    def test_reactive_limit_turns_a_pu_bus_into_pq(self):
        net = build_lf(80.0)
        net.get("G7").q_max_mvar = 20.0
        res = run_case(net, Case("lf", "loadflow", options={"q_limits": True}))
        self.assertEqual(res.info["type:b7"], "PQ")
        close(res.elements["G7"]["q_mvar"].value, 20.0, rtol=1e-12)
        self.assertLess(abs(res.buses["b7"]["u"].value), 1.0)
        self.assertTrue(any("reactive limit" in w for w in res.warnings))
        # Without enforcing them the limit is ignored
        res = run_case(net, Case("lf", "loadflow", options={"q_limits": False}))
        self.assertEqual(res.info["type:b7"], "PU")

    def test_an_island_without_slack_is_refused(self):
        net = build_lf(80.0)
        net.get("G1").control = "pv"
        with self.assertRaises(TopologyError):
            run_case(net, Case("lf", "loadflow"))

    def test_conflicting_voltage_setpoints_are_refused(self):
        net = build_lf(80.0)
        net.add(Generator("G7b", bus="b7", control="pv", p_mw=10.0, u_set_pu=1.02))
        with self.assertRaises(ValidationError):
            run_case(net, Case("lf", "loadflow"))

    def test_two_generators_on_one_bus_share_the_reactive_power(self):
        net = build_lf(80.0)
        net.get("G7").p_mw = 108.5
        net.add(Generator("G7b", bus="b7", control="pv", p_mw=100.0, u_set_pu=1.0))
        res = run_case(net, Case("lf", "loadflow"))
        close(res.scalars["P_loss_MW"].value, ref(80, "LF", "P_loss_MW"))
        q = res.elements["G7"]["q_mvar"].value + res.elements["G7b"]["q_mvar"].value
        close(q, ref(80, "LF", "Q_G7_MVAr"))

    def test_constant_impedance_loads_take_less_below_rated_voltage(self):
        res = run_case(build_lf(80.0), Case("lf", "loadflow",
                                            options={"load_model": "constant_impedance"}))
        self.assertEqual(res.info["converged"], "yes")
        p_ld4 = res.elements["LD4"]["s"].value.real
        u4 = abs(res.buses["b4"]["u"].value)
        close(p_ld4, 166.5 * u4 ** 2, rtol=1e-12)
        self.assertLess(abs(res.scalars["balance_MW"].value), 1e-6)

    def test_divergence_is_reported_not_raised(self):
        net = build_lf(80.0)
        net.get("LD4").p_mw = 5000.0
        res = run_case(net, Case("lf", "loadflow", options={"max_iter": 8}))
        self.assertEqual(res.info["converged"], "no")
        self.assertTrue(any("did not converge" in w for w in res.warnings))

    def test_other_studies_see_loads_as_impedances(self):
        """Constant-power loads are a load-flow notion: the linear model keeps Z."""
        model = build_model(build_lf(80.0), ModelOptions(energized_only=True))
        self.assertEqual({sh.comp_id for sh in model.seq().shunts},
                         {"LD4", "LD6", "LD8", "SH5"})
        self.assertEqual(model.injections()[0].comp_id, "G1")
        sol = solve_load_flow(build_model(build_lf(80.0), ModelOptions(
            energized_only=True, load_model="as_set", shunt_model="as_set")))
        self.assertEqual(sol.sm.shunts, [])


class PandapowerCrossCheck(unittest.TestCase):
    """An independent solver on the same data.

    Opt-in with ``PSA_ORACLES=1``: importing pandapower takes around 15 s here,
    longer than the rest of the suite. Skipped without pandapower.
    """

    def setUp(self):
        if os.environ.get("PSA_ORACLES") != "1":
            self.skipTest("set PSA_ORACLES=1 to run the pandapower cross-check")
        try:
            import pandapower  # noqa: F401 - only checks it imports
        except Exception as exc:  # noqa: BLE001 - any import failure means skip
            self.skipTest(f"pandapower unavailable: {exc}")

    def test_lfa_matches_pandapower(self):
        import pandapower as pp

        a = 80.0
        net = build_lf(a)
        pn = pp.create_empty_network(sn_mva=100.0, f_hz=50.0)
        idx = {b: pp.create_bus(pn, vn_kv=220.0, name=b) for b in BUSES}
        for i, j, length in LF_LINES:
            pp.create_line_from_parameters(
                pn, idx[f"b{i}"], idx[f"b{j}"], length_km=length, r_ohm_per_km=0.05,
                x_ohm_per_km=0.5, c_nf_per_km=3.6e-6 / (2 * math.pi * 50.0) * 1e9,
                max_i_ka=2.0)
        pp.create_ext_grid(pn, idx["b1"], vm_pu=1.0, va_degree=0.0)
        pp.create_gen(pn, idx["b7"], p_mw=208.5, vm_pu=1.0)
        pp.create_gen(pn, idx["b3"], p_mw=0.0, vm_pu=1.0)
        for bus in ("b4", "b6", "b8"):
            ld = net.get("LD" + bus[1:])
            pp.create_load(pn, idx[bus], p_mw=ld.p_mw, q_mvar=ld.q_mvar)
        pp.create_sgen(pn, idx["b5"], p_mw=0.0, q_mvar=net.get("SH5").q_mvar)
        pp.runpp(pn, tolerance_mva=1e-10, init="flat", calculate_voltage_angles=True,
                 numba=False)

        res = run_case(net, Case("lf", "loadflow"))
        vm = [abs(res.buses[b]["u"].value) for b in BUSES]
        va = [res.buses[b]["theta_deg"].value for b in BUSES]
        np.testing.assert_allclose(vm, pn.res_bus.vm_pu.values, rtol=1e-8)
        np.testing.assert_allclose(va, pn.res_bus.va_degree.values, atol=1e-7)
        np.testing.assert_allclose(res.elements["G1"]["p_mw"].value,
                                   pn.res_ext_grid.p_mw.values[0], rtol=1e-8)
        np.testing.assert_allclose(res.scalars["P_loss_MW"].value,
                                   pn.res_line.pl_mw.sum(), rtol=1e-8)


if __name__ == "__main__":
    unittest.main()
