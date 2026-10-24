"""GUI tests: editing, undo, connection sync and the parametric fields.

They run against the offscreen Qt platform, so no window ever appears. Text
cannot rasterise there (the plugin ships no fonts), so these tests assert
behaviour and geometry, never pixels.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import psa.gui  # noqa: E402,F401 - must come before PySide6, see its docstring
from PySide6.QtCore import QPointF, QSettings, Qt   # noqa: E402
from PySide6.QtWidgets import QApplication          # noqa: E402

from psa.core.analysis import equivalent_impedance     # noqa: E402
from psa.core.examples import build_s1, s1_parameter_bindings  # noqa: E402
from psa.core.params import Parameters                 # noqa: E402
from psa.core.serialize import Project                 # noqa: E402
from psa.gui.commands import SetField, delete_objects  # noqa: E402
from psa.gui.document import NetworkDocument, blank_document  # noqa: E402
from psa.gui.items import BusItem, LoadItem, TransformerItem  # noqa: E402
from psa.gui.scene import SchematicScene             # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "s1_a80.psn"

_app = QApplication.instance() or QApplication(["tests"])

# Keep the tests away from the real user settings (the registry on Windows):
# MainWindow saves its geometry and recent files on close.
_SETTINGS_DIR = tempfile.mkdtemp(prefix="powerlab-test-settings-")
atexit.register(shutil.rmtree, _SETTINGS_DIR, ignore_errors=True)
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _SETTINGS_DIR)


def s1_document() -> NetworkDocument:
    net = build_s1(80.0)
    project_vars, bindings = s1_parameter_bindings(80.0)
    proj = Project(network=net,
                   params=Parameters(project_vars=project_vars,
                                     bindings=bindings))
    proj.ensure_layout()
    return NetworkDocument(proj)


class SceneBuilding(unittest.TestCase):
    def setUp(self):
        self.doc = s1_document()
        self.scene = SchematicScene(self.doc)

    def test_every_object_gets_an_item(self):
        for obj in self.doc.network:
            self.assertIsNotNone(self.scene.item_for(obj.id), obj.id)

    def test_item_classes_match_component_types(self):
        self.assertIsInstance(self.scene.item_for("bus_a"), BusItem)
        self.assertIsInstance(self.scene.item_for("TI1"), TransformerItem)
        self.assertIsInstance(self.scene.item_for("LDb"), LoadItem)

    def test_wires_exist_for_every_connected_terminal(self):
        expected = sum(len(self.scene.item_for(c.id).TERMINALS)
                       for c in self.doc.network.components.values())
        self.assertEqual(len(self.scene._wires), expected)

    def test_wire_follows_the_component_when_it_moves(self):
        wire = self.scene._wires["TI1:0"]
        before = wire.path().pointAtPercent(0.0)
        item = self.scene.item_for("TI1")
        item.setPos(item.x(), item.y() + 40)
        after = wire.path().pointAtPercent(0.0)
        self.assertNotEqual((before.x(), before.y()), (after.x(), after.y()))

    def test_wire_follows_the_bus_when_it_moves(self):
        wire = self.scene._wires["TI1:0"]
        before = wire.path().currentPosition()
        bus = self.scene.item_for("bus_a")
        bus.setPos(bus.x(), bus.y() + 60)
        after = wire.path().currentPosition()
        self.assertNotEqual((before.x(), before.y()), (after.x(), after.y()))

    def test_bus_attach_point_is_projected_and_clamped(self):
        bus = self.scene.item_for("bus_a")
        centre = bus.scenePos()
        # A point beside the middle of the bar attaches at that height.
        p = bus.attach_point(QPointF(centre.x() + 50, centre.y() + 10))
        self.assertAlmostEqual(p.x(), centre.x(), places=6)
        self.assertAlmostEqual(p.y(), centre.y() + 10, places=6)
        # A point far below clamps to the lower end, not beyond it.
        p = bus.attach_point(QPointF(centre.x(), centre.y() + 900))
        self.assertAlmostEqual(p.y(), centre.y() + bus.length / 2, places=6)

    def test_items_snap_to_the_grid(self):
        item = self.scene.item_for("TI1")
        item.setPos(103.7, -47.2)
        self.assertEqual((item.x() % self.scene.grid, item.y() % self.scene.grid),
                         (0, 0))

    def test_out_of_service_component_still_draws(self):
        self.doc.undo.push(SetField(self.doc, "TI1", "in_service", False))
        item = self.scene.item_for("TI1")
        self.assertFalse(item.is_in_service())


class Placement(unittest.TestCase):
    def setUp(self):
        self.doc = blank_document()
        self.scene = SchematicScene(self.doc)

    def test_branch_dropped_between_two_buses_connects_to_both(self):
        self.scene.place_component("bus", QPointF(0, 0))
        self.scene.place_component("bus", QPointF(180, 0))
        tid = self.scene.place_component("transformer", QPointF(90, 0))
        tf = self.doc.network.get(tid)
        self.assertTrue(tf.bus_hv, "HV terminal did not find a bus")
        self.assertTrue(tf.bus_lv, "LV terminal did not find a bus")
        self.assertNotEqual(tf.bus_hv, tf.bus_lv)

    def test_shunt_dropped_below_a_bus_connects_to_it(self):
        bus = self.scene.place_component("bus", QPointF(0, 0))
        load = self.scene.place_component("load", QPointF(0, 70))
        self.assertEqual(self.doc.network.get(load).bus, bus)

    def test_component_dropped_in_open_space_stays_unconnected(self):
        self.scene.place_component("bus", QPointF(0, 0))
        load = self.scene.place_component("load", QPointF(900, 900))
        self.assertFalse(self.doc.network.get(load).bus)

    def test_new_buses_get_successive_letters(self):
        ids = [self.scene.place_component("bus", QPointF(i * 200, 0))
               for i in range(3)]
        names = [self.doc.network.get(i).name for i in ids]
        self.assertEqual(names, ["a", "b", "c"])

    def test_ids_are_numbered_per_type(self):
        self.scene.place_component("bus", QPointF(0, 0))
        t1 = self.scene.place_component("transformer", QPointF(200, 0))
        t2 = self.scene.place_component("transformer", QPointF(400, 0))
        self.assertEqual([t1, t2], ["T1", "T2"])

    def test_placed_components_have_usable_defaults(self):
        self.scene.place_component("bus", QPointF(0, 0))
        tid = self.scene.place_component("transformer", QPointF(200, 0))
        tf = self.doc.network.get(tid)
        self.assertGreater(tf.sn_mva, 0)
        self.assertGreater(tf.x_pct, 0)


class UndoRedo(unittest.TestCase):
    def setUp(self):
        self.doc = blank_document()
        self.scene = SchematicScene(self.doc)

    def test_add_then_undo_removes_the_object(self):
        self.scene.place_component("bus", QPointF(0, 0))
        self.assertEqual(len(self.doc.network.buses), 1)
        self.doc.undo.undo()
        self.assertEqual(len(self.doc.network.buses), 0)
        self.doc.undo.redo()
        self.assertEqual(len(self.doc.network.buses), 1)

    def test_deleting_a_bus_takes_its_dependents_and_undo_restores_all(self):
        bus = self.scene.place_component("bus", QPointF(0, 0))
        self.scene.place_component("load", QPointF(0, 70))
        self.assertEqual(len(self.doc.network.components), 1)

        delete_objects(self.doc, [bus])
        self.assertEqual(len(self.doc.network.buses), 0)
        self.assertEqual(len(self.doc.network.components), 0)

        self.doc.undo.undo()
        self.assertEqual(len(self.doc.network.buses), 1)
        self.assertEqual(len(self.doc.network.components), 1)
        self.assertEqual(self.doc.network.get("LD1").bus, bus)

    def test_deleted_items_survive_for_undo(self):
        """The classic Qt binding crash: undo must not touch a destroyed C++ object."""
        bus = self.scene.place_component("bus", QPointF(0, 0))
        delete_objects(self.doc, [bus])
        self.doc.undo.undo()
        self.doc.undo.redo()
        self.doc.undo.undo()
        self.assertIsNotNone(self.scene.item_for(bus))

    def test_repeated_edits_of_one_field_merge_into_one_step(self):
        self.scene.place_component("bus", QPointF(0, 0))
        tid = self.scene.place_component("transformer", QPointF(200, 0))
        depth = self.doc.undo.count()
        for value in (7.0, 8.0, 9.0):
            self.doc.undo.push(SetField(self.doc, tid, "x_pct", value))
        self.assertEqual(self.doc.undo.count(), depth + 1)
        self.assertEqual(self.doc.network.get(tid).x_pct, 9.0)

    def test_editing_marks_dirty_and_saving_clears_it(self):
        import tempfile

        self.assertFalse(self.doc.is_dirty)
        self.scene.place_component("bus", QPointF(0, 0))
        self.assertTrue(self.doc.is_dirty)
        with tempfile.TemporaryDirectory() as d:
            self.doc.save_to(Path(d) / "x.psn")
        self.assertFalse(self.doc.is_dirty)


class ResultsLifecycle(unittest.TestCase):
    def test_editing_invalidates_results(self):
        doc = s1_document()
        SchematicScene(doc)
        doc.mark_results_valid()
        self.assertTrue(doc.results_valid)
        doc.undo.push(SetField(doc, "TI1", "x_pct", 14.0))
        self.assertFalse(doc.results_valid)

    def test_changing_reactance_changes_the_answer(self):
        doc = s1_document()
        before = equivalent_impedance(doc.network, "bus_a").z_pu
        doc.undo.push(SetField(doc, "TI1", "x_pct", 14.0))
        after = equivalent_impedance(doc.network, "bus_a").z_pu
        self.assertGreater(abs(after - before), 1e-6)
        doc.undo.undo()
        restored = equivalent_impedance(doc.network, "bus_a").z_pu
        self.assertAlmostEqual(abs(restored - before), 0.0, places=12)


class ParametricFields(unittest.TestCase):
    """The property editor's spreadsheet-like numeric fields."""

    def setUp(self):
        from psa.gui.panels import PropertyEditor

        self.doc = s1_document()
        self.editor = PropertyEditor(self.doc)

    def test_field_shows_the_bound_expression(self):
        self.editor.bind("LIbc")
        widget = self.editor._widgets["length_km"]
        self.assertEqual(widget.text(), "(217 + A)/1000")
        self.assertEqual(widget.property("bound"), "true")

    def test_typing_a_plain_number_clears_the_binding(self):
        self.editor.bind("LIbc")
        self.editor._commit_numeric(
            "length_km", _spec(self.doc, "LIbc", "length_km"), "0.5")
        self.assertIsNone(self.doc.params.expression_for("LIbc", "length_km"))
        self.assertAlmostEqual(self.doc.network.get("LIbc").length_km, 0.5)

    def test_typing_an_expression_creates_a_binding(self):
        self.editor.bind("TI1")
        self.editor._commit_numeric(
            "x_pct", _spec(self.doc, "TI1", "x_pct"), "A/10")
        self.assertEqual(self.doc.params.expression_for("TI1", "x_pct"), "A/10")
        self.assertAlmostEqual(self.doc.network.get("TI1").x_pct, 8.0)

    def test_a_bad_expression_changes_nothing(self):
        self.editor.bind("TI1")
        before = self.doc.network.get("TI1").x_pct
        self.editor._commit_numeric(
            "x_pct", _spec(self.doc, "TI1", "x_pct"), "A/nope")
        self.assertEqual(self.doc.network.get("TI1").x_pct, before)
        self.assertIsNone(self.doc.params.expression_for("TI1", "x_pct"))

    def test_changing_A_reevaluates_every_bound_field(self):
        self.doc.params.set_var("A", "0")
        self.doc.apply_parameters()
        self.assertAlmostEqual(self.doc.network.get("LIbc").length_km, 0.217)
        self.assertAlmostEqual(self.doc.network.get("LDb").p_mw, 0.117)
        z = equivalent_impedance(self.doc.network, "bus_a").z_pu
        # The answer printed in the assignment for A = 0.
        self.assertAlmostEqual(z.real, 22.5156, places=4)
        self.assertAlmostEqual(z.imag, 11.0767, places=4)

    def test_complex_value_is_stored_and_undoable(self):
        """Typing Z_th used to raise inside an open macro and wreck the undo stack."""
        spec = _spec(self.doc, "SRC", "z_th_ohm")
        for text, expected in (("1.6+0.3j", 1.6 + 0.3j), ("2-1i", 2 - 1j)):
            self.editor.bind("SRC")
            self.editor._commit_numeric("z_th_ohm", spec, text)
            self.assertEqual(self.doc.network.get("SRC").z_th_ohm, expected, text)
            self.doc.undo.undo()           # impossible if a macro were left open
            self.assertEqual(self.doc.network.get("SRC").z_th_ohm, 0j)

    def test_complex_expression_creates_a_binding(self):
        self.editor.bind("SRC")
        self.editor._commit_numeric(
            "z_th_ohm", _spec(self.doc, "SRC", "z_th_ohm"), "(1 + 2j)*A/80")
        self.assertEqual(self.doc.network.get("SRC").z_th_ohm, 1 + 2j)
        self.assertEqual(self.doc.params.expression_for("SRC", "z_th_ohm"),
                         "(1 + 2j)*A/80")

    def test_complex_result_in_a_real_field_is_rejected(self):
        self.editor.bind("TI1")
        depth = self.doc.undo.count()
        self.editor._commit_numeric("x_pct", _spec(self.doc, "TI1", "x_pct"), "7+1j")
        self.assertEqual(self.doc.network.get("TI1").x_pct, 7.0)
        self.assertEqual(self.doc.undo.count(), depth)
        self.assertIsNone(self.doc.params.expression_for("TI1", "x_pct"))

    def test_leaving_a_field_untouched_adds_no_undo_step(self):
        """editingFinished also fires when focus simply moves on."""
        for obj_id, field in (("TI1", "x_pct"), ("LIbc", "length_km")):
            self.editor.bind(obj_id)
            text = self.editor._widgets[field].text()
            depth = self.doc.undo.count()
            self.editor._commit_numeric(field, _spec(self.doc, obj_id, field), text)
            self.assertEqual(self.doc.undo.count(), depth, obj_id)
        self.assertFalse(self.doc.is_dirty)


