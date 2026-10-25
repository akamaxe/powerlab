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
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import psa.gui  # noqa: E402,F401 - must come before PySide6, see its docstring
from PySide6.QtCore import QPointF, QSettings, Qt   # noqa: E402
from PySide6.QtWidgets import QApplication          # noqa: E402

from psa.core.analysis import equivalent_impedance     # noqa: E402
from psa.core.cases import Case                        # noqa: E402
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
        doc.project.cases = [
            Case("s1a", "s1a", options={"bus_i": "bus_a", "bus_j": "bus_a"}),
            Case("s1b", "s1b"), Case("s1c", "s1c")]
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
        chosen = Case("s1b", "s1b", options={"bus": "bus_b", "far_bus": "bus_e"})
        with mock.patch("psa.gui.mainwindow.CaseDialog") as dialog_cls, \
                mock.patch.object(win, "_run_cases") as run:
            dialog = dialog_cls.return_value
            dialog.exec.return_value = dialog_cls.DialogCode.Accepted
            dialog.case.return_value = chosen
            win.run_study("s1b")
        self.assertEqual(doc.case("s1b").options, {"bus": "bus_b", "far_bus": "bus_e"})
        self.assertEqual(run.call_args[0][0], [chosen])
        self.assertTrue(doc.is_dirty)
        doc.undo.undo()
        self.assertIsNone(doc.case("s1b"))
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


class StudyCases(unittest.TestCase):
    """Cases: the generic dialog, the panel actions and the network variants."""

    def test_dialog_lists_the_shunts_on_whichever_buses_are_the_ports(self):
        from psa.core.study import STUDY_REGISTRY
        from psa.gui.dialogs import CaseDialog

        doc = s1_document()
        dialog = CaseDialog(doc, STUDY_REGISTRY["s1a"], Case("s1a", "s1a"))
        checks = dialog.choice_checks["include_at_ports"]
        self.assertIn("LDe", checks)
        self.assertTrue(checks["LDe"].isEnabled())
        self.assertFalse(checks["SRC"].isEnabled(), "an ideal source at a port")
        port2 = dialog.widgets["bus_j"]
        port2.setCurrentIndex(port2.findData("bus_d"))
        self.assertNotIn("LDe", dialog.choice_checks["include_at_ports"])
        case = dialog.case()
        self.assertEqual(case.options["bus_i"], "bus_a")
        self.assertEqual(case.options["bus_j"], "bus_d")
        dialog.deleteLater()

    def test_dialog_validates_overrides_and_records_exclusions(self):
        from psa.core.errors import ExpressionError
        from psa.core.study import STUDY_REGISTRY
        from psa.gui.dialogs import CaseDialog

        doc = s1_document()
        dialog = CaseDialog(doc, STUDY_REGISTRY["s1c"], Case("half", "s1c"))
        dialog._add_override_row("LDe.p_watts", "1")
        with self.assertRaises(ExpressionError):
            dialog.case()
        dialog.override_table.item(0, 0).setText("LDe.p_mw")
        dialog.override_table.item(0, 1).setText("0.5*value")
        for i in range(dialog.exclude_list.count()):
            item = dialog.exclude_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == "LDb":
                item.setCheckState(Qt.CheckState.Checked)
        case = dialog.case()
        self.assertEqual(case.overrides, {"LDe.p_mw": "0.5*value"})
        self.assertEqual(case.exclude, ["LDb"])
        dialog.deleteLater()

    def test_panel_actions_are_undoable(self):
        from unittest import mock

        from psa.gui.mainwindow import MainWindow

        doc = s1_document()
        win = MainWindow(doc)
        with mock.patch("psa.gui.mainwindow.CaseDialog") as dialog_cls:
            dialog = dialog_cls.return_value
            dialog.exec.return_value = dialog_cls.DialogCode.Accepted
            dialog.case.return_value = Case("s1c", "s1c", title="Base",
                                            options={"bus": "bus_e"})
            win.new_case("s1c")
        self.assertEqual([c.id for c in doc.project.cases], ["s1c"])
        self.assertEqual(win.cases_panel.list.count(), 1)
        win.duplicate_case("s1c")
        self.assertEqual([c.id for c in doc.project.cases], ["s1c", "s1c_2"])
        self.assertEqual(doc.case("s1c_2").title, "Base (copy)")
        win.delete_case("s1c")
        self.assertEqual([c.id for c in doc.project.cases], ["s1c_2"])
        doc.undo.undo()
        doc.undo.undo()
        self.assertEqual([c.id for c in doc.project.cases], ["s1c"])
        doc.undo.setClean()
        win.close()

    def test_a_variant_case_leaves_the_drawn_network_alone(self):
        from psa.gui.mainwindow import MainWindow

        doc = s1_document()
        p_before = doc.network.get("LDe").p_mw
        doc.project.cases = [
            Case("base", "s1c", options={"bus": "bus_e"}),
            Case("half", "s1c", title="LDe at 50 %", options={"bus": "bus_e"},
                 overrides={"LDe.p_mw": "0.5*value"})]
        win = MainWindow(doc)
        win.run_all_studies()
        base, half = win.results.result_at(0), win.results.result_at(1)
        u_base = base.buses["bus_e"]["u"].value
        u_half = half.buses["bus_e"]["u"].value
        self.assertGreater(abs(u_half), abs(u_base), "less load, less voltage drop")
        self.assertEqual(doc.network.get("LDe").p_mw, p_before)
        self.assertTrue(win.results.tabText(1).startswith("LDe at 50 %"))
        doc.undo.setClean()
        win.close()

    def test_annotations_follow_their_element(self):
        from psa.gui.commands import MoveItems
        from psa.gui.mainwindow import MainWindow

        doc = s1_document()
        win = MainWindow(doc)
        win.run_all_studies()
        badges = [b for b in win.scene._overlay if b.target == "bus_e"]
        self.assertEqual(len(badges), 1)
        x0 = badges[0].pos().x()
        old = dict(doc.project.layout["bus_e"])
        new = dict(old, x=old["x"] + 100)
        doc.undo.push(MoveItems(doc, {"bus_e": (old, new)}))
        self.assertAlmostEqual(badges[0].pos().x(), x0 + 100)
        self.assertTrue(doc.results_valid, "moving a symbol changes no number")
        doc.undo.setClean()
        win.close()

    def test_switching_tabs_keeps_the_last_annotations(self):
        from psa.gui.mainwindow import MainWindow

        doc = s1_document()
        win = MainWindow(doc)
        win.run_all_studies()
        n = len(win.scene._overlay)
        self.assertGreater(n, 0)
        win.results.setCurrentIndex(0)          # the per-unit model: no badges
        self.assertEqual(len(win.scene._overlay), n)
        doc.undo.setClean()
        win.close()


