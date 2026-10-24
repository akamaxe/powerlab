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
`tests/test_core_units_and_params.py` checks it in a subprocess) and `psa/gui` (PyQt5 today,
PySide6 from roadmap phase 1).

Core pipeline, in the order a study sees it:

1. `components.py`: plain dataclasses holding floats only. Connectivity is the bus-id strings in
   the fields each class lists in `BUS_FIELDS`; ids are unique across buses and components, and
   `Network` dict insertion order becomes matrix order. `FIELDS` (`FieldSpec`) drives the GUI
   property editor and the study option dialogs.
2. `params.py`: project variables, an optional user script and bindings
   (`"COMP.field": "expression"`) are evaluated with an AST-whitelisted `safe_eval` and written
   into the dataclass fields eagerly (on load, on variable edits). The network always holds
   evaluated numbers; nothing downstream sees expressions.
3. `bases.py`: per-unit bases by BFS from `base_ref_bus` through rated transformer ratios,
   following the nameplate rather than the switching state; conflicts are reported, not resolved.
4. `perunit.py`: `to_per_unit` turns active components into a `PuModel` (series branches, shunts,
   sources) plus a `ConversionStep` audit trail that feeds the per-unit table. It is an
   `isinstance` chain and positive-sequence only; roadmap phase 2 replaces it with element models
   and per-sequence stamps.
5. `ybus.py`, `analysis.py` (linear nodal solve, equivalent impedance, Thévenin), `twoport.py`
   (S1a cascade, cross-checked against Kron reduction of the Y-bus).
6. `study.py`: `STUDY_REGISTRY` of `Study(key, title, menu_group, run(net, opts, pu),
   option_fields)`. The GUI menu, F5 and the headless `--study` all come from it.
7. `results.py` + `units.py`: `Result` holds pre-formatted string tables in MATLAB
   `format short` style.

GUI: `NetworkDocument` owns the `Project` and the `QUndoStack`; the `QUndoCommand`s in
`commands.py` are the only code that mutates the network (exceptions: `apply_parameters` and
`reload_script`). Document signals drive the scene and the panels. The scene keeps no model of its
own: items hold an element id and read the dataclass. Layout lives apart from the electrical model
in `project.layout` (keyed by element id) and `project.wires` (keyed `"COMP:terminalIndex"`).
`MainWindow._run` converts to per unit once, runs the studies, then re-runs `solve_voltages` for
the diagram overlay, whatever study ran (to be replaced in roadmap phase 3).

`.psn` files are JSON (`serialize.py`, `VERSION` plus a `MIGRATIONS` chain). Components are built
generically from dataclass fields, so a new field with a default needs no migration.

Adding a component type currently touches: the dataclass and `COMPONENT_CLASSES`
(`components.py`), the dispatch in `perunit.to_per_unit`, `Network.validate`, `ITEM_CLASSES`
(`gui/items.py`), `PALETTE_ITEMS` and `symbol_icon` (`gui/panels.py`), `_apply_defaults`
(`gui/scene.py`), the id prefixes in `NetworkDocument.new_id`, the connectivity-field list in
`NetworkDocument._apply_set_field`, and `serialize.auto_layout`. Adding a study touches
`study.register` plus the S1 special cases in `gui/dialogs.py` (it only renders bus fields),
`MainWindow._fill_defaults` and `SchematicScene.show_overlay`.

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
- `tests/golden/*.txt`: `run_s1_headless.py` output captured before the core v2 refactor. Output
  must stay identical; regenerate them only for an intended change.
- `tests/test_gui.py` runs on the offscreen Qt platform and redirects `QSettings` to a temporary
  INI file; it reaches into private attributes, so GUI refactors usually need test updates.