class VariablesUndo(unittest.TestCase):
    def test_undo_and_redo_move_the_bound_values_with_the_variable(self):
        from psa.gui.commands import SetParameters

        doc = s1_document()
        p = doc.params
        doc.undo.push(SetParameters(doc, p.script_path, [("A", "0")], p.bindings))
        self.assertAlmostEqual(doc.network.get("LDb").p_mw, 0.117)
        doc.undo.undo()
        self.assertAlmostEqual(doc.network.get("LDb").p_mw, 0.037)
        self.assertAlmostEqual(doc.network.get("LIbc").length_km, 0.297)
        doc.undo.redo()
        self.assertAlmostEqual(doc.network.get("LDb").p_mw, 0.117)


class ConnectionDrag(unittest.TestCase):
    """Dragging a terminal onto a bus, with real mouse events on a view."""

    def test_the_mouse_is_released_after_connecting(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QGraphicsView

        from psa.gui.commands import Connect
        from psa.gui.items import TerminalItem

        doc = s1_document()
        scene = SchematicScene(doc)
        doc.undo.push(Connect(doc, "LDb", "bus", ""))
        view = QGraphicsView(scene)
        view.resize(1600, 1000)
        view.show()
        view.fitInView(scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
        _app.processEvents()

        terminal = next(c for c in scene.item_for("LDb").childItems()
                        if isinstance(c, TerminalItem))
        viewport = view.viewport()
        start = view.mapFromScene(terminal.scenePos())
        target = view.mapFromScene(scene.item_for("bus_b").scenePos())
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(viewport, target)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target)
        _app.processEvents()
        self.assertEqual(doc.network.get("LDb").bus, "bus_b")
        self.assertIsNone(scene.mouseGrabberItem())

        # Before the fix this click restarted the drag and created a stray bus.
        n_buses = len(doc.network.buses)
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, 5))
        _app.processEvents()
        self.assertIsNone(scene._connecting)
        self.assertEqual(len(doc.network.buses), n_buses)
        view.close()