class GridStudies(unittest.TestCase):
    """The S2 example in the window: fault-level fields and studies at a bus."""

    S2 = ROOT / "examples" / "s2_grid.psn"

    def test_external_grid_shows_only_the_fields_of_its_mode(self):
        doc = NetworkDocument.open(self.S2, as_copy=True)
        from psa.gui.panels import PropertyEditor

        editor = PropertyEditor(doc)
        editor.bind("GRID")
        self.assertIn("ik_ka", editor._widgets)
        self.assertNotIn("z_th_ohm", editor._widgets)
        doc.undo.push(SetField(doc, "GRID", "z_mode", "z_ohm"))
        editor.bind("GRID")
        self.assertIn("z_th_ohm", editor._widgets)
        self.assertNotIn("ik_ka", editor._widgets)
        editor.deleteLater()

    def test_running_the_s2_cases_annotates_the_faulted_bus(self):
        from psa.gui.mainwindow import MainWindow

        doc = NetworkDocument.open(self.S2, as_copy=True)
        win = MainWindow(doc)
        win.run_all_studies()
        titles = [win.results.tabText(i) for i in range(win.results.count())]
        self.assertTrue(any(t.startswith("S2a") for t in titles), titles)
        self.assertTrue(any(t.startswith("S2c") for t in titles), titles)
        win.results.setCurrentIndex(2)                  # S2c, the short circuit
        badges = {b.target: b for b in win.scene._overlay}
        self.assertIn("b7", badges)
        self.assertTrue(badges["b7"].lines[0].startswith("I_f 0.7584 kA"))
        from PySide6.QtWidgets import QLabel

        head = win.results.widget(2).findChild(QLabel, "headline")
        self.assertIn("kA &lt; -25.3911 deg", head.text(),
                      "the '<' of a phasor must not be read as an HTML tag")
        doc.undo.setClean()
        win.close()

    def test_a_study_from_the_bus_context_menu_leaves_the_cases_alone(self):
        from psa.gui.mainwindow import MainWindow

        doc = NetworkDocument.open(self.S2, as_copy=True)
        cases_before = [c.id for c in doc.project.cases]
        win = MainWindow(doc)
        win.run_study_at_bus("thevenin", "b4")
        self.assertTrue(win.results.tabText(0).startswith("Thevenin equivalent at bus 4"))
        self.assertEqual([c.id for c in doc.project.cases], cases_before)
        self.assertFalse(doc.is_dirty)
        win.close()


