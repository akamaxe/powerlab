# PowerLab v1 roadmap

## Status

| Phase | Topic | Status |
|---|---|---|
| 0 | Repository, conventions, references | done |
| 1 | GUI on PySide6 | done |
| 2 | Core v2 model | pending |
| 3 | Study framework v2 and GUI wiring | pending |
| 4 | S2 end to end | pending |
| 5 | Editor ergonomics | pending |
| 6 | S3: sequence networks and unbalanced conditions | pending |
| 7 | Load flow (Newton–Raphson) | pending |
| 8 | Data manager, visualization, export, headless | pending |
| 9 | Documentation and v1 acceptance | pending |

## Context

`PowerSystemasAnalysis/` holds the KTH EG2100 (HT26) coursework, A-number 80: four solved assignments
(S1, S2, S3, LF), each with a Python script, a MATLAB solution (`matlab/`) and a LaTeX report whose
`latex/*_output.txt` is the MATLAB printout at A = 80. `PowerLab/` is the software tool that grew out of
the course: PyQt5 GUI (`psa/gui`, ~3.9k lines) over a Qt-free numpy/networkx core (`psa/core`), 117
unittest tests. Today it solves S1 completely; the core already has a validated Thévenin for S2b, but
there are no S2/S3/LF studies.

Goal of v1: a comfortable interactive single-line editor (PowerFactory / PSCAD / PSS/E style, aiming
higher) in which a user draws a network and solves everything the four assignments ask, matching the
MATLAB references. Phase 0 also adds the contributor notes (`CLAUDE.md`).

Facts that shape the plan (from exploring the code):
- Core debt: topology is hard-wired to 1 or 2 buses per element; one positive-sequence `PuModel`
  built by an `isinstance` chain (`perunit.py:286-359`); loads always become admittances; results are
  pre-formatted strings; the diagram overlay re-runs `solve_voltages` (`mainwindow.py:656-674`)
  instead of showing the study that ran; any unwired bus blocks every study.
- GUI debt: S1 special cases in `dialogs.py` and `MainWindow._fill_defaults`; wires derived (single
  L elbow, waypoints never written); property form rebuilt on every edit (focus loss); full scene
  rebuild on any change; GUI tests write to the real registry key `HKCU\Software\PowerLab\PowerLab`.
- Solid and kept: document/`QUndoStack`/commands as the only mutation path, base propagation with
  conflict reporting, the `ConversionStep` audit trail, `safe_eval` + project variables/bindings,
  versioned `.psn` JSON, MATLAB `format short` formatting, cross-check discipline.
- The assignment Python scripts are set to A = 45/54 and `S2a_Ybus.py` is stale: the references are
  the MATLAB outputs (A = 80) plus the values published in the statement (A = 0).

## Decisions

- **GUI on PySide6 (Qt 6)**: port the existing GUI first, then evolve it. Pinned to
  `PySide6==6.11.2`.
- **git + private GitHub repo**: `git init` in `PowerLab/`, private repo `akamaxe/powerlab`, pushed
  with `gh`. Course PDFs/figures stay out of the repo. Commit identity is the existing global
  config (noreply email). Before ever making the repo public, decide whether assignment reference
  data may stay (KTH honour code).
- **Everything in the repo is in English**: code, comments, UI strings, README, CLAUDE.md, roadmap,
  commit messages.

## Design principles (where PowerLab should beat the commercial tools)

1. **Glass box**: every number is traceable: per-unit conversion trail, Y-bus/Z-bus/Jacobian
   viewable with labels, NR iteration log, formulas named in the results.
2. **Parametric by default**: variables (`A = 80`) and expressions in every numeric field, plus
   study cases with overrides (`LD4.p_mw = 1.1*value`) and exclusions (industry disconnected).
3. **Verified engine**: each study cross-checks itself by a second method; tests pin every
   assignment number at full precision; pandapower is an independent oracle for load flow.
4. **Course-grade presentation**: Z, Y, S rectangular; U, I polar in degrees; MATLAB `format short`
   including the common-factor matrix print; ready-to-paste CSV/LaTeX export.
5. **Frictionless editing**: undo everything, no modal dead ends, readable diffable `.psn` files.

## Target architecture (core v2)

