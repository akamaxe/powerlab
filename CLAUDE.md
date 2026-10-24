# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

PowerLab is an interactive single-line diagram editor plus a power system analysis engine, started
from the KTH course EG2100. The v1 goal (all four course assignments: S1, S2, S3, LF, in a
PowerFactory-grade editor) and its phases are in `docs/ROADMAP.md`; update its status table and
this file when a phase changes the architecture.

Everything in this repository is written in English: code, comments, UI strings, docs and commit
messages.

## Commands

Run everything from the repository root with the Anaconda interpreter by full path:
`C:\Users\marce\anaconda3\python.exe` (a bare `python` resolves to the Microsoft Store stub).

```
python -m pytest                                   # full suite, GUI runs offscreen
python -m pytest tests/test_s1_a80.py -k f_matrix  # one test
python -m unittest discover -s tests -t .          # same suite without pytest
python run_gui.py --example                        # GUI with the validated S1 network
python run_s1_headless.py [project.psn] [--study KEY] [--a-number N]
```

Regenerating the MATLAB reference fixtures needs the coursework folder and MATLAB; see
`tools/refs/README.md`.

## Architecture

Two packages with a hard boundary: `psa/core` (numpy + networkx, no Qt; `CoreIsQtFree` in
`tests/test_core_units_and_params.py` checks it in a subprocess) and `psa/gui` (PySide6 /
Qt 6). Import `psa.gui` before anything that imports PySide6: on Windows its `__init__`
preloads the system ICU, because Anaconda's `Library\bin\icuuc.dll` otherwise shadows it and
`Qt6Core.dll` fails to load. Enums are written fully qualified (`Qt.MouseButton.LeftButton`).

Core pipeline, in the order a study sees it:

1. `components.py`: plain dataclasses holding floats only. Connectivity is the bus-id strings in
   the fields each class lists in `TERMINALS` (`BUS_FIELDS` is derived from it); `is_series` is
   structural (two or more required terminals), and a component with an unconnected terminal is
   a validation warning that `Network.active_components` leaves out. Ids are unique across buses
   and components, and `Network` dict insertion order becomes matrix order. `FIELDS`
   (`FieldSpec`) drives the GUI property editor and the study option dialogs.
2. `params.py`: project variables, an optional user script and bindings
   (`"COMP.field": "expression"`) are evaluated with an AST-whitelisted `safe_eval` and written
   into the dataclass fields eagerly (on load, on variable edits). The network always holds
   evaluated numbers; nothing downstream sees expressions.
3. `bases.py`: per-unit bases by BFS from `base_ref_bus`, crossing each series branch with its
   element model's `base_ratio` (rated ratios), following the nameplate rather than the switching
   state; conflicts are reported, not resolved.
4. `model/`: where components become numbers. `elements.py` holds one `ElementModel` per
   component class (registered with `@element_model`), which writes `BranchStamp` (PI or general
   2x2), `ShuntStamp`, `SourceStamp` and a `ConversionStep` audit row. `sequence.py`:
   `build_model(net, ModelOptions)` validates, propagates bases and returns a `NetworkModel`;
   `NetworkModel.seq(Seq.POS)` gives the cached `SequenceModel`, whose `ybus(sources=...)`
   treats finite-impedance sources as excluded, grounded (Norton shunt) or as an internal node
   (the MATLAB S2 9x9 form). `ModelOptions(energized_only=True)` drops islands without a source.
   Only the positive sequence exists so far. `perunit.py` is the S1 view on top: `to_per_unit`
   returns the legacy `PuModel` (PI branches, shunts, sources, trace) and stays strict.
5. `ybus.py`, `analysis.py` (linear nodal solve, equivalent impedance, Thévenin), `twoport.py`
   (S1a cascade, cross-checked against Kron reduction of the Y-bus; whatever hangs off a port
   without reaching the other one is part of the termination).