class EditorErgonomics(unittest.TestCase):
    """Property editing in place, multi-selection, context menus, Esc, labels."""

    def setUp(self):
        self.doc = s1_document()

    def editor(self):
        from psa.gui.panels import PropertyEditor
        return PropertyEditor(self.doc)

    def test_committing_a_value_keeps_the_same_widgets(self):
        """Rebuilding the form on every edit is what used to steal the focus."""
        ed = self.editor()
        ed.bind("TI1")
        x_pct, r_pct = ed._widgets["x_pct"], ed._widgets["r_pct"]
        x_pct.setText("8")
        x_pct.committed.emit("8")
        self.assertEqual(self.doc.network.get("TI1").x_pct, 8.0)
        self.assertIs(ed._widgets["x_pct"], x_pct)
        self.assertIs(ed._widgets["r_pct"], r_pct)
        ed.deleteLater()

    def test_values_below_the_minimum_are_refused(self):
        ed = self.editor()
        ed.bind("LIcd")
        before = self.doc.undo.count()
        length = ed._widgets["length_km"]
        length.setText("-1")
        length.committed.emit("-1")
        self.assertEqual(self.doc.undo.count(), before)
        self.assertEqual(length.property("invalid"), "true")
        self.assertIn("at least 0", length.toolTip())
        ed.deleteLater()

    def test_fields_are_grouped(self):
        from PySide6.QtWidgets import QGroupBox

        ed = self.editor()
        ed.bind("LIcd")
        titles = [g.title() for g in ed._form_host.findChildren(QGroupBox)]
        self.assertEqual(titles, ["General", "Connection", "Parameters", "Zero sequence"])
        ed.deleteLater()

    def test_editing_several_lines_at_once(self):
        ed = self.editor()
        ed.bind_many(["LIbc", "LIcd"])
        self.assertNotIn("bus_from", ed._widgets, "connectivity is per element")
        self.assertNotIn("name", ed._widgets)
        n_parallel = ed._widgets["n_parallel"]
        self.assertEqual(n_parallel.text(), "")
        self.assertEqual(n_parallel.placeholderText(), ed.SEVERAL)
        before = self.doc.undo.count()
        r = ed._widgets["r_ohm_per_km"]
        r.setText("0.2")
        r.committed.emit("0.2")
        net = self.doc.network
        self.assertEqual((net.get("LIbc").r_ohm_per_km, net.get("LIcd").r_ohm_per_km),
                         (0.2, 0.2))
        self.assertEqual(self.doc.undo.count(), before + 1, "one undo step")
        self.doc.undo.undo()
        self.assertEqual(net.get("LIbc").r_ohm_per_km, 0.16)
        self.assertEqual(net.get("LIcd").r_ohm_per_km, 0.16)
        ed.deleteLater()

    def test_mixed_types_cannot_be_edited_together(self):
        ed = self.editor()
        ed.bind_many(["LIbc", "TI1"])
        self.assertEqual(ed._widgets, {})
        self.assertIn("different types", ed._title.text())
        ed.deleteLater()

    def test_context_menu_and_its_actions(self):
        from psa.gui.mainwindow import MainWindow

        win = MainWindow(self.doc)
        item = win.scene.item_for("LDb")
        menu = win.scene.build_context_menu(item)      # keep it alive while read
        texts = [a.text() for a in menu.actions() if a.text()]
        for wanted in ("Properties", "Cut", "Copy", "Duplicate", "Rotate 90 deg",
                       "In service", "Delete"):
            self.assertIn(wanted, texts)
        bus_menu = win.scene.build_context_menu(win.scene.item_for("bus_b"))
        bus_texts = [a.text() for a in bus_menu.actions()]
        self.assertIn("Thevenin equivalent here", bus_texts)
        self.assertIn("Switch vertical / horizontal", bus_texts)

        item.setSelected(True)
        win.context_action("toggle_service", "LDb")
        self.assertFalse(self.doc.network.get("LDb").in_service)
        win.context_action("rotate", "LDb")
        self.assertEqual(self.doc.project.layout["LDb"]["rotation"], 90.0)
        self.doc.undo.undo()
        self.doc.undo.undo()
        self.assertTrue(self.doc.network.get("LDb").in_service)
        self.doc.undo.setClean()
        win.close()

    def test_escape_drops_the_palette_tool(self):
        from PySide6.QtTest import QTest

        from psa.gui.scene import SchematicView

        view = SchematicView(SchematicScene(self.doc))
        dropped = []
        view.toolConsumed.connect(lambda: dropped.append(True))
        view.arm_tool("load")
        QTest.keyClick(view, Qt.Key.Key_Escape)
        self.assertIsNone(view._pending_tool)
        self.assertEqual(dropped, [True])
        view.deleteLater()

    def test_labels_stay_upright_on_rotated_symbols(self):
        scene = SchematicScene(self.doc)
        item = scene.item_for("LIcd")
        item.setRotation(90)
        t = item.label.sceneTransform()
        self.assertAlmostEqual(t.m12(), 0.0, places=9)
        self.assertAlmostEqual(t.m11(), 1.0, places=9)
        # The name now sits beside the vertical branch, not across it.
        name = item.label.sceneBoundingRect()
        self.assertLess(name.right(), item.scenePos().x())

    def test_dragging_a_connection_marks_the_real_target(self):
        from PySide6.QtCore import QPointF as P
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent
        from PySide6.QtCore import QEvent

        scene = SchematicScene(self.doc)
        load = scene.item_for("LDb")
        scene.begin_connection(load.terminals[0])
        target = scene.item_for("bus_e")
        event = QGraphicsSceneMouseEvent(QEvent.Type.GraphicsSceneMouseMove)
        event.setScenePos(target.scenePos())
        scene.mouseMoveEvent(event)
        self.assertEqual(target.highlight, "target")
        self.assertEqual(scene.item_for("bus_b").highlight, "candidate")
        scene._cancel_connection()
        self.assertEqual(target.highlight, "")