Components stay plain-data dataclasses; physics moves into a registry of element models.

- **Terminals**: each class declares `TERMINALS = (Terminal(field, role), ...)`; `BUS_FIELDS` derives
  from it in the same order, so wire keys stay stable. Removes the hard-coded "2 buses" in
  `network.py:102-108`, `bases.py:94-96`, validation.
- **Element models** (`psa/core/model/elements.py`): `base_ratio()`, `stamps(comp, seq, ctx)`,
  `injections(comp, ctx)` (load flow), `trace()` (audit rows, now with a `seq` field). Stamps
  (`model/stamps.py`): `BranchStamp` (2×2 admittance, covers PI lines, later taps and phase shift),
  `ShuntStamp`, `SourceStamp(e, z)`, `Injection(role slack|pv|pq, p, q, u_set, q_lim, reports)`.
- **Sequence models** (`model/sequence.py`): `NetworkModel.seq(POS|NEG|ZERO) -> SequenceModel`
  (built lazily, energized islands only) with `ybus(sources="internal_node"|"grounded"|"exclude")`.
  The old `to_per_unit`/`PuModel` becomes an adapter, so `twoport.py` and S1 stay untouched until
  the end.
- **Zero-sequence stamps**: YNyn → series Z0 + 3Zn; YNd → shunt at the YN side; Dyn (TI2) → shunt
  at bus e, open at bus d; Y0 loads/shunts keep their admittance, Y/Δ ones are open; buses with no
  zero-sequence path to ground are dropped from Y0 with an info message.
- **Solvers** (`psa/core/solve/`): `linear` (today's nodal solve, `LoadFlowResult` renamed
  `NodalSolution`, alias kept); `zbus` (one LU factorization per sequence, `scipy.linalg`);
  `faults`: one generic solver in which each condition is three phase constraints
  `A·U_abc + B·I_abc = 0`, solved as `(B·T − A·T·Z_s)·I_s = −A·T·U_pre` (sequence order 1,2,0).
  It covers 3ph, SLG, LL, LLG with Zf/Zg and the unbalanced Y0 shunt of S3e, and reduces exactly to
  the MATLAB formulas. Post-fault voltages are `U_n = U_pre,n − Z_n(:,k)·I_n`. `loadflow`: polar
  Newton–Raphson with H/N/J/L and the course's `ΔU/U` update; bus types derived from the connected
  elements; flat start, tolerance 1e-10; iteration log; optional Q-limit PV→PQ switching.
- **Results** (`results.py` extended, not replaced): numeric payload `scalars`, `buses`, `elements`
  (`Quantity` with kind/unit/base/seq) and `matrices` (`LabeledMatrix`). Tables (`present.py`),
  diagram annotations (`Study.annotate`) and export (`export.py`) all derive from it.
- **Studies and cases**: `OptionSpec` kinds bus | element | elements | choice | float | int | bool |
  complex | phase; `Study` gains `annotate`, `defaults` (replaces `_fill_defaults`) and
  `dynamic_choices` (S1a port shunts). A `Case` = study + options + field overrides + excluded
  elements, run on a deep copy of the network taken after bindings are applied.
- **New and extended components**: `ExternalGrid` (class rename of `Source`, file key `"source"`
  kept, alias `Source`): Z by Ω, Ik″+cosφ or Sk″+cosφ; Z0 by Ω, Ik1″+cosφ0 or Z0/Z1; u/θ setpoint for
  load flow. `Generator` (pv/slack/pq, P, U set, Q limits). `SVC` (voltage-controlled shunt: PV with
  P = 0, reports Q and B = Q/U²). `Load`: P-cosφ or P-Q spec, model constant Z/constant PQ, connection
  Y0/Y/Δ, Zn. `Shunt`: connection, model constant B/constant Q. `Line`: zero-sequence data as factors
  or absolute values; zero-sequence studies refuse lines with no Z0 data. `Transformer`: vector group,
  neutral impedances, optional Z0. `FieldSpec` gains `visible_if` for mode-dependent fields.
- **Robustness**: an unwired element is a warning, not a blocker; de-energized buses are greyed out;
  `PsaError` guards for zero impedance, zero length, Ik″ ≤ 0, no slack or two slacks per island,
  conflicting PV setpoints.