6. `cases.py` + `study.py`: a `Case` is a study id plus options, field `overrides`
   (`"COMP.field": "expr"`, with the project variables and `value`, the field's own value) and
   `exclude` (ids taken out of service); `prepare_network` applies them to a deep copy, so the
   drawn network is never touched. `STUDY_REGISTRY` holds `Study(key, title, menu_group,
   run(StudyContext), option_fields, defaults, dynamic_choices, note, annotate)`; options are
   `OptionSpec` (kinds bus, element, elements, choice, float, int, bool, complex, str).
   `run_case(net, case, params)` lays the case options over `defaults(net)`. Calling a study
   directly, `study(net, opts, pu)`, still works.
7. `results.py` + `present.py` + `units.py`: a `Result` holds formatted `Table`s (MATLAB
   `format short`) and the numbers behind them: `scalars`, per-bus `buses`, per-component
   `elements` (`Quantity` with a `units.QUANTITY_STYLE` kind) and labelled `matrices`.
   `Study.annotate(result, mode)` turns a result into diagram `Annotation`s; `present.py` prints
   matrices with MATLAB's common factor (`1.0e+02 *`).

GUI: `NetworkDocument` owns the `Project` and the `QUndoStack`; the `QUndoCommand`s in
`commands.py` are the only code that mutates the network (exceptions: `apply_parameters` and
`reload_script`). Document signals drive the scene and the panels. The scene keeps no model of its
own: items hold an element id and read the dataclass. Layout lives apart from the electrical model
in `project.layout` (keyed by element id) and `project.wires` (keyed `"COMP:terminalIndex"`).
`MainWindow._run_cases` runs each case through `run_case` (F5 runs the project's cases, or one
default case per study when there are none); the Calculate menu edits the study's own case (id =
study key) in `CaseDialog`, which is generated from the `OptionSpec`s and has a "Network
variant" tab. Cases change through the `SetCases` command. Diagram badges come from the
annotating study of the shown result tab and follow their element when it moves. The app uses
the Fusion style: the native Windows 11 style loses check-box indicators under a global style
sheet.

`.psn` files are JSON (`serialize.py`, `VERSION` 2 plus a `MIGRATIONS` chain; version 1 kept
`studies` options and migrates to `cases`), written with LF line endings. Components are built
generically from dataclass fields, so a new field with a default needs no migration.

Adding a component type currently touches: the dataclass (with `TERMINALS`) and
`COMPONENT_CLASSES` (`components.py`), an `ElementModel` in `model/elements.py`,
`Network.validate` for its sanity checks, `ITEM_CLASSES` (`gui/items.py`), `PALETTE_ITEMS` and
`symbol_icon` (`gui/panels.py`), `_apply_defaults` (`gui/scene.py`), the id prefixes in
`NetworkDocument.new_id`, and `serialize.auto_layout`. Adding a study only takes a
`study.register(Study(...))` call: the menu, the case dialog, F5, the result tab and the diagram
badges all follow from it.

## Modelling and presentation conventions

- Per unit: one global `S_base`, one `U_base` per voltage zone.
- Loads are constant impedance at their own nominal voltage; transformers have no magnetizing
  branch and the ratio is absorbed by the base change; a shunt with `Q > 0` is capacitive; a
  line's `b_s_per_km` is the total charging of one line, split half per PI end; `n_parallel`
  divides Z and multiplies the shunts.
- Results follow the course: Z, Y and S rectangular; U and I polar with the angle in degrees;
  MATLAB `format short`.

## Tests and references

- `tests/refs/refs_A80.json`, `refs_A0.json`: every numeric variable of the MATLAB reference
  solutions, full precision, loaded through `tests/refdata.py` (`ref(80, "S2", "Zth7_pu")`).
  New assignment tests compare against these at `rtol=1e-9` (load flow `1e-8`), at A = 80 and
  A = 0. The A = 0 S3b values printed in the assignment statement contain misprints; see
  `tools/refs/README.md`.
- `tests/fixtures/s1_a80_v1.psn`: a version 1 project, for the migration test.
- `tests/golden/*.txt`: `run_s1_headless.py` output captured before the core v2 refactor, and
  `pumodel_v1.json`, the per-unit model of the previous implementation; `test_core_model.py`
  holds the refactor to both. Regenerate them only for an intended change.
- `tests/test_gui.py` runs on the offscreen Qt platform and redirects `QSettings` to a temporary
  INI file; it reaches into private attributes, so GUI refactors usually need test updates.