class Clipboard(unittest.TestCase):
    """Copy, cut, paste and duplicate."""

    def test_duplicate_keeps_internal_connections_only(self):
        from psa.gui.clipboard import copy_payload, paste_payload

        doc = s1_document()
        net = doc.network
        before = doc.undo.count()
        new_ids = paste_payload(doc, copy_payload(doc, ["bus_d", "TI2", "bus_e", "LDe"]))
        self.assertEqual(doc.undo.count(), before + 1, "one undo step")
        self.assertEqual(len(new_ids), 4)
        new = {net.get(i).TYPE: net.get(i) for i in new_ids if i in net.components}
        buses = [i for i in new_ids if i in net.buses]
        self.assertEqual(sorted({new["transformer"].bus_hv, new["transformer"].bus_lv}),
                         sorted(buses))
        self.assertIn(new["load"].bus, buses)
        self.assertEqual(doc.params.expression_for(new["load"].id, "p_mw"),
                         "(243 + A)/1000", "bindings follow the copy")
        self.assertEqual(doc.project.layout[new["load"].id]["x"],
                         doc.project.layout["LDe"]["x"] + 40.0)
        doc.undo.undo()
        for obj_id in new_ids:
            self.assertNotIn(obj_id, net)

    def test_a_copy_without_its_bus_is_left_unconnected(self):
        from psa.gui.clipboard import copy_payload, paste_payload

        doc = s1_document()
        (new_id,) = paste_payload(doc, copy_payload(doc, ["LDb"]))
        self.assertEqual(doc.network.get(new_id).bus, "")
        self.assertEqual(doc.network.get(new_id).p_mw, doc.network.get("LDb").p_mw)

    def test_window_copy_paste_and_cut(self):
        from psa.gui.mainwindow import MainWindow

        doc = s1_document()
        win = MainWindow(doc)
        n0 = len(list(doc.network))
        win._select(["LDb"])
        win.copy_selection()
        win.paste()
        win.paste()
        self.assertEqual(len(list(doc.network)), n0 + 2)
        pasted = win.selected_ids()
        self.assertEqual(len(pasted), 1)
        self.assertEqual(doc.project.layout[pasted[0]]["y"],
                         doc.project.layout["LDb"]["y"] + 80.0, "second paste cascades")
        win.cut_selection()
        self.assertEqual(len(list(doc.network)), n0 + 1)
        doc.undo.setClean()
        win.close()

    def test_swapping_documents_keeps_shortcuts_unambiguous(self):
        from psa.gui.mainwindow import MainWindow

        win = MainWindow(s1_document())
        n_actions = len(win.view.actions())
        win.new_project()
        self.assertEqual(len(win.view.actions()), n_actions)
        win.close()