- **`.psn` v2**: `MIGRATIONS[1]` turns `studies{key: opts}` into `cases[]`; new fields have defaults;
  unknown fields are reported instead of silently dropped.

## Assignment → PowerLab mapping (references at A = 80, MATLAB)

| Task | PowerLab study (case) | Reference |
|---|---|---|
| S1a | `s1a` two-port a→e | F_ae, det = 1 |
| S1b | `s1b` | Z_I = 22.5232+11.1298i pu (A=0: 22.5156+11.0767i) |
| S1c | `linear`/`s1c` | U_e = 0.9952∠−0.5345° pu = 5.9715 kV |
| S2a | `ybus` POS, source as internal node (9×9), industry excluded | Y(1,1) = 45.47−401.55i, Y(9,9) = 45.47−299.68i |
| S2b | `thevenin` bus 7, industry excluded | U_Th7 = 0.9348∠−3.0196°, Z_Th7 = 0.1645+0.0677i (A=0: 0.9732∠−1.4224°, 0.0649+0.0356i) |
| S2c | `short_circuit` 3ph bus 7 | I_sc = 0.7584 kA; S_sc = 45.4220+18.6951i MVA (√3·Un·I also shown, labelled) |
| S2d | `linear`, override LDe ×0.5, industry connected | U_LDe = 0.9289∠−3.2857° pu = 5.5734 kV |
| S3a | `ybus` ZERO, grounded (8×8) | Y0(7,7) = 4.8795−1.4449i |
| S3b | `thevenin` bus 7, sequences 1/2/0 | Z_Th7,0 = 0.4031+0.1226i |
| S3c | `short_circuit` SLG bus 7 | I_f = 3.6126∠−22.4347° pu = 0.5214 kA |
| S3d | `thevenin` bus e, LDe excluded | U_Th,e = 0.9340∠−3.0422°, Z1 = 0.1694+0.3466i, Z0 = j0.1600 |
| S3e | `unbalanced_shunt` LDe, phase factors (0.5, 1, 1) | U7 = 21.4825∠−3.0011°, 21.4474∠−123.0463°, 21.4503∠117.0576° kV |
| LFa–c | `loadflow` (Q_sh5 constant Q) | P_G1 = 380.1658 MW, Q_G1 = 28.8537, Q_sh3 = 46.6418, Q_G7 = 28.9567 MVAr, P_loss = 22.1658 MW (A=0: 26.4240) |
| LFd | `loadflow`, override P_LD4 ×1.1 | P_loss = 24.3993 MW |
| LF variant | `loadflow`, shunt model constant B | P_loss = 22.1662 MW, Q_sh3 = 46.5526 MVAr |

