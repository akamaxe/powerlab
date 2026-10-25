"""The core v2 model: element models, stamps and sequence networks.

The parity tests pin the refactor against ``tests/golden/pumodel_v1.json``,
the per-unit model written by the previous implementation, and against the
console output captured before the refactor (``tests/golden/*.txt``).
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

import numpy as np

import run_s1_headless
from psa.core.components import (
    COMPONENT_CLASSES,
    Bus,
    Impedance,
    Line,
    Load,
    Transformer,
    is_series,
    unconnected_terminals,
)
from psa.core.errors import ValidationError
from psa.core.examples import build_s1
from psa.core.model import (
    INTERNAL_NODE_SUFFIX,
    ModelOptions,
    Seq,
    build_model,
    model_for,
)
from psa.core.perunit import to_per_unit
from psa.core.serialize import load
from psa.core.ybus import build_ybus
from tests.refdata import A_NUMBERS, ref
from tests.test_s2_thevenin import build_s2

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden"


def _c(pair):
    return None if pair is None else complex(pair[0], pair[1])


def _close(a, b, rtol=1e-15):
    if a is None or b is None:
        return a is b
    return abs(a - b) <= rtol * max(abs(a), abs(b), 1e-300)


class ParityWithTheV1PerUnitModel(unittest.TestCase):
    """to_per_unit reproduces what the previous implementation produced."""

    @classmethod
    def setUpClass(cls):
        cls.snapshot = json.loads((GOLDEN / "pumodel_v1.json").read_text(encoding="utf-8"))
        cls.networks = {
            "s1_A80": build_s1(80.0), "s1_A0": build_s1(0.0),
            "s2_A80": build_s2(80.0), "s2_A0": build_s2(0.0),
            "s1_example_psn": load(ROOT / "examples" / "s1_a80.psn").network,
        }

    def test_every_case(self):
        for name, snap in self.snapshot.items():
            with self.subTest(case=name):
                pu = to_per_unit(self.networks[name])
                self.assertEqual(pu.buses, snap["buses"])
                self.assertEqual(pu.labels, snap["labels"])
                self.assertEqual(pu.bases.u_base_kv, snap["u_base_kv"])
                self.assertEqual(pu.bases.z_base_ohm, snap["z_base_ohm"])
                self.assertEqual(pu.bases.i_base_ka, snap["i_base_ka"])
                self.assertEqual([[u, ids] for u, ids in pu.bases.zones], snap["zones"])

                self.assertEqual(len(pu.series), len(snap["series"]))
                for got, (cid, bi, bj, z, ysh) in zip(pu.series, snap["series"]):
                    self.assertEqual((got.comp_id, got.bus_i, got.bus_j), (cid, bi, bj))
                    self.assertTrue(_close(got.z_pu, _c(z)), (cid, got.z_pu, z))
                    self.assertTrue(_close(got.ysh_pu, _c(ysh)), (cid, got.ysh_pu, ysh))
                self.assertEqual(len(pu.shunts), len(snap["shunts"]))
                for got, (cid, bus, y, kind) in zip(pu.shunts, snap["shunts"]):
                    self.assertEqual((got.comp_id, got.bus, got.kind), (cid, bus, kind))
                    self.assertTrue(_close(got.y_pu, _c(y)), (cid, got.y_pu, y))
                self.assertEqual(len(pu.sources), len(snap["sources"]))
                for got, (cid, bus, u, z, slack) in zip(pu.sources, snap["sources"]):
                    self.assertEqual((got.comp_id, got.bus, got.is_slack), (cid, bus, slack))
                    self.assertTrue(_close(got.u_pu, _c(u)))
                    self.assertTrue(_close(got.z_th_pu, _c(z)))

                self.assertEqual(len(pu.trace), len(snap["trace"]))
                for got, want in zip(pu.trace, snap["trace"]):
                    for key, value in want.items():
                        if want["comp_type"] == "source" and key == "z_ohm":
                            continue        # added on purpose: the grid's Z in ohm
                        mine = getattr(got, key)
                        if isinstance(mine, complex) or isinstance(value, list):
                            self.assertTrue(_close(mine, _c(value)), (got.comp_id, key))
                        elif isinstance(mine, float) and np.isnan(mine):
                            self.assertTrue(value is None or np.isnan(value))
                        else:
                            self.assertEqual(mine, value, (got.comp_id, key))
                self.assertEqual([[i.severity, i.code, i.message, i.ids] for i in pu.issues],
                                 snap["issues"])

    def test_ybus_from_stamps_matches_the_v1_matrix(self):
        for name, snap in self.snapshot.items():
            with self.subTest(case=name):
                model = build_model(self.networks[name]).seq(Seq.POS)
                for mode, key in (("grounded", "ybus_sources"), ("exclude", "ybus_passive")):
                    Y, index = model.ybus(mode)
                    self.assertEqual(list(index), snap["ybus_order"])
                    want = np.array([[_c(v) for v in row] for row in snap[key]])
                    np.testing.assert_allclose(Y, want, rtol=1e-15, atol=0)
                    # The legacy path over the adapter agrees too.
                    legacy, _ = build_ybus(to_per_unit(self.networks[name]),
                                           include_sources=(mode == "grounded"))
                    np.testing.assert_array_equal(Y, legacy)


class ConsoleOutputIsUnchanged(unittest.TestCase):
    """The S1 console report matches the output captured before the refactor."""

    def _run(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(run_s1_headless.main(list(argv)), 0)
        return buf.getvalue()

    def _golden(self, name):
        return (GOLDEN / name).read_text(encoding="utf-8")

    def test_builtin_network_a80(self):
        self.assertEqual(self._run(), self._golden("s1_builtin_A80.txt"))

    def test_builtin_network_a0(self):
        self.assertEqual(self._run("--a-number", "0"), self._golden("s1_builtin_A0.txt"))

    def test_example_project(self):
        out = self._run(str(ROOT / "examples" / "s1_a80.psn"))
        self.assertEqual(out, self._golden("s1_example_psn.txt"))


class S2YbusAgainstMatlab(unittest.TestCase):
    """The two ways the MATLAB solution writes the S2 admittance matrix."""

    def test_source_as_internal_node_is_the_9x9_matrix(self):
        for a in A_NUMBERS:
            with self.subTest(A=a):
                Y, index = build_model(build_s2(a)).seq(Seq.POS).ybus("internal_node")
                self.assertEqual(list(index)[-1], "GRID" + INTERNAL_NODE_SUFFIX)
                np.testing.assert_allclose(Y, ref(a, "S2", "Y"), rtol=1e-9, atol=1e-9)

    def test_grounded_source_is_the_8x8_matrix(self):
        for a in A_NUMBERS:
            with self.subTest(A=a):
                Y, _ = build_model(build_s2(a)).seq(Seq.POS).ybus("grounded")
                np.testing.assert_allclose(Y, ref(a, "S2", "Ydelta"), rtol=1e-9, atol=1e-9)


class Terminals(unittest.TestCase):
    def test_bus_fields_follow_the_terminals(self):
        for cls in COMPONENT_CLASSES:
            with self.subTest(cls=cls.__name__):
                self.assertEqual(cls.BUS_FIELDS, tuple(t.field for t in cls.TERMINALS))

    def test_every_component_class_has_an_element_model(self):
        for cls in COMPONENT_CLASSES:
            if cls is Bus:
                continue
            with self.subTest(cls=cls.__name__):
                model_for(cls(id="x"))

    def test_series_is_structural_not_a_wiring_state(self):
        self.assertTrue(is_series(Line("L")))             # unwired, still a branch
        self.assertFalse(is_series(Impedance("Z", bus_from="a")))   # to ground
        self.assertTrue(is_series(Impedance("Z", bus_from="a", bus_to="b")))
        self.assertFalse(is_series(Load("LD")))
        self.assertEqual(unconnected_terminals(Line("L", bus_from="a")), ["bus_to"])
        self.assertEqual(unconnected_terminals(Impedance("Z", bus_from="a")), [])


class Robustness(unittest.TestCase):
    def test_an_unwired_component_is_a_warning_and_is_ignored(self):
        net = build_s1(80.0)
        net.add(Load("LOOSE", name="Loose load", p_mw=1.0, cos_phi=0.9))
        net.add(Line("HALF", bus_from="bus_b", name="Half-drawn line"))
        issues = {i.code: i for i in net.validate()}
        self.assertEqual(issues["unconnected"].severity, "warning")
        pu = to_per_unit(net)
        self.assertNotIn("LOOSE", [s.comp_id for s in pu.shunts])
        self.assertNotIn("HALF", [s.comp_id for s in pu.series])
        ref_pu = to_per_unit(build_s1(80.0))
        Y, _ = build_ybus(pu)
        Y_ref, _ = build_ybus(ref_pu)
        np.testing.assert_array_equal(Y, Y_ref)

    def test_zero_length_line_is_a_readable_error(self):
        net = build_s1(80.0)
        net.components["LIcd"].length_km = 0.0
        with self.assertRaises(ValidationError) as ctx:
            to_per_unit(net)
        self.assertIn("LIcd", ctx.exception.ids)

    def test_zero_impedance_transformer_is_a_readable_error(self):
        net = build_s1(80.0)
        tf = net.components["TI2"]
        tf.x_pct = tf.r_pct = 0.0
        with self.assertRaises(ValidationError) as ctx:
            to_per_unit(net)
        self.assertIn("TI2", ctx.exception.ids)

    def test_islands(self):
        net = build_s1(80.0)
        net.add(Bus("lonely", name="Lonely"))
        net.add(Load("LDX", bus="lonely", p_mw=1.0, cos_phi=0.9))
        islands = net.islands()
        self.assertIn({"lonely"}, islands)
        self.assertEqual(len(islands), 2)

    def test_energized_only_drops_buses_without_a_source(self):
        net = build_s1(80.0)
        net.add(Bus("lonely", name="Lonely"))
        net.add(Load("LDX", bus="lonely", p_mw=1.0, cos_phi=0.9))
        with self.assertRaises(ValidationError):       # the S1 path stays strict
            to_per_unit(net)
        model = build_model(net, ModelOptions(energized_only=True))
        self.assertNotIn("lonely", model.buses)
        self.assertIn("de_energized", [i.code for i in model.validation_issues])
        sm = model.seq(Seq.POS)
        self.assertNotIn("LDX", [s.comp_id for s in sm.shunts])

    def test_the_zero_sequence_needs_line_data(self):
        with self.assertRaises(ValidationError) as ctx:
            build_model(build_s1(80.0)).seq(Seq.ZERO)
        self.assertIn("zero-sequence data", str(ctx.exception))
        build_model(build_s1(80.0)).seq(Seq.NEG)        # the negative one is fine

    def test_a_transformer_carries_the_base_both_ways(self):
        net = build_s1(80.0)
        tf: Transformer = net.components["TI1"]
        model = model_for(tf)
        self.assertEqual(model.base_ratio(tf, tf.bus_hv, tf.bus_lv), 12.0 / 40.0)
        self.assertEqual(model.base_ratio(tf, tf.bus_lv, tf.bus_hv), 40.0 / 12.0)


if __name__ == "__main__":
    unittest.main()