class MainWindowSmoke(unittest.TestCase):
    """The window builds, runs the studies and annotates the diagram."""

    def test_end_to_end(self):
        from psa.gui.mainwindow import MainWindow

        doc = NetworkDocument.open(EXAMPLE) if EXAMPLE.is_file() else s1_document()
        win = MainWindow(doc)
        win.run_all_studies()

        titles = [win.results.tabText(i) for i in range(win.results.count())]
        self.assertIn("Log", titles)
        self.assertTrue(any("S1a" in t for t in titles))
        self.assertTrue(any("S1b" in t for t in titles))
        self.assertTrue(any("S1c" in t for t in titles))
        self.assertTrue(doc.results_valid)
        self.assertGreater(len(win.scene._overlay), 0,
                           "the result overlay should annotate the diagram")

        win.properties.bind("TI1")
        self.assertIn("x_pct", win.properties._widgets)
        doc.undo.setClean()
        win.close()

    def test_settings_stay_out_of_the_user_profile(self):
        """Closing a window writes to the temporary INI file, never the registry."""
        from psa.gui.mainwindow import MainWindow, _settings

        settings_file = Path(_settings().fileName()).resolve()
        self.assertTrue(settings_file.is_relative_to(Path(_SETTINGS_DIR).resolve()),
                        settings_file)
        win = MainWindow(blank_document())
        win.close()
        self.assertIsNotNone(_settings().value("geometry"))

    def test_running_with_an_invalid_network_reports_and_bails_out(self):
        """An incomplete network must produce a message, not a traceback."""
        from unittest import mock

        from psa.gui.mainwindow import MainWindow

        doc = blank_document()
        win = MainWindow(doc)
        win.scene.place_component("bus", QPointF(0, 0))
        # No base reference bus is set yet, so the study cannot run. Stub the
        # modal out: an unanswered dialog would block a headless run.
        with mock.patch("psa.gui.mainwindow.QMessageBox.warning") as warn:
            win._run(["s1b"], {})
        self.assertTrue(warn.called, "the user must be told why nothing ran")
        self.assertFalse(doc.results_valid)
        self.assertIn("base", warn.call_args[0][2].lower())
        doc.undo.setClean()          # so closing does not prompt to save
        win.close()

    def test_one_failing_study_does_not_hide_the_others(self):
        from unittest import mock

        from psa.gui.mainwindow import MainWindow

        doc = s1_document()
        doc.project.studies["s1a"] = {"bus_i": "bus_a", "bus_j": "bus_a"}
        win = MainWindow(doc)
        with mock.patch.object(win, "_error") as error:
            win.run_all_studies()
        self.assertFalse(error.called)
        titles = [win.results.tabText(i) for i in range(win.results.count())]
        self.assertTrue(any("S1b" in t for t in titles))
        self.assertTrue(any("S1c" in t for t in titles))
        self.assertTrue(doc.results_valid)
        doc.undo.setClean()
        win.close()

    def test_choosing_study_options_marks_the_project_modified(self):
        from unittest import mock

        from psa.gui.mainwindow import MainWindow

        doc = s1_document()
        win = MainWindow(doc)
        with mock.patch("psa.gui.mainwindow.StudyOptionsDialog") as dialog_cls, \
                mock.patch.object(win, "_run"):
            dialog = dialog_cls.return_value
            dialog.exec.return_value = dialog_cls.DialogCode.Accepted
            dialog.options.return_value = {"bus": "bus_b", "far_bus": "bus_e"}
            win.run_study("s1b")
        self.assertEqual(doc.project.studies["s1b"],
                         {"bus": "bus_b", "far_bus": "bus_e"})
        self.assertTrue(doc.is_dirty)
        doc.undo.undo()
        self.assertNotIn("s1b", doc.project.studies)
        doc.undo.setClean()
        win.close()

    def test_bundled_example_opens_as_an_untitled_copy(self):
        from psa.gui.mainwindow import MainWindow

        win = MainWindow(blank_document())
        win.load_example()
        self.assertIsNone(win.document.path, "Ctrl+S would overwrite the example")
        self.assertIn("bus_e", win.document.network.buses)
        win.document.undo.setClean()
        win.close()

    def test_a_linked_script_only_runs_after_the_user_agrees(self):
        import tempfile
        from unittest import mock

        from PySide6.QtWidgets import QMessageBox

        from psa.gui.mainwindow import MainWindow

        with tempfile.TemporaryDirectory() as d:
            sentinel = Path(d) / "ran.txt"
            script = Path(d) / "vars.py"
            script.write_text(f"from pathlib import Path\n"
                              f"Path({str(sentinel)!r}).write_text('ran')\n"
                              f"A = 0\n", encoding="utf-8")
            doc = s1_document()
            doc.params.script_path = str(script)
            win = MainWindow(doc)
            with mock.patch("psa.gui.mainwindow.QMessageBox.question",
                            return_value=QMessageBox.StandardButton.No) as ask:
                win.offer_linked_script()
            self.assertTrue(ask.called)
            self.assertFalse(sentinel.exists())
            with mock.patch("psa.gui.mainwindow.QMessageBox.question",
                            return_value=QMessageBox.StandardButton.Yes):
                win.offer_linked_script()
            self.assertTrue(sentinel.exists())
            doc.undo.setClean()
            win.close()


class StartupArguments(unittest.TestCase):
    def test_unreadable_project_gives_a_blank_document_and_the_reason(self):
        from psa.gui.app import initial_document

        doc, error = initial_document(["run_gui.py", "no_such_project.psn"])
        self.assertEqual(len(doc.network.buses), 0)
        self.assertIn("no_such_project.psn", error)

    def test_example_flag_opens_an_untitled_copy(self):
        from psa.gui.app import initial_document

        doc, error = initial_document(["run_gui.py", "--example"])
        self.assertIsNone(error)
        self.assertIsNone(doc.path)
        self.assertEqual(len(doc.network.buses), 5)


def _spec(doc, obj_id: str, field: str):
    from psa.core.components import field_specs

    obj = doc.network.get(obj_id)
    return next(s for s in field_specs(obj) if s.name == field)


if __name__ == "__main__":
    unittest.main()