Example projects (all parametric in `A`, generated from builders in `examples.py`, and a test
asserts that each `.psn` matches its builder): `s1_industry.psn` (today's `s1_a80.psn`),
`s2_grid.psn`, `grid_industry.psn` (grid + industry + sequence data; cases S2a–d, S3a–e),
`lf_220kv.psn` (cases LFa–d and the constant-B variant).

## Phases

Each phase ends with every test green, a commit series with English messages, and a push.
`CLAUDE.md` is updated at the end of each phase in which the architecture changes.

### Phase 0: repository, conventions, references
- `git init` in `PowerLab/`, `.gitignore` (`__pycache__/`, `build/`, `.pytest_cache/`), first
  commit of the current state, `gh repo create akamaxe/powerlab --private --source . --push`
  (check the name is free first).
- Translate `README.md` to English; reword references to files outside the repo.
- Write `CLAUDE.md` (contributor notes) and `docs/ROADMAP.md` (this file).
- `pyproject.toml`: metadata, dependencies (numpy, scipy, networkx, PySide6), pytest config
  (`testpaths = ["tests"]`). pytest also runs the existing unittest classes.
- Isolate QSettings in `tests/test_gui.py` (INI format in a temp dir, so tests stop touching
  the registry).
- Reference fixtures: `tools/refs/export_refs.m` runs the assignment MATLAB scripts in chain
  order (S1 → S2 → S3, and LF plus its variant) with A substituted (80 and 0), and writes
  `tests/refs/refs_A80.json` / `refs_A0.json` with `jsonencode` at full precision (complex as
  `[re, im]`). Run it once through the MATLAB MCP and commit the JSON.
- Acceptance: 117 tests pass under pytest; repo pushed; fixtures committed.

### Phase 1: port the GUI to PySide6
- Install PySide6 6.11.2. Port `psa/gui/*`, `run_gui.py` and the tests: `Signal`/`Slot`;
  `QAction`/`QShortcut`/`QUndoStack`/`QUndoCommand` move to `QtGui`; `exec()`; fully qualified
  enums; `event.position()`/`globalPosition()`; `angleDelta()`; drop the High-DPI attributes;
  `sip.isdeleted` → `shiboken6.isValid`; `QFontMetrics.horizontalAdvance`.
- Acceptance: all tests green; no `PyQt5` import left (grep); `run_gui.py --example` opens; an
  offscreen `QWidget.grab()` screenshot is checked.

### Phase 2: core v2 model (no new physics)
- Terminals, element-model registry, stamps, positive-sequence `SequenceModel`/`NetworkModel`,
  generic base propagation, `PuModel` adapter, energized islands, guards.
- Acceptance: 117 green; the adapter matches the old `PuModel` within 1e-15 on the S1 and S2
  networks; no `isinstance` dispatch left in `perunit.py`.

### Phase 3: study framework v2 and GUI wiring
- Numeric `Result`, `present.py` (with `fmt_matrix_scaled` for MATLAB's common factor),
  `OptionSpec`, `Study` v2, `Case`, `.psn` v2 migration.
- GUI: generic options dialog built from `OptionSpec` (delete the S1 special cases and
  `_fill_defaults`); overlay built from `Study.annotate` (no more `solve_voltages` re-run);
  annotations attached to their items so they move with them and are invalidated by layout
  changes; a *Study cases* panel (add, duplicate, run; F5 runs all cases); numeric result tables
  with copy.
- Acceptance: S1 text output identical to a golden file captured in Phase 0; the s1c annotations
  equal today's overlay; v1 → v2 migration test.

### Phase 4: S2 end to end
- `ExternalGrid` with impedance modes; studies `ybus`, `thevenin` (cross-checked: MATLAB route
  `Z(k,s)/Z(s,s)·E` against Kron), 3ph `short_circuit`, `linear` with cases.
- Builders and projects `s2_grid.psn` and `grid_industry.psn` (S2 cases).
- GUI: ExternalGrid symbol and mode-dependent fields; bus context-menu actions *Thévenin here* and
  *Short circuit here*; matrix viewer for Y-bus.
- Acceptance: S2a–d at A = 80 and A = 0 against the fixtures (rtol 1e-9), plus the 4-decimal
  values from `S2_output.txt`.

### Phase 5: editor ergonomics
- Property panel updated in place (no focus loss), field groups, min/max validation,
  multi-selection editing of shared fields.
- Wires as first-class objects: orthogonal routing, editable waypoints (`SetWire` command), stored
  attach offset along the bus, terminal exit direction that follows rotation, selectable wires.
- Busbar resize handles; labels kept upright and draggable; context menus (rotate, flip, delete,
  in/out of service, properties); copy/cut/paste/duplicate with id renaming; Esc cancels the
  palette tool; Ctrl+Shift+S and Ctrl+Q; highlight the real target while connecting.
- Incremental scene updates instead of full rebuilds; one registry entry per component type
  (dataclass, item, palette icon, defaults, id prefix) instead of today's seven touch points.
- Acceptance: GUI tests per feature; a generated network of IEEE-39 size stays responsive;
  manual checklist: redraw the S2 grid by hand.

### Phase 6: S3, sequence networks and unbalanced conditions
- Sequence data fields; negative- and zero-sequence stamps; Z-bus; generic fault solver (3ph,
  SLG, LL, LLG, Zf, Zg); `unbalanced_shunt` study; fault sweep over all buses; `seqmodel` study
  (sequence per-unit tables from the audit trail).
- `grid_industry.psn` gains sequence data and cases S3a–e (industry excluded for a–c).
- GUI: sequence-data property group, short-circuit annotations, Y0 in the matrix viewer.
- Acceptance: S3a–e against the fixtures (rtol 1e-9); property tests: unbalanced shunt with
  factors (1,1,1) equals the balanced solve, the 3ph constraint form equals U/Z, Dyn zero sequence
  is a shunt only.

### Phase 7: load flow (Newton–Raphson)
- `Generator`, `SVC`, P–Q loads, load and shunt models per study, derived bus types (course
  naming: Slack/PU/PQ), NR with iteration log and labelled Jacobian, losses as the sum of branch
  losses plus a balance residual, branch flows, optional Q limits.
- `lf_220kv.psn` with cases LFa–d and the constant-B variant.
- GUI: Generator and SVC symbols; load-flow annotations (U∠θ, bus type, P/Q, loading); colour by
  voltage.
- Acceptance: base model, variant and LFd at A = 80 against the fixtures (rtol 1e-8); A = 0
  P_loss = 26.4240 MW; pandapower cross-check (skipped if pandapower fails to import); Jacobian
  against finite differences.

### Phase 8: data manager, visualization, export, headless
- Data manager: one editable spreadsheet per component type (QTableView over `FieldSpec`, edits go
  through commands and can be undone), sort/filter, parametric cells; Ctrl+F finds an element and
  centres the diagram on it.
- Colour by voltage level and result heat maps with a legend.
- Export results to CSV, LaTeX, JSON and format-short text; export the diagram to PNG, SVG and PDF
  (`QSvgGenerator`, `QPdfWriter`); *Copy as MATLAB* for matrices.
- `run_headless.py` replaces `run_s1_headless.py` (`--case`, `--study`, `--export`).
- Move the S1 studies to the new model and drop the parts of the adapter nothing uses.
- Acceptance: export tests; CLI tests for every example and case.

### Phase 9: documentation and v1 acceptance
- English README: features, install, quickstart, and a walkthrough *Solving the EG2100
  assignments with PowerLab* (project, case and study per task, with the expected numbers);
  architecture and file format; CLAUDE.md and ROADMAP updated; tag `v1.0.0` and push.
- Optional independent check: rebuild the LF network in PowerFactory 2024 through the
  `powerfactory` MCP (parallel mode) and compare the load flow.


## Verification

- Every phase: `python -m pytest` from `PowerLab/` (GUI offscreen),
  plus a `git status` check before committing.
- Assignment acceptance: `tests/test_s1_a80.py` (exists), and new `test_s2_*`, `test_s3_*` and
  `test_lf_*` that load the fixtures and compare at rtol 1e-9 (linear/sequence) and 1e-8 (load
  flow), plus the 4-decimal values from the `*_output.txt` files as a second layer, at A = 80 and
  A = 0.
- Independent checks: pandapower (load flow), finite-difference Jacobian, Thévenin vs direct solve,
  Z_kk vs Kron, and optionally PowerFactory through the MCP.
- GUI: an offscreen screenshot of each example after running its cases (read back with Read), and a
  manual smoke run of `run_gui.py` (draw, connect, edit, undo, run, annotate, export).
- CLI: `run_headless.py <example>.psn --case <id>` for every case prints the reference numbers.

## Out of scope for v1 (post-v1 roadmap)

Multiple diagram pages, switches and breakers with topology processing, 3-winding transformers, an
element type library, IEC 60909 short circuit (c-factors), Gauss–Seidel/DC/fast-decoupled load
flow, OLTC and phase-shift control, transformer phase shifts in sequence networks, RMS/EMT dynamics,
harmonics, MATPOWER / PSS/E RAW import (IEEE 39 as a target: `diagramas/ieee39_new_england.drawio`),
auto-layout for meshed networks, a scripting console and plugins.

## Main risks

- Sign and ordering conventions in sequence networks (current direction, order 1,2,0, Δ windings
  as shunts). Covered by the S3c/S3e fixtures and the (1,1,1) identity test.
- Keeping S1 bit-identical through the refactor. Covered by the golden file and the parity test.
- GUI coupling in Phase 3: the up-front per-unit conversion and the overlay re-run in `mainwindow.py`
  must change in the same phase as the study framework.
- PySide6 alongside conda's PyQt5 in the base env: never import both in one process. Spyder keeps
  PyQt5 because qtpy prefers it.