def _mouse(kind: str, pos, button=Qt.MouseButton.LeftButton):
    """A scene mouse event of ``kind`` (press, move, release, double) at ``pos``."""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent

    types = {"press": QEvent.Type.GraphicsSceneMousePress,
             "move": QEvent.Type.GraphicsSceneMouseMove,
             "release": QEvent.Type.GraphicsSceneMouseRelease,
             "double": QEvent.Type.GraphicsSceneMouseDoubleClick}
    event = QGraphicsSceneMouseEvent(types[kind])
    event.setScenePos(pos)
    event.setButton(button)
    return event


class WiresAndBusbars(unittest.TestCase):
    def test_orthogonal_route_through_bends(self):
        from psa.gui.items import orthogonal_route

        pts = orthogonal_route(QPointF(0, 0), True, [QPointF(50, 30)], QPointF(100, 30))
        self.assertEqual([(p.x(), p.y()) for p in pts],
                         [(0, 0), (50, 0), (50, 30), (100, 30)])
        pts = orthogonal_route(QPointF(0, 0), False, [], QPointF(40, 60))
        self.assertEqual([(p.x(), p.y()) for p in pts], [(0, 0), (0, 60), (40, 60)])

    def test_a_rotated_terminal_points_the_way_the_symbol_does(self):
        scene = SchematicScene(s1_document())
        line = scene.item_for("LIcd")
        self.assertAlmostEqual(line.terminal_direction(0).x(), -1.0)
        line.setRotation(90)
        d = line.terminal_direction(0)
        self.assertAlmostEqual(d.x(), 0.0, places=9)
        self.assertAlmostEqual(d.y(), -1.0, places=9)

    def test_dragging_a_bus_handle_resizes_it_with_undo(self):
        doc = s1_document()
        scene = SchematicScene(doc)
        bus = scene.item_for("bus_b")
        bus.setSelected(True)
        handle = bus.handles[1]                      # the lower end of a vertical bar
        self.assertTrue(handle.isVisible())
        top_before = bus.mapToScene(bus.endpoints()[0]).y()
        start = handle.scenePos()
        handle.mousePressEvent(_mouse("press", start))
        handle.mouseMoveEvent(_mouse("move", QPointF(start.x(), start.y() + 43)))
        handle.mouseReleaseEvent(_mouse("release", QPointF(start.x(), start.y() + 43)))
        self.assertEqual(bus.length, 100.0, "60 + 43 rounded to 20-unit steps")
        self.assertAlmostEqual(bus.mapToScene(bus.endpoints()[0]).y(), top_before,
                               msg="the other end stays put")
        self.assertEqual(doc.project.layout["bus_b"]["length"], 100.0)
        doc.undo.undo()
        self.assertEqual(bus.length, 60.0)
        self.assertEqual(doc.project.layout["bus_b"]["length"], 60)

    def test_wire_bends_are_added_moved_and_removed_with_undo(self):
        doc = s1_document()
        scene = SchematicScene(doc)
        wire = scene._wires["LDb:0"]
        self.assertEqual(wire.waypoints, [])
        click = wire.points()[0] + QPointF(0, -3)
        wire.mouseDoubleClickEvent(_mouse("double", click))
        self.assertEqual(len(doc.project.wires["LDb:0"]), 1)
        for a, b in zip(wire.points(), wire.points()[1:]):
            self.assertTrue(abs(a.x() - b.x()) < 1e-9 or abs(a.y() - b.y()) < 1e-9,
                            "every segment is horizontal or vertical")

        wire.setSelected(True)
        self.assertEqual(len(wire.handles), 1)
        grip = wire.handles[0]
        target = grip.scenePos() + QPointF(40, 0)
        grip.mousePressEvent(_mouse("press", grip.scenePos()))
        grip.mouseMoveEvent(_mouse("move", target))
        grip.mouseReleaseEvent(_mouse("release", target))
        self.assertEqual(doc.project.wires["LDb:0"][0][0], scene.snap(target).x())

        doc.undo.undo()                               # the move
        doc.undo.undo()                               # the bend
        self.assertNotIn("LDb:0", doc.project.wires)
        self.assertEqual(wire.waypoints, [])
        doc.undo.redo()
        self.assertEqual(len(wire.waypoints), 1)

    def test_bends_survive_saving(self):
        import tempfile

        doc = s1_document()
        scene = SchematicScene(doc)
        wire = scene._wires["LDe:0"]
        wire.mouseDoubleClickEvent(_mouse("double", wire.points()[0]))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bends.psn"
            doc.save_to(path)
            again = NetworkDocument.open(path)
        self.assertEqual(again.project.wires["LDe:0"], doc.project.wires["LDe:0"])
        scene2 = SchematicScene(again)
        self.assertEqual(len(scene2._wires["LDe:0"].waypoints), 1)


