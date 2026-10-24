"""Project round-trip, auto-layout and the validation / error paths."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from psa.core.analysis import equivalent_impedance, solve_voltages
from psa.core.bases import propagate_bases
from psa.core.components import Bus, Line, Load, Source, Transformer
from psa.core.errors import (
    DecoupledPortsError,
    SingularReductionError,
    TopologyError,
    ValidationError,
)
from psa.core.examples import build_s1, s1_parameter_bindings
from psa.core.network import Network
from psa.core.params import Parameters
from psa.core.perunit import to_per_unit
from psa.core.serialize import Project, auto_layout, load, save
from psa.core.twoport import two_port


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "s1.psn"

    def tearDown(self):
        self.dir.cleanup()

    def _project(self) -> Project:
        net = build_s1(80.0)
        project_vars, bindings = s1_parameter_bindings(80.0)
        proj = Project(network=net,
                       params=Parameters(project_vars=project_vars,
                                         bindings=bindings),
                       meta={"title": "EG2100 Assignment S1, A = 80"})
        proj.ensure_layout()
        return proj

    def test_save_load_gives_identical_results(self):
        proj = self._project()
        save(proj, self.path)
        reloaded = load(self.path)

        for net in (proj.network, reloaded.network):
            eq = equivalent_impedance(net, "bus_a")
            self.assertAlmostEqual(eq.z_pu.real, 22.5232, places=4)
            self.assertAlmostEqual(eq.z_pu.imag, 11.1298, places=4)

        tp_a = two_port(proj.network, "bus_a", "bus_e")
        tp_b = two_port(reloaded.network, "bus_a", "bus_e")
        self.assertLess(float(np.max(np.abs(tp_a.f - tp_b.f))), 1e-15)

    def test_resaving_is_byte_identical(self):
        proj = self._project()
        save(proj, self.path)
        first = self.path.read_text(encoding="utf-8")
        again = load(self.path)
        second_path = self.path.with_name("again.psn")
        save(again, second_path)
        self.assertEqual(first, second_path.read_text(encoding="utf-8"))

    def test_parameters_survive_the_round_trip(self):
        proj = self._project()
        save(proj, self.path)
        reloaded = load(self.path)
        self.assertEqual(dict(reloaded.params.project_vars), {"A": "80.0"})
        self.assertEqual(reloaded.params.expression_for("LIbc", "length_km"),
                         "(217 + A)/1000")

    def test_network_section_alone_is_enough(self):
        proj = self._project()
        save(proj, self.path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for section in ("layout", "view", "studies", "parameters"):
            data.pop(section, None)
        stripped = self.path.with_name("bare.psn")
        stripped.write_text(json.dumps(data), encoding="utf-8")

        proj2 = load(stripped)
        proj2.ensure_layout()
        self.assertEqual(len(proj2.layout), len(list(proj2.network)))
        eq = equivalent_impedance(proj2.network, "bus_a")
        self.assertAlmostEqual(eq.z_pu.real, 22.5232, places=4)

    def test_complex_field_round_trips(self):
        net = build_s1(80.0)
        net.get("SRC").z_th_ohm = 1.5 + 2.5j
        proj = Project(network=net)
        save(proj, self.path)
        back = load(self.path).network.get("SRC").z_th_ohm
        self.assertEqual(back, 1.5 + 2.5j)

    def test_opening_a_project_does_not_run_its_script(self):
        """A linked script is ordinary Python, so opening a file must not execute it."""
        script = Path(self.dir.name) / "vars.py"
        sentinel = Path(self.dir.name) / "script_ran.txt"
        script.write_text(
            "from pathlib import Path\n"
            f"Path({str(sentinel)!r}).write_text('ran')\n"
            "A = 0\n", encoding="utf-8")
        proj = self._project()
        proj.params.script_path = str(script)
        proj.params.project_vars = []        # A now comes only from the script
        save(proj, self.path)

        reloaded = load(self.path)
        self.assertFalse(sentinel.exists(), "load() executed the linked script")
        # Without the script A is unknown, so the value saved for A = 80 stays.
        self.assertAlmostEqual(reloaded.network.get("LIbc").length_km, 0.297)

        trusted = load(self.path, run_script=True)
        self.assertTrue(sentinel.exists())
        self.assertAlmostEqual(trusted.network.get("LIbc").length_km, 0.217)

    def test_unknown_format_is_rejected(self):
        self.path.write_text('{"format": "something-else"}', encoding="utf-8")
        with self.assertRaises(ValidationError):
            load(self.path)

    def test_newer_version_is_rejected_clearly(self):
        self.path.write_text(
            json.dumps({"format": "psa-project", "version": 99,
                        "network": {"buses": [], "components": []}}),
            encoding="utf-8")
        with self.assertRaises(ValidationError) as ctx:
            load(self.path)
        self.assertIn("newer version", str(ctx.exception))


class AutoLayout(unittest.TestCase):
    def test_every_object_gets_a_position(self):
        net = build_s1(80.0)
        layout = auto_layout(net)
        for obj in net:
            self.assertIn(obj.id, layout)
            self.assertIn("x", layout[obj.id])

    def test_buses_advance_left_to_right(self):
        net = build_s1(80.0)
        layout = auto_layout(net)
        xs = [layout[b][ "x"] for b in
              ("bus_a", "bus_b", "bus_c", "bus_d", "bus_e")]
        self.assertEqual(xs, sorted(xs))
        self.assertLess(xs[0], xs[-1])

    def test_positions_are_on_the_grid(self):
        net = build_s1(80.0)
        layout = auto_layout(net, grid=10)
        for item in layout.values():
            self.assertEqual(item["x"] % 10, 0)
            self.assertEqual(item["y"] % 10, 0)


class Validation(unittest.TestCase):
    def test_missing_base_reference_is_an_error(self):
        net = build_s1(80.0)
        net.base_ref_bus = ""
        codes = [i.code for i in net.validate() if i.is_error]
        self.assertIn("no_base_ref", codes)

    def test_dangling_terminal_is_an_error(self):
        net = build_s1(80.0)
        net.get("TI1").bus_lv = "nope"
        codes = [i.code for i in net.validate() if i.is_error]
        self.assertIn("dangling", codes)

    def test_duplicate_id_is_rejected(self):
        net = build_s1(80.0)
        with self.assertRaises(ValidationError):
            net.add(Bus("bus_a"))

    def test_line_across_a_base_change_is_rejected(self):
        """Only reachable with pinned bases, so drive line_pu directly."""
        from psa.core.bases import BaseTable
        from psa.core.perunit import line_pu

        bases = BaseTable(s_base_mva=10.0,
                          u_base_kv={"hi": 12.0, "lo": 6.0},
                          z_base_ohm={"hi": 14.4, "lo": 3.6},
                          i_base_ka={"hi": 0.4811, "lo": 0.9623})
        bad = Line("BAD", bus_from="hi", bus_to="lo", name="BAD",
                   r_ohm_per_km=0.16, x_ohm_per_km=0.08, length_km=1.0)
        with self.assertRaises(ValidationError) as ctx:
            line_pu(bad, bases)
        self.assertIn("voltage base", str(ctx.exception))

    def test_out_of_service_branch_keeps_the_downstream_base(self):
        """A base is a nameplate property; opening a breaker must not remove it."""
        net = build_s1(80.0)
        net.get("TI2").in_service = False
        table = propagate_bases(net)
        self.assertAlmostEqual(table.u("bus_e"), 6.0, places=9)
        self.assertEqual(table.unreachable, [])

    def test_inconsistent_transformer_loop_is_reported(self):
        net = build_s1(80.0)
        # Close a loop a -> b with a second transformer of the wrong ratio.
        net.add(Transformer("TX", bus_hv="bus_a", bus_lv="bus_b", name="TX",
                            sn_mva=5.0, u1n_kv=40.0, u2n_kv=20.0, x_pct=5.0))
        table = propagate_bases(net)
        self.assertTrue(table.conflicts)
        self.assertEqual(table.conflicts[0].bus, "bus_b")

    def test_unreachable_bus_is_reported(self):
        net = build_s1(80.0)
        net.add(Bus("island", name="island"))
        table = propagate_bases(net)
        self.assertIn("island", table.unreachable)
        with self.assertRaises(ValidationError):
            to_per_unit(net)

    def test_floating_bus_gives_a_readable_reduction_error(self):
        net = Network(s_base_mva=10.0, base_ref_bus="b1", base_ref_u_kv=40.0)
        net.add(Bus("b1", name="b1"))
        net.add(Bus("b2", name="b2"))
        net.add(Bus("b3", name="b3"))
        net.add(Source("SRC", bus="b1"))
        net.add(Line("L1", bus_from="b1", bus_to="b2", name="L1",
                     r_ohm_per_km=0.1, x_ohm_per_km=0.1, length_km=1.0))
        net.add(Line("L2", bus_from="b2", bus_to="b3", name="L2",
                     r_ohm_per_km=0.1, x_ohm_per_km=0.1, length_km=1.0))
        # b3 is a dead end with nothing to ground.
        with self.assertRaises((SingularReductionError, TopologyError)):
            equivalent_impedance(net, "b1")

    def test_no_source_gives_a_readable_message(self):
        net = build_s1(80.0)
        net.remove("SRC")
        with self.assertRaises(TopologyError) as ctx:
            solve_voltages(net)
        self.assertIn("no source", str(ctx.exception).lower())

    def test_two_port_between_the_same_bus_is_rejected(self):
        net = build_s1(80.0)
        with self.assertRaises(TopologyError):
            two_port(net, "bus_a", "bus_a")

    def test_disconnected_ports_are_reported(self):
        net = build_s1(80.0)
        net.get("TI2").in_service = False
        with self.assertRaises(DecoupledPortsError):
            two_port(net, "bus_a", "bus_e")

    def test_constant_power_load_is_flagged(self):
        """The linear studies cannot honour it, so they must say so."""
        net = build_s1(80.0)
        net.get("LDe").model = "constant_power"
        codes = [i.code for i in to_per_unit(net).issues]
        self.assertIn("load_model", codes)
        self.assertNotIn("load_model",
                         [i.code for i in to_per_unit(build_s1(80.0)).issues])

    def test_voltage_study_skips_the_single_source_chain_with_two_sources(self):
        """U_a / Z_I is the feeding current only when that source is alone."""
        from psa.core.study import STUDY_REGISTRY

        net = build_s1(80.0)
        net.add(Source("SRC2", bus="bus_e", name="Local", z_th_ohm=2.0 + 4.0j))
        res = STUDY_REGISTRY["s1c"](net, {"bus": "bus_e"})
        self.assertNotIn("Phasor chain", [t.title for t in res.tables])
        self.assertTrue(any("more than one source" in w for w in res.warnings))

        single = STUDY_REGISTRY["s1c"](build_s1(80.0), {"bus": "bus_e"})
        self.assertIn("Phasor chain", [t.title for t in single.tables])


class MeshedNetwork(unittest.TestCase):
    """The general method must carry the cases the cascade cannot."""

    def _meshed(self) -> Network:
        net = Network(s_base_mva=10.0, base_ref_bus="b1", base_ref_u_kv=40.0)
        for n in ("b1", "b2", "b3"):
            net.add(Bus(n, name=n))
        net.add(Source("SRC", bus="b1"))
        line = dict(r_ohm_per_km=0.5, x_ohm_per_km=1.0, b_s_per_km=3e-6)
        net.add(Line("L12", bus_from="b1", bus_to="b2", name="L12",
                     length_km=10.0, **line))
        net.add(Line("L23", bus_from="b2", bus_to="b3", name="L23",
                     length_km=10.0, **line))
        net.add(Line("L13", bus_from="b1", bus_to="b3", name="L13",
                     length_km=25.0, **line))
        net.add(Load("LD3", bus="b3", name="LD3", p_mw=2.0, cos_phi=0.95))
        return net

    def test_cascade_unavailable_but_general_method_works(self):
        tp = two_port(self._meshed(), "b1", "b3")
        self.assertEqual(tp.cascade_status, "not_unique")
        self.assertIsNone(tp.f_cascade)
        self.assertAlmostEqual(abs(tp.det - 1.0), 0.0, places=9)

    def test_equivalent_impedance_matches_the_two_port_route(self):
        net = self._meshed()
        eq = equivalent_impedance(net, "b1", far_bus="b3")
        self.assertTrue(np.isclose(eq.z_pu, eq.z_pu_twoport,
                                   rtol=1e-9, atol=1e-12))

    def test_nodal_solve_is_consistent_with_the_equivalent_impedance(self):
        net = self._meshed()
        lf = solve_voltages(net)
        eq = equivalent_impedance(net, "b1")
        i_expected = lf.u_pu["b1"] / eq.z_pu
        self.assertLess(abs(lf.i_source_pu["SRC"] - i_expected), 1e-12)


class ParallelDrawnAsTwoLines(unittest.TestCase):
    """Drawing two cables separately must give the same answer as n_parallel=2."""

    def test_two_lines_equal_one_line_with_n_parallel_two(self):
        merged = build_s1(80.0)

        split = build_s1(80.0)
        original = split.get("LIbc")
        split.remove("LIbc")
        for suffix in ("1", "2"):
            split.add(Line(f"LIbc{suffix}", bus_from="bus_b", bus_to="bus_c",
                           name=f"LIbc{suffix}",
                           r_ohm_per_km=original.r_ohm_per_km,
                           x_ohm_per_km=original.x_ohm_per_km,
                           b_s_per_km=original.b_s_per_km,
                           length_km=original.length_km, n_parallel=1))

        z_merged = equivalent_impedance(merged, "bus_a").z_pu
        z_split = equivalent_impedance(split, "bus_a").z_pu
        self.assertLess(abs(z_merged - z_split), 1e-12)

        tp = two_port(split, "bus_a", "bus_e")
        self.assertEqual(tp.cascade_status, "parallel_merged")
        self.assertLess(tp.residual_rel, 1e-9)
        self.assertLess(
            float(np.max(np.abs(tp.f - two_port(merged, "bus_a", "bus_e").f))),
            1e-12)


if __name__ == "__main__":
    unittest.main()
