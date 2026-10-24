"""The main window: menus, docks and the run actions."""

from __future__ import annotations

import copy
import traceback
from pathlib import Path

import shiboken6
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..core.cases import Case, unique_case_id
from ..core.errors import PsaError
from ..core.examples import build_s1, s1_parameter_bindings
from ..core.params import Parameters
from ..core.results import Result
from ..core.serialize import Project
from ..core.study import STUDY_REGISTRY, default_cases, run_case, studies_by_group
from . import style as st
from .commands import SetCases, SetNetworkSettings, delete_objects
from .dialogs import BaseSettingsDialog, CaseDialog
from .document import NetworkDocument, blank_document
from .panels import (
    CasesPanel,
    PaletteList,
    PropertyEditor,
    ResultsPanel,
    VariablesPanel,
)
from .scene import SchematicScene, SchematicView

FILE_FILTER = "PowerLab project (*.psn);;All files (*)"
ORG, APP = "PowerLab", "PowerLab"


def _settings() -> QSettings:
    """Per-user settings in the default format (the registry on Windows).

    ``QSettings(ORG, APP)`` would always use the native format; going through
    ``defaultFormat()`` lets the tests redirect everything to a temporary INI
    file instead of the real user settings.
    """
    return QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, ORG, APP)


class MainWindow(QMainWindow):
    def __init__(self, document: NetworkDocument | None = None):
        super().__init__()
        self.document = document or blank_document()
        self._annotated: Result | None = None
        self.setWindowTitle("PowerLab")
        self.resize(1500, 950)

        self.scene = SchematicScene(self.document)
        self.view = SchematicView(self.scene)
        self.setCentralWidget(self._wrap_canvas())

        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()

        self.status = self.statusBar()
        self._status_hint = QLabel("")
        self.status.addPermanentWidget(self._status_hint)

        self.scene.selectionInfoChanged.connect(self.properties.bind)
        self.scene.requestProperties.connect(self._focus_properties)
        self.scene.statusMessage.connect(self.status.showMessage)
        self.scene.selectionChanged.connect(self._on_selection_changed)
        self.scene.studyAtBusRequested.connect(self.run_study_at_bus)
        self.scene.contextActionRequested.connect(self.context_action)
        self.view.toolConsumed.connect(self.palette.clear_selection)
        self.palette.toolPicked.connect(self.view.arm_tool)
        self.variables.loadScriptRequested.connect(self.link_variable_script)
        self.variables.reloadScriptRequested.connect(self.reload_variable_script)
        self.document.dirtyChanged.connect(self._update_title)
        self.document.resultsInvalidated.connect(self._on_results_stale)
        self.document.structureChanged.connect(self._update_actions)
        self.results.resultActivated.connect(self._on_result_activated)
        self.cases_panel.newRequested.connect(self.new_case)
        self.cases_panel.editRequested.connect(self.edit_case)
        self.cases_panel.duplicateRequested.connect(self.duplicate_case)
        self.cases_panel.deleteRequested.connect(self.delete_case)
        self.cases_panel.runRequested.connect(self.run_cases_by_id)
        self.cases_panel.runAllRequested.connect(self.run_all_studies)

        self._apply_default_docks()
        self._restore_window_state()
        self._update_title()
        self._update_actions()
        self.view.zoom_fit()

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #

    def _wrap_canvas(self) -> QWidget:
        host = QWidget()
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setVisible(False)
        box.addWidget(self.banner)
        box.addWidget(self.view, 1)
        return host

    def _build_docks(self) -> None:
        self.palette = PaletteList()
        self._dock("Library", self.palette, Qt.DockWidgetArea.LeftDockWidgetArea, "dock_library")

        self.variables = VariablesPanel(self.document)
        self._dock("Variables", self.variables, Qt.DockWidgetArea.LeftDockWidgetArea,
                   "dock_variables")

        self.properties = PropertyEditor(self.document)
        self._dock("Properties", self.properties, Qt.DockWidgetArea.RightDockWidgetArea,
                   "dock_properties")

        self.cases_panel = CasesPanel(self.document)
        self._dock("Study cases", self.cases_panel, Qt.DockWidgetArea.RightDockWidgetArea,
                   "dock_cases")

        self.results = ResultsPanel(self.document)
        dock = self._dock("Results", self.results, Qt.DockWidgetArea.BottomDockWidgetArea,
                          "dock_results")
        dock.setMinimumHeight(240)

    def _dock(self, title: str, widget: QWidget, area, object_name: str
              ) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(object_name)
        dock.setWidget(widget)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.addDockWidget(area, dock)
        setattr(self, object_name, dock)
        return dock

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def _act(self, text: str, slot, shortcut=None, tip: str = "",
             checkable: bool = False) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(shortcut)
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        action.setCheckable(checkable)
        return action

    def _build_actions(self) -> None:
        d = self.document
        self.act_new = self._act("&New", self.new_project, QKeySequence.StandardKey.New)
        self.act_open = self._act("&Open...", self.open_project, QKeySequence.StandardKey.Open)
        self.act_save = self._act("&Save", self.save_project, QKeySequence.StandardKey.Save)
        # Explicit: the platform's standard keys for these are empty on Windows.
        self.act_save_as = self._act("Save &As...", self.save_project_as, "Ctrl+Shift+S")
        self.act_example = self._act(
            "Load Assignment S1 example", self.load_example,
            tip="The EG2100 S1 network with A = 80, already validated")
        self.act_quit = self._act("E&xit", self.close, "Ctrl+Q")

        self.act_undo = d.undo.createUndoAction(self, "&Undo")
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_redo = d.undo.createRedoAction(self, "&Redo")
        self.act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self.act_delete = self._act("&Delete", self.delete_selection,
                                    QKeySequence.StandardKey.Delete)
        self.act_select_all = self._act("Select &All", self.select_all,
                                        QKeySequence.StandardKey.SelectAll)

        self.act_zoom_in = self._act("Zoom &In", lambda: self.view.zoom_by(1.2),
                                     QKeySequence.StandardKey.ZoomIn)
        self.act_zoom_out = self._act("Zoom &Out", lambda: self.view.zoom_by(1 / 1.2),
                                      QKeySequence.StandardKey.ZoomOut)
        self.act_zoom_fit = self._act("&Fit to window", self.view.zoom_fit, "Ctrl+0")
        self.act_zoom_reset = self._act("&Actual size", self.view.zoom_reset, "Ctrl+1")
        self.act_overlay = self._act(
            "Show results on the diagram", self._toggle_overlay, "Ctrl+R",
            tip="Annotate each bus with its voltage and each branch with its flow",
            checkable=True)
        self.act_overlay.setChecked(True)

        self.act_bases = self._act("&Base settings...", self.edit_bases,
                                   tip="S base, frequency and the reference bus")
        self.act_rotate = self._act("Rotate 90 deg", self.rotate_selection, "Ctrl+Alt+R")
        self.act_orient = self._act("Flip bus orientation", self.flip_bus, "Ctrl+Alt+O")

        self.act_run_all = self._act(
            "&Run all cases", self.run_all_studies, "F5",
            tip="Run every study case (every study, if the project has none)")
        self.study_actions = {}
        for key, study in STUDY_REGISTRY.items():
            self.study_actions[key] = self._act(
                study.title + "...", lambda _=False, k=key: self.run_study(k),
                tip=study.description)

        self.act_about = self._act("&About PowerLab", self.about)

    def _build_menus(self) -> None:
        bar = self.menuBar()

        m = bar.addMenu("&File")
        for a in (self.act_new, self.act_open):
            m.addAction(a)
        self.menu_recent = m.addMenu("Open &recent")
        self._rebuild_recent_menu()
        m.addSeparator()
        m.addAction(self.act_example)
        m.addSeparator()
        for a in (self.act_save, self.act_save_as):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_quit)

        m = bar.addMenu("&Edit")
        for a in (self.act_undo, self.act_redo):
            m.addAction(a)
        m.addSeparator()
        for a in (self.act_delete, self.act_select_all):
            m.addAction(a)
        m.addSeparator()
        for a in (self.act_rotate, self.act_orient):
            m.addAction(a)

        m = bar.addMenu("&View")
        for a in (self.act_zoom_in, self.act_zoom_out, self.act_zoom_fit,
                  self.act_zoom_reset):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_overlay)
        m.addSeparator()
        for dock in (self.dock_library, self.dock_variables,
                     self.dock_properties, self.dock_cases, self.dock_results):
            m.addAction(dock.toggleViewAction())

        m = bar.addMenu("&Network")
        m.addAction(self.act_bases)

        m = bar.addMenu("&Calculate")
        m.addAction(self.act_run_all)
        m.addSeparator()
        for group, studies in studies_by_group().items():
            sub = m.addMenu(group)
            for study in studies:
                sub.addAction(self.study_actions[study.key])

        bar.addMenu("&Help").addAction(self.act_about)

    def _build_toolbar(self) -> None:
        bar = getattr(self, "toolbar", None)
        if bar is None:
            bar = self.addToolBar("Main")
            bar.setObjectName("toolbar_main")
            bar.setMovable(False)
            self.toolbar = bar
        else:
            bar.clear()
        for a in (self.act_new, self.act_open, self.act_save):
            bar.addAction(a)
        bar.addSeparator()
        for a in (self.act_undo, self.act_redo, self.act_delete):
            bar.addAction(a)
        bar.addSeparator()
        for a in (self.act_zoom_out, self.act_zoom_in, self.act_zoom_fit):
            bar.addAction(a)
        bar.addSeparator()
        bar.addAction(self.act_run_all)
        bar.addAction(self.act_overlay)

    # ------------------------------------------------------------------ #
    # Document lifecycle
    # ------------------------------------------------------------------ #

    def _swap_document(self, doc: NetworkDocument) -> None:
        old_scene = self.scene
        if old_scene is not None:
            old_scene.detach()
        self.document = doc
        self.scene = SchematicScene(doc)
        self.view.setScene(self.scene)
        self.scene.selectionInfoChanged.connect(self.properties.bind)
        self.scene.requestProperties.connect(self._focus_properties)
        self.scene.statusMessage.connect(self.status.showMessage)
        self.scene.selectionChanged.connect(self._on_selection_changed)
        self.scene.studyAtBusRequested.connect(self.run_study_at_bus)
        self.scene.contextActionRequested.connect(self.context_action)

        self.properties.document = doc
        self.properties.bind(None)
        self.variables.document = doc
        self.results.document = doc
        self.cases_panel.set_document(doc)
        self._annotated = None
        self.banner.setVisible(False)

        doc.componentChanged.connect(self.properties._on_changed)
        doc.connectivityChanged.connect(self.properties._on_changed)
        doc.structureChanged.connect(self.properties.rebind)
        doc.parametersChanged.connect(self.variables.refresh)
        doc.structureChanged.connect(self.variables.refresh)
        doc.structureChanged.connect(self._update_actions)
        doc.parametersChanged.connect(self.properties.rebind)
        doc.resultsInvalidated.connect(self.results.mark_stale)
        doc.resultsInvalidated.connect(self._on_results_stale)
        doc.dirtyChanged.connect(self._update_title)

        self.act_undo.setEnabled(False)
        self.act_redo.setEnabled(False)
        self.menuBar().clear()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()

        self.variables.refresh()
        self.results.show_results([])
        self._update_title()
        self._update_actions()
        self.view.zoom_fit()

    def _confirm_discard(self) -> bool:
        if not self.document.is_dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes",
            f"{self.document.title()} has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return True

    def new_project(self) -> None:
        if self._confirm_discard():
            self._swap_document(blank_document())

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", str(self._start_dir()), FILE_FILTER)
        if not path:
            return
        try:
            doc = NetworkDocument.open(path)
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self._error("Could not open the project", exc)
            return
        self._swap_document(doc)
        self._remember_recent(path)
        self.status.showMessage(f"Opened {Path(path).name}", 4000)
        self.offer_linked_script()

    def save_project(self) -> bool:
        if not self.document.path:
            return self.save_project_as()
        try:
            self.document.save_to(self.document.path)
        except Exception as exc:  # noqa: BLE001
            self._error("Could not save the project", exc)
            return False
        self.status.showMessage(f"Saved {Path(self.document.path).name}", 4000)
        self._update_title()
        return True

    def save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project as", str(self._start_dir() / "network.psn"),
            FILE_FILTER)
        if not path:
            return False
        if not path.lower().endswith(".psn"):
            path += ".psn"
        try:
            self.document.save_to(path)
        except Exception as exc:  # noqa: BLE001
            self._error("Could not save the project", exc)
            return False
        self._remember_recent(path)
        self.status.showMessage(f"Saved {Path(path).name}", 4000)
        self._update_title()
        return True

    # -- recent files --------------------------------------------------- #

    def _recent_paths(self) -> list[str]:
        stored = _settings().value("recentFiles") or []
        if isinstance(stored, str):
            stored = [stored]
        return [p for p in stored if Path(p).is_file()]

    def _remember_recent(self, path: str) -> None:
        paths = [str(Path(path).resolve())]
        paths += [p for p in self._recent_paths()
                  if Path(p).resolve() != Path(path).resolve()]
        _settings().setValue("recentFiles", paths[:8])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        menu = getattr(self, "menu_recent", None)
        if menu is None:
            return
        menu.clear()
        paths = self._recent_paths()
        for p in paths:
            action = menu.addAction(Path(p).name)
            action.setToolTip(p)
            action.triggered.connect(
                lambda _checked=False, path=p: self.open_recent(path))
        if paths:
            menu.addSeparator()
            menu.addAction("Clear list", self._clear_recent)
        menu.setEnabled(bool(paths))

    def _clear_recent(self) -> None:
        _settings().remove("recentFiles")
        self._rebuild_recent_menu()

    def _forget_recent(self, path: str) -> None:
        paths = [p for p in self._recent_paths()
                 if Path(p).resolve() != Path(path).resolve()]
        _settings().setValue("recentFiles", paths)
        self._rebuild_recent_menu()

    def open_recent(self, path: str) -> None:
        if not self._confirm_discard():
            return
        try:
            doc = NetworkDocument.open(path)
        except Exception as exc:  # noqa: BLE001
            self._error("Could not open the project", exc)
            self._forget_recent(path)
            return
        self._swap_document(doc)
        self._remember_recent(path)
        self.offer_linked_script()

    def load_example(self) -> None:
        if not self._confirm_discard():
            return
        bundled = Path(__file__).resolve().parents[2] / "examples" / "s1_a80.psn"
        if bundled.is_file():
            try:
                # Untitled copy: Ctrl+S must not overwrite the reference example.
                self._swap_document(NetworkDocument.open(bundled, as_copy=True))
                self.status.showMessage("Loaded the Assignment S1 example", 4000)
                return
            except Exception:  # noqa: BLE001 - fall back to building it
                pass
        net = build_s1(80.0)
        project_vars, bindings = s1_parameter_bindings(80.0)
        proj = Project(network=net,
                       params=Parameters(project_vars=project_vars,
                                         bindings=bindings),
                       meta={"title": "EG2100 Assignment S1, A = 80"})
        proj.ensure_layout()
        self._swap_document(NetworkDocument(proj))

    def _start_dir(self) -> Path:
        if self.document.path:
            return Path(self.document.path).parent
        return Path(__file__).resolve().parents[2] / "examples"

    # ------------------------------------------------------------------ #
    # Editing
    # ------------------------------------------------------------------ #

    def selected_ids(self) -> list[str]:
        from .items import ComponentItem
        # A scene being destroyed clears its selection, and the signal can
        # arrive after its C++ side is gone.
        if not shiboken6.isValid(self.scene):
            return []
        return [i.comp_id for i in self.scene.selectedItems()
                if isinstance(i, ComponentItem)]

    def select_all(self) -> None:
        from .items import ComponentItem
        for item in self.scene.items():
            if isinstance(item, ComponentItem):
                item.setSelected(True)

    def delete_selection(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        dependents = []
        for obj_id in ids:
            if obj_id in self.document.network.buses:
                dependents += [d for d in self.document.dependents_of(obj_id)
                               if d.id not in ids]
        if dependents:
            names = "\n  ".join(f"{d.name or d.id} ({d.TYPE})" for d in dependents)
            answer = QMessageBox.question(
                self, "Delete connected components",
                f"These components are attached to the bus(es) you are "
                f"deleting and will be deleted too:\n\n  {names}\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        delete_objects(self.document, ids)
        self.properties.bind(None)

    def rotate_selection(self) -> None:
        from .items import BusItem
        for obj_id in self.selected_ids():
            item = self.scene.item_for(obj_id)
            if item is None or isinstance(item, BusItem):
                continue
            layout = dict(self.document.project.layout.get(obj_id, {}))
            layout["rotation"] = (float(layout.get("rotation", 0)) + 90) % 360
            from .commands import SetLayout
            self.document.undo.push(
                SetLayout(self.document, obj_id, layout, "Rotate"))

    def flip_bus(self) -> None:
        from .items import BusItem
        from .commands import SetLayout
        for obj_id in self.selected_ids():
            item = self.scene.item_for(obj_id)
            if not isinstance(item, BusItem):
                continue
            layout = dict(self.document.project.layout.get(obj_id, {}))
            layout["orientation"] = "H" if layout.get("orientation", "V") == "V" else "V"
            self.document.undo.push(
                SetLayout(self.document, obj_id, layout, "Flip bus"))

    def toggle_in_service(self) -> None:
        """Flip *In service* on the selection, in one undo step."""
        from .commands import SetField

        ids = self.selected_ids()
        if not ids:
            return
        net = self.document.network
        turn_on = not all(net.get(i).in_service for i in ids)
        doc = self.document
        doc.undo.beginMacro("Put in service" if turn_on else "Take out of service")
        for obj_id in ids:
            if net.get(obj_id).in_service != turn_on:
                doc.undo.push(SetField(doc, obj_id, "in_service", turn_on, "In service"))
        doc.undo.endMacro()

    def context_action(self, action: str, obj_id: str) -> None:
        """An entry of an element's right-click menu (it acts on the selection)."""
        handlers = {
            "properties": lambda: self._focus_properties(obj_id),
            "rotate": self.rotate_selection,
            "flip": self.flip_bus,
            "toggle_service": self.toggle_in_service,
            "delete": self.delete_selection,
            "cut": getattr(self, "cut_selection", None),
            "copy": getattr(self, "copy_selection", None),
            "duplicate": getattr(self, "duplicate_selection", None),
        }
        handler = handlers.get(action)
        if handler is not None:
            handler()

    def edit_bases(self) -> None:
        dialog = BaseSettingsDialog(self.document, self)
        if dialog.exec() == BaseSettingsDialog.DialogCode.Accepted:
            self.document.undo.push(
                SetNetworkSettings(self.document, *dialog.values()))

    # ------------------------------------------------------------------ #
    # Variables
    # ------------------------------------------------------------------ #

    def link_variable_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a Python file that defines variables",
            str(self._start_dir()), "Python files (*.py);;All files (*)")
        if not path:
            return
        answer = QMessageBox.question(
            self, "Run this script?",
            f"PowerLab will execute\n\n{path}\n\nas ordinary Python to read its "
            f"numeric variables. Only do this with a file you trust. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        from .commands import SetParameters
        params = self.document.params
        self.document.undo.push(SetParameters(
            self.document, path, params.project_vars, params.bindings))
        self.reload_variable_script()

    def offer_linked_script(self) -> None:
        """Ask before running the script an opened project links to.

        Opening a file never executes code by itself. Until the user agrees,
        the values saved in the project are used as they are.
        """
        script = self.document.params.script_path
        if not script:
            return
        answer = QMessageBox.question(
            self, "Run the linked script?",
            f"This project reads variables from\n\n{script}\n\nPowerLab would "
            f"execute it as ordinary Python. Only do this with a file you "
            f"trust. Run it now?\n\n(If you say no, the values saved in the "
            f"project are used; the Reload button in the Variables panel runs "
            f"it later.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.reload_variable_script()

    def reload_variable_script(self) -> None:
        issues = self.document.reload_script()
        for issue in issues:
            self.results.append_log(str(issue))
        errors = [i for i in issues if i.is_error]
        if errors:
            QMessageBox.warning(self, "Variable script",
                                "\n".join(str(e) for e in errors))
        else:
            self.status.showMessage("Variables reloaded", 4000)

    # ------------------------------------------------------------------ #
    # Studies
    # ------------------------------------------------------------------ #

    def run_study(self, key: str) -> None:
        """Calculate menu: edit (or create) the study's own case, then run it."""
        study = STUDY_REGISTRY[key]
        case = self.document.case(key) or Case(id=key, study=key)
        if study.option_fields:
            edited = self._edit_case_dialog(study, case)
            if edited is None:
                return
            self._store_case(edited)
            case = edited
        self._run_cases([case])

    def run_study_at_bus(self, key: str, bus_id: str) -> None:
        """Bus context menu: run a study at that bus now, without saving a case."""
        study = STUDY_REGISTRY[key]
        bus = self.document.network.buses.get(bus_id)
        label = (bus.name or bus_id) if bus else bus_id
        options = study.default_options(self.document.network)
        options["bus"] = bus_id
        self._run_cases([Case(id=f"{key}@{bus_id}", study=key,
                              title=f"{study.title} at bus {label}", options=options)])

    def run_all_studies(self) -> None:
        """F5: every case of the project, or every study when it has none."""
        self._run_cases(list(self.document.project.cases) or default_cases())

    def run_cases_by_id(self, case_ids: list[str]) -> None:
        cases = [c for c in (self.document.case(i) for i in case_ids) if c]
        if cases:
            self._run_cases(cases)

    def _run(self, keys: list[str], options: dict | None = None) -> None:
        """Run studies by key, with ``options`` or each one's saved case options."""
        cases = []
        for key in keys:
            saved = self.document.case(key)
            opts = dict(options) if options is not None else \
                dict(saved.options if saved else {})
            cases.append(Case(id=key, study=key, options=opts))
        self._run_cases(cases)

    # -- case editing --------------------------------------------------- #

    def _edit_case_dialog(self, study, case: Case) -> Case | None:
        dialog = CaseDialog(self.document, study, case, self)
        if dialog.exec() != CaseDialog.DialogCode.Accepted:
            return None
        return dialog.case()

    def _store_case(self, case: Case) -> None:
        """Insert or replace a case through the undo stack (marks the project modified)."""
        cases = list(self.document.project.cases)
        for i, existing in enumerate(cases):
            if existing.id == case.id:
                if existing == case:
                    return
                cases[i] = case
                break
        else:
            cases.append(case)
        self.document.undo.push(SetCases(self.document, cases, f"Save case {case.id}"))

    def new_case(self, study_key: str) -> None:
        study = STUDY_REGISTRY[study_key]
        case = Case(id=unique_case_id(self.document.project.cases, study_key),
                    study=study_key)
        edited = self._edit_case_dialog(study, case)
        if edited is not None:
            self._store_case(edited)

    def edit_case(self, case_id: str) -> None:
        case = self.document.case(case_id)
        study = STUDY_REGISTRY.get(case.study) if case else None
        if study is None:
            QMessageBox.warning(self, "Study case",
                                f"Case '{case_id}' runs an unknown study.")
            return
        edited = self._edit_case_dialog(study, case)
        if edited is not None:
            self._store_case(edited)

    def duplicate_case(self, case_id: str) -> None:
        cases = list(self.document.project.cases)
        index = next(i for i, c in enumerate(cases) if c.id == case_id)
        twin = copy.deepcopy(cases[index])
        twin.id = unique_case_id(cases, case_id)
        if twin.title:
            twin.title += " (copy)"
        cases.insert(index + 1, twin)
        self.document.undo.push(SetCases(self.document, cases, f"Duplicate case {case_id}"))

    def delete_case(self, case_id: str) -> None:
        cases = [c for c in self.document.project.cases if c.id != case_id]
        self.document.undo.push(SetCases(self.document, cases, f"Delete case {case_id}"))

    # -- running -------------------------------------------------------- #

    def _run_cases(self, cases: list[Case]) -> None:
        net = self.document.network
        issues = net.validate()
        errors = [i for i in issues if i.is_error]
        if errors:
            QMessageBox.warning(
                self, "The network is not ready",
                "Fix these before running a study:\n\n  "
                + "\n  ".join(str(e) for e in errors))
            self.results.append_log("\n".join(str(e) for e in errors))
            return

        results: list[Result] = []
        failures: list[PsaError] = []
        for case in cases:
            study = STUDY_REGISTRY.get(case.study)
            if study is None:
                failures.append(PsaError(f"Unknown study '{case.study}'."))
                results.append(Result(case.study, case.title or case.id,
                                      warnings=[f"Unknown study '{case.study}'."],
                                      case_id=case.id))
                continue
            try:
                results.append(run_case(net, case, self.document.params))
            except PsaError as exc:
                # One case that cannot run (say, S1a with unsuitable ports)
                # must not throw away the results of the others.
                failures.append(exc)
                results.append(Result(case.study, case.title or study.title,
                                      warnings=[f"Could not run: {exc}"],
                                      case_id=case.id))
            except Exception as exc:  # noqa: BLE001
                self._error(f"Unexpected error in {study.title}", exc)
                return
        if failures and len(failures) == len(cases):
            self._error("The study could not be completed", failures[-1], brief=True)
            return

        self.results.show_results(results)
        self.document.mark_results_valid()
        self.banner.setVisible(False)
        for res in results:
            self.results.append_log(res.text())

        self._annotated = None
        target = self.results.result_at(self.results.currentIndex())
        if self._annotating_study(target) is None:
            target = next((r for r in results if self._annotating_study(r)), None)
        self._show_annotations(target)

        warnings = [w for res in results for w in res.warnings]
        self.status.showMessage(
            "Done. " + (f"{len(warnings)} warning(s), see the Results panel."
                        if warnings else "All cross-checks passed."), 6000)

    # -- diagram annotations -------------------------------------------- #

    @staticmethod
    def _annotating_study(res: Result | None):
        if res is None:
            return None
        study = STUDY_REGISTRY.get(res.key)
        return study if study is not None and study.annotate else None

    def _show_annotations(self, res: Result | None) -> None:
        study = self._annotating_study(res)
        if study is None:
            return
        self._annotated = res
        if self.act_overlay.isChecked():
            mode = self.document.project.view.get("overlay_mode", "kv_pu")
            self.scene.show_annotations(study.annotate(res, mode))

    def _on_result_activated(self, res: Result | None) -> None:
        """Switching to a result tab shows that study's annotations, if it has any."""
        if self.document.results_valid and self._annotating_study(res):
            self._show_annotations(res)

    def _toggle_overlay(self) -> None:
        self.document.project.view["overlay"] = self.act_overlay.isChecked()
        if not self.act_overlay.isChecked():
            self.scene.clear_overlay()
        elif self.document.results_valid and self._annotated is not None:
            self._show_annotations(self._annotated)

    # ------------------------------------------------------------------ #
    # UI state
    # ------------------------------------------------------------------ #

    def _on_selection_changed(self) -> None:
        ids = self.selected_ids()
        self._update_actions()
        self.properties.bind_many(ids)

    def _focus_properties(self, obj_id: str) -> None:
        self.properties.bind(obj_id)
        self.dock_properties.raise_()

    def _on_results_stale(self) -> None:
        if self.results.count() > 1:
            self.banner.setText(
                "The model has changed, so the results below are out of date. "
                "Press F5 to run the studies again.")
            self.banner.setVisible(True)

    def _update_title(self) -> None:
        mark = " *" if self.document.is_dirty else ""
        self.setWindowTitle(f"{self.document.title()}{mark} - PowerLab")

    def _update_actions(self) -> None:
        has_buses = bool(self.document.network.buses)
        selected = bool(self.selected_ids())
        self.act_delete.setEnabled(selected)
        self.act_rotate.setEnabled(selected)
        self.act_orient.setEnabled(selected)
        self.act_run_all.setEnabled(has_buses)
        for action in self.study_actions.values():
            action.setEnabled(has_buses)
        n_bus = len(self.document.network.buses)
        n_comp = len(self.document.network.components)
        self._status_hint.setText(f"{n_bus} buses, {n_comp} components")

    # ------------------------------------------------------------------ #
    # Misc
    # ------------------------------------------------------------------ #

    def _error(self, title: str, exc: Exception, brief: bool = False) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(str(exc))
        if not brief:
            box.setDetailedText(traceback.format_exc())
        box.exec()
        self.results.append_log(f"{title}: {exc}")

    def about(self) -> None:
        QMessageBox.about(
            self, "About PowerLab",
            "<b>PowerLab</b><br>"
            "Interactive single-line diagram editor and analysis tool for "
            "KTH EG2100 Power System Analysis.<br><br>"
            "Draw buses, transformers, PI lines, loads and sources, then "
            "compute two-port (ABCD) matrices, equivalent impedances and bus "
            "voltages in per unit.<br><br>"
            "Every two-port result is computed twice, by cascading the element "
            "matrices and by reducing the Y-bus, and the two are cross-checked "
            "against each other.")

    # -- window state --------------------------------------------------- #

    def _apply_default_docks(self) -> None:
        """Proportions that fit the whole palette and the property labels."""
        self.dock_library.setMinimumWidth(240)
        self.dock_properties.setMinimumWidth(300)
        self.resizeDocks([self.dock_library, self.dock_variables],
                         [420, 250], Qt.Orientation.Vertical)
        self.resizeDocks([self.dock_library, self.dock_properties],
                         [275, 330], Qt.Orientation.Horizontal)
        self.resizeDocks([self.dock_properties, self.dock_cases],
                         [430, 240], Qt.Orientation.Vertical)
        self.resizeDocks([self.dock_results], [320], Qt.Orientation.Vertical)

    def _restore_window_state(self) -> None:
        settings = _settings()
        geometry = settings.value("geometry")
        state = settings.value("windowState")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)

    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        settings = _settings()
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())
        super().closeEvent(event)