def _meshed_document(rows: int = 5, cols: int = 8) -> NetworkDocument:
    """A grid-shaped network of IEEE-39 size: 40 buses, ~70 lines, 20 loads."""
    from psa.core.components import Bus, ExternalGrid, Line, Load
    from psa.core.network import Network

    net = Network(s_base_mva=100.0, base_ref_bus="n0_0", base_ref_u_kv=220.0)
    layout = {}
    for r in range(rows):
        for c in range(cols):
            bus_id = f"n{r}_{c}"
            net.add(Bus(bus_id, name=bus_id))
            layout[bus_id] = {"x": c * 200, "y": r * 200, "orientation": "V", "length": 60}
    k = 0
    for r in range(rows):
        for c in range(cols):
            for dr, dc, rot in ((0, 1, 0), (1, 0, 90)):
                if r + dr < rows and c + dc < cols:
                    k += 1
                    line_id = f"L{k}"
                    net.add(Line(line_id, bus_from=f"n{r}_{c}", bus_to=f"n{r + dr}_{c + dc}",
                                 r_ohm_per_km=0.05, x_ohm_per_km=0.5, b_s_per_km=3.6e-6,
                                 length_km=50.0))
                    layout[line_id] = {"x": c * 200 + dc * 100, "y": r * 200 + dr * 100,
                                       "rotation": rot, "mirrored": False}
            if (r + c) % 2:
                load_id = f"LD{r}_{c}"
                net.add(Load(load_id, bus=f"n{r}_{c}", p_mw=50.0, cos_phi=0.95))
                layout[load_id] = {"x": c * 200 + 40, "y": r * 200 + 110,
                                   "rotation": 0, "mirrored": False}
    net.add(ExternalGrid("GRID", bus="n0_0", z_mode="sk", sk_mva=5000.0, cos_phi=0.1))
    layout["GRID"] = {"x": -90, "y": 0, "rotation": 0, "mirrored": False}
    proj = Project(network=net, layout=layout)
    return NetworkDocument(proj)


class Scale(unittest.TestCase):
    """An IEEE-39-size diagram stays responsive (generous limits, any machine)."""

    def test_building_moving_adding_and_removing(self):
        import time

        doc = _meshed_document()
        self.assertGreaterEqual(len(list(doc.network)), 120)     # IEEE 39: ~114
        t0 = time.perf_counter()
        scene = SchematicScene(doc)
        build = time.perf_counter() - t0

        bus = scene.item_for("n2_3")
        t0 = time.perf_counter()
        for dx in range(10):
            bus.setPos(bus.x() + 10, bus.y())
        move = (time.perf_counter() - t0) / 10

        t0 = time.perf_counter()
        new_id = scene.place_component("load", QPointF(600, 470))
        add = time.perf_counter() - t0
        t0 = time.perf_counter()
        delete_objects(doc, [new_id])
        remove = time.perf_counter() - t0

        self.assertLess(build, 3.0, f"building took {build:.2f} s")
        self.assertLess(move, 0.05, f"a move step took {move * 1000:.1f} ms")
        self.assertLess(add, 0.25, f"adding took {add * 1000:.0f} ms")
        self.assertLess(remove, 0.25, f"removing took {remove * 1000:.0f} ms")
        self.assertEqual(len(scene._items), len(list(doc.network)))

    def test_the_meshed_network_solves(self):
        from psa.core.cases import Case
        from psa.core.study import run_case

        res = run_case(_meshed_document().network,
                       Case("sc", "short_circuit", options={"bus": "n4_7"}))
        self.assertGreater(abs(res.scalars["I_f"].value), 0.0)


