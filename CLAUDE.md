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
python run_headless.py examples/lf_220kv.psn [--case ID] [--study KEY] [--set A=0] [--export out.json]
python run_s1_headless.py [project.psn] [--study KEY] [--a-number N]   # the S1 report
PSA_ORACLES=1 python -m pytest tests/test_lf.py -k pandapower   # opt-in: pandapower takes ~15 s to import
```

Regenerating the MATLAB reference fixtures needs the coursework folder and MATLAB; see
`tools/refs/README.md`. `python tools/make_examples.py` rewrites the generated example projects
(`examples/s2_grid.psn`, `examples/grid_industry.psn`, `examples/lf_220kv.psn`) from
`psa.core.examples.example_projects()`;
`tests/test_s2.py` fails when a file is out of date. `examples/s1_a80.psn` is hand-made.

## Architecture

Two packages with a hard boundary: `psa/core` (numpy, scipy, networkx; no Qt: `CoreIsQtFree` in
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
   All three sequences exist: negative = positive with the sources' EMF at 0; zero sequence from
   the elements' data (line `z0_mode`, transformer `vector_group` and `Zn` — YNyn series, YNd/Dyn
   a shunt on the grounded-star side, else open —, load/shunt `connection`, grid `z0_mode`); a line
   without Z0 data makes `seq(Seq.ZERO)` raise. No transformer phase shifts. For the load flow
   an element model also returns `injections(comp, ctx)` (`Injection`: role slack/pv/pq with P,
   Q, U set, Q limits and what it `reports`), collected by `NetworkModel.injections()`.
   `ModelOptions.load_model` / `shunt_model` decide what is stamped: by default every load is a
   constant impedance and every shunt a constant B (the linear and fault studies); `as_set`
   honours each component's `model`, so a `constant_power` load or a `constant_q` shunt becomes
   an injection instead. `Generator` and `SVC` stamp nothing (an audit row only). `perunit.py` is the
   S1 view on top: `to_per_unit` returns the legacy `PuModel` (PI branches, shunts, sources,
   trace, and the `SequenceModel` it came from in `PuModel.sm`) and stays strict.
5. `solve/`: solvers over a `SequenceModel` — `linear.solve_nodal` (pre-fault state, sources as
   boundary conditions or Norton pairs), `zbus.ZBus` (impedance-matrix columns from one scipy LU
   factorization, ideal-source buses grounded), `thevenin.thevenin_at` (with cross-checks against
   Kron reduction and the MATLAB `Z(k,s) E / Z(s,s)` route). `ZBus` treats islands with no path
   to ground in a sequence as open (infinite impedance). `shunt.py` is the one solver for faults
   and unbalanced loads: a condition `A U_abc + B I_abc = 0` at a bus, solved in sequence order
   (1, 2, 0) with `T = [[1,1,1],[a^2,a,1],[a,a^2,1]]` against the sequence Thevenin equivalents
   (`SequenceNetworks`); `fault_constraints` gives `A, B` for 3ph/slg/ll/llg and
   `phase_admittance` for Y0/Y/D loads. `faults.solve_fault` builds only the sequences a fault
   needs. `loadflow.solve_load_flow` is polar Newton-Raphson: bus types from the injections
   (Slack, PU, PQ; exactly one slack per island, voltage setpoints at a bus must agree), the
   blocks H, N, J, L from the complex derivatives of `S = U conj(Y U)`, the `dU/U` update, a flat
   start, tolerance 1e-10 pu; optional Q limits turn a PU bus into PQ at its limit and re-solve
   (no switching back). It never raises on divergence: the solution says `converged=False` and
   keeps the iteration log; the Jacobian is kept at the flat start and at the last state.
   The S1 code reads the `PuModel` view: `ybus.py` (Y-bus and Kron reduction), `analysis.py`
   (equivalent impedance, and `solve_voltages`, which runs `solve_nodal` on the model behind
   the view and adds the S1 presentation) and `twoport.py` (S1a cascade, cross-checked against
   Kron reduction of the Y-bus; whatever hangs off a port without reaching the other one is part
   of the termination).
6. `cases.py` + `study.py`: a `Case` is a study id plus options, field `overrides`
   (`"COMP.field": "expr"`, with the project variables and `value`, the field's own value) and
   `exclude` (ids taken out of service); `prepare_network` applies them to a deep copy, so the
   drawn network is never touched. `STUDY_REGISTRY` holds `Study(key, title, menu_group,
   run(StudyContext), option_fields, defaults, dynamic_choices, note, annotate)`; options are
   `OptionSpec` (kinds bus, element, elements, choice, float, int, bool, complex, str).
   `run_case(net, case, params)` lays the case options over `defaults(net)`. Calling a study
   directly, `study(net, opts, pu)`, still works. The S1 studies live in `study.py`; the others
   in `studies/` (`grid.py`: `ybus`, `linear`, `thevenin`, `short_circuit`; `sequence.py`:
   `seqmodel`, `unbalanced_load`; `loadflow.py`: `loadflow`, whose options include the load and
   shunt models), imported at the end
   of `study.py` so they register themselves. These use `build_model(..., energized_only=True)`,
   so a case that takes part of the network out of service just sees the rest.
7. `results.py` + `present.py` + `units.py`: a `Result` holds formatted `Table`s (MATLAB
   `format short`) and the numbers behind them: `scalars`, per-bus `buses`, per-component
   `elements` (`Quantity` with a `units.QUANTITY_STYLE` kind) and labelled `matrices`.
   `Study.annotate(result, mode)` turns a result into diagram `Annotation`s; `present.py` prints
   matrices with MATLAB's common factor (`1.0e+02 *`), and a table printed from a matrix keeps its
   key in `Table.source`. `export.py` writes results as text, CSV, LaTeX (booktabs) or JSON
   (full-precision numbers, complex as `{re, im}`) and matrices as MATLAB literals;
   `run_headless.py` is its command line.

GUI: `NetworkDocument` owns the `Project` and the `QUndoStack`; the `QUndoCommand`s in
`commands.py` are the only code that mutates the network (exceptions: `apply_parameters` and
`reload_script`). Document signals drive the scene and the panels; the scene updates
incrementally (`objectAdded` / `objectRemoved` add or drop one symbol and its wires,
`structureChanged` only refreshes labels, `wireChanged` re-routes one wire). The scene keeps no
model of its own: items hold an element id and read the dataclass. Layout lives apart from the
electrical model in `project.layout` (keyed by element id: position, rotation, bus orientation and
length, a dragged `label_offset`) and `project.wires` (bends per `"COMP:terminalIndex"`, absolute
scene points; wires are routed orthogonally through them by `items.orthogonal_route`, leaving
each terminal along `terminal_direction`). Per component type, `gui/registry.py` holds the palette
entry, symbol class, id prefix and placement defaults. `gui/clipboard.py` copies selections as
JSON and pastes them with fresh ids. The property editor updates widgets in place and edits
several same-type objects at once; it and the data manager (`gui/datamanager.py`, a
`QAbstractTableModel` per type over `FIELDS`) turn typed text into commands through
`gui/editing.py` (numbers, expressions that become bindings, empty for a `None` default).
`gui/export.py` renders the scene to PNG/SVG/PDF with `scene.print_mode` (no grid) and the
selection cleared; `gui/colouring.py` computes per-id tints (`ComponentItem.tint`,
`WireItem.tint`, applied by `scene.set_tints`) and the legend `SchematicView.set_legend` shows.
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

`ExternalGrid` (file type key `"source"`, alias `Source`) gives its impedance in ohm or by fault
level (`z_mode` `ik`/`sk`, `|Z| = U/(sqrt(3) Ik)` or `U^2/Sk` at `acos(cos_phi)`, `U = u_pu * un_kv`);
`FieldSpec.visible_if` hides the fields of the other modes in the property editor.

Adding a component type touches: the dataclass (with `TERMINALS`) and `COMPONENT_CLASSES`
(`components.py`), an `ElementModel` in `model/elements.py`, `Network.validate` for its sanity
checks, a symbol class in `gui/items.py` and its `ElementUI` entry in `gui/registry.py`, and
`serialize.auto_layout`. Adding a study only takes a
`study.register(Study(...))` call: the menu, the case dialog, F5, the result tab and the diagram
badges all follow from it.

## Modelling and presentation conventions

- Per unit: one global `S_base`, one `U_base` per voltage zone.
- Loads are constant impedance at their own nominal voltage (in the load flow, `model =
  constant_power` makes one a PQ injection; a shunt's `model` picks constant B, rated at its
  nominal voltage, or constant Q); transformers have no magnetizing
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
- `tests/test_s2.py` and `tests/test_s3.py` run every S2 and S3 case of the example projects
  against the fixtures, at A = 80 and, by setting the project variable A, at A = 0;
  `test_s3.py` also checks the closed-form 3ph/SLG/LL/LLG currents from the general solver.
- `tests/test_lf.py` runs the LF cases of `examples/lf_220kv.psn` against `LF` and `LF_q_rated`
  at `rtol=1e-8`, checks the Jacobian against finite differences, and cross-checks pandapower
  when `PSA_ORACLES=1`.
- `tests/fixtures/s1_a80_v1.psn`: a version 1 project, for the migration test.
- `tests/golden/*.txt`: `run_s1_headless.py` output captured before the core v2 refactor, and
  `pumodel_v1.json`, the per-unit model of the previous implementation; `test_core_model.py`
  holds the refactor to both. Regenerate them only for an intended change.
- `tests/test_gui.py` runs on the offscreen Qt platform and redirects `QSettings` to a temporary
  INI file; it reaches into private attributes, so GUI refactors usually need test updates. A test
  that edits the document must close its `MainWindow` with `_confirm_discard` patched to return
  True: otherwise the unsaved-changes `QMessageBox` is modal and the run hangs.