class DraggableLabels(unittest.TestCase):
    def test_dragging_a_name_moves_it_and_survives_rotation(self):
        doc = s1_document()
        scene = SchematicScene(doc)
        item = scene.item_for("LIcd")
        auto = item.label_offset()
        start = item.label.scenePos()
        item.label.mousePressEvent(_mouse("press", start))
        item.label.mouseMoveEvent(_mouse("move", start + QPointF(15, 40)))
        item.label.mouseReleaseEvent(_mouse("release", start + QPointF(15, 40)))
        self.assertEqual(doc.project.layout["LIcd"]["label_offset"],
                         [auto.x() + 15, auto.y() + 40])
        item.setRotation(90)                    # automatic placement must not win
        self.assertEqual(item.label_offset(), QPointF(auto.x() + 15, auto.y() + 40))
        doc.undo.undo()
        self.assertNotIn("label_offset", doc.project.layout["LIcd"])
        self.assertEqual(item.label_offset(), auto)


class SequenceStudiesInTheWindow(unittest.TestCase):
    GI = ROOT / "examples" / "grid_industry.psn"

    def test_all_s2_and_s3_cases_run_and_s3e_annotates_bus_7(self):
        from psa.gui.mainwindow import MainWindow

        doc = NetworkDocument.open(self.GI, as_copy=True)
        win = MainWindow(doc)
        with unittest.mock.patch.object(win, "_error") as error:
            win.run_all_studies()
        self.assertFalse(error.called)
        titles = [win.results.tabText(i) for i in range(win.results.count() - 1)]
        self.assertEqual([t[:3] for t in titles],
                         ["S2a", "S2b", "S2c", "S2d", "S3a", "S3b", "S3c", "S3d", "S3e"])
        warnings = [w for i in range(len(titles)) for w in win.results.result_at(i).warnings]
        self.assertEqual(warnings, [])
        win.results.setCurrentIndex(titles.index(next(t for t in titles if t.startswith("S3e"))))
        badge = next(b for b in win.scene._overlay if b.target == "b7")
        self.assertEqual(badge.lines[0], "U 0.9302 / 0.9287 / 0.9288 pu")
        win.close()

    def test_case_dialog_hides_the_ground_impedance_unless_llg(self):
        from psa.core.study import STUDY_REGISTRY
        from psa.gui.dialogs import CaseDialog

        doc = NetworkDocument.open(self.GI, as_copy=True)
        dialog = CaseDialog(doc, STUDY_REGISTRY["short_circuit"], doc.case("S3c"))
        dialog.show()
        self.assertFalse(dialog.widgets["zg_ohm"].isVisible())
        kind = dialog.widgets["kind"]
        kind.setCurrentIndex(kind.findData("llg"))
        self.assertTrue(dialog.widgets["zg_ohm"].isVisible())
        dialog.close()

    def test_unbalanced_load_dialog_offers_only_loads(self):
        from psa.core.study import STUDY_REGISTRY
        from psa.gui.dialogs import CaseDialog

        doc = NetworkDocument.open(self.GI, as_copy=True)
        dialog = CaseDialog(doc, STUDY_REGISTRY["unbalanced_load"], doc.case("S3e"))
        combo = dialog.widgets["element"]
        items = [combo.itemData(i) for i in range(combo.count())]
        self.assertEqual(sorted(items), ["LD4", "LD6", "LD8", "LDb", "LDe"])
        self.assertEqual(combo.currentData(), "LDe")
        case = dialog.case()
        self.assertEqual(case.options["factor_a"], 0.5)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()

