# PowerLab

Interactive single-line diagram editor and power system analysis tool, started from the course
**EG2100 Power System Analysis (KTH)**.

You place buses, transformers, PI-model lines, loads, shunts, generators, SVCs and external
grids on a canvas in the style of DIgSILENT PowerFactory / ETAP, and the app converts the network
to per unit and computes what **all four assignments (S1, S2, S3 and LF)** ask for: the two-port
(ABCD) matrix, the equivalent impedance, the bus voltages, the admittance matrices of the three
sequence networks, Thevenin equivalents, short circuits of every type, unbalanced loads and the
Newton-Raphson load flow, with the results formatted the way the course requires.

| Assignment | Status in PowerLab |
|---|---|
| **S1** — two-port, equivalent Z, voltages | Complete in the GUI and in console mode, validated against the assignment statement (A = 0) and against the MATLAB reference solution (A = 80) |
| **S2** — Y-bus, Thévenin, three-phase short circuit | Complete: `examples/s2_grid.psn` and `examples/grid_industry.psn` hold the four tasks as study cases, validated against the MATLAB reference solution (A = 80) and the assignment statement (A = 0) |
| **S3** — sequence networks, single line-to-ground fault | Complete: cases S3a-S3e in `examples/grid_industry.psn` (S3a-S3c also in `examples/s2_grid.psn`), validated against the MATLAB reference solution (A = 80 and A = 0) |
| **LF** — load flow | Complete: cases LFa-LFd and the constant-B reading of the bus-5 shunt in `examples/lf_220kv.psn`, validated against the MATLAB reference solution (A = 80 and A = 0) and against pandapower |

The plan for v1 (all four assignments, a PowerFactory-grade editor) is in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## Installation and startup

**Requirements:** Python (tested with Anaconda's 3.12.13; 3.10 or later should work),
**PySide6** (Qt 6), **numpy**, **scipy** and **networkx**. Nothing else: matplotlib is not used,
and the tests run with pytest or with the standard library's `unittest`. Console mode
(`run_headless.py`, `run_s1_headless.py`) does not need PySide6.

Anaconda ships numpy, scipy and networkx; PySide6 comes from pip:

```
pip install PySide6 numpy scipy networkx
```

On Windows with Anaconda, Qt 6 can fail to load ("the specified procedure could not be found")
because Anaconda's own ICU library shadows the one Qt expects. `psa.gui` works around it when
imported, so import it before anything that imports PySide6 (`run_gui.py` already does).

There is nothing to install or build for the project itself. Open a terminal (PowerShell or
Anaconda Prompt) in the `PowerLab` folder and run:

```
python run_gui.py --example        # opens the validated S1 network (A = 80)
python run_gui.py                  # empty canvas
python run_gui.py my_network.psn   # opens a project
```

- `--example` opens `examples/s1_a80.psn` as an **untitled copy**: `Ctrl+S` asks where to save it
  and never overwrites the example. *File > Load Assignment S1 example* does the same.
- If the file does not exist or is damaged, the app still starts with an empty project and tells
  you why it could not open it.
- If the project links a variable script, the app **asks before running it** (see
  [Parametric variables](#parametric-variables)).
- In VS Code, open `run_gui.py` and run it (*Run Python File*). Arguments are added in a terminal.
- The recent-files list and the window layout are stored with `QSettings`
  (on Windows, in the registry: `HKCU\Software\PowerLab\PowerLab`).

### Console mode

`run_headless.py` runs the study cases of any project without opening a window, prints every
table and can export what it ran:

```
python run_headless.py examples/lf_220kv.psn                  # every case of the project
python run_headless.py examples/lf_220kv.psn --case LFd       # one case (repeatable)
python run_headless.py examples/s2_grid.psn --study thevenin  # a study with its defaults
python run_headless.py examples/lf_220kv.psn --set A=0        # change a project variable first
python run_headless.py examples/s2_grid.psn --export s2.json  # also write .txt, .csv, .tex or .json
python run_headless.py examples/s2_grid.psn --list            # the project's cases and the studies
```

| Option | What it does |
|---|---|
| `project.psn` | Project to solve |
| `--case ID` | Run this case of the project. Repeatable. Without `--case` or `--study`, every case runs (every study with its defaults, if the project has none) |
| `--study KEY` | Run this study: the project's case of the same id if there is one, otherwise the study's defaults. Repeatable |
| `--set NAME=VALUE` | Set a project variable before solving (`--set A=0` gives the answers printed in the assignments). Repeatable |
| `--export PATH` | Also write the results; the extension picks text, CSV, LaTeX or JSON |
| `--quiet` | Do not print the results (with `--export`) |
| `--list` | List the project's cases and the available studies |
| `--run-script` | Run the linked variable script. Without it the saved values are used |

A case that fails prints why and the others still run (exit code 1); a project that cannot be
opened, an unknown case or a bad variable gives exit code 2.

`run_s1_headless.py` prints the S1 report (bases, per-unit values, S1a-S1c) of the built-in S1
network or of a project:

```
python run_s1_headless.py                         # built-in S1 network, A = 80
python run_s1_headless.py --a-number 0            # the answer published in the assignment
python run_s1_headless.py examples/s1_a80.psn     # a saved project
python run_s1_headless.py my_network.psn --study s1b  # a single study (repeatable)
```

| Option | What it does |
|---|---|
| `project.psn` | Project to solve. Without it the built-in S1 network is used |
| `--a-number N` | A-number of the built-in network (80 by default). Ignored when a project is given |
| `--study KEY` | Only `model`, `s1a`, `s1b` or `s1c`. Repeatable |
| `--run-script` | Runs the linked variable script. Without it the saved values are used |

It uses the buses and ports you chose in the GUI for each study (they are saved in the project).
If the project cannot be opened or the network is invalid, it prints the reason and returns exit
code 2.

## What it can do

### Components

| Component (palette) | Model | Parameters |
|---|---|---|
| **Bus** | Node (`busbar`, or `junction`, drawn as a dot) | Optional pinned `U base` |
| **External grid** | Voltage source behind a Thevenin impedance, given in Ω (0 = ideal) or by its fault level: `Ik` [kA] or `Sk` [MVA] with cos φ | `U` [pu], angle [°], impedance mode, `Z_th` or `Ik`/`Sk`, cos φ, nominal `U` [kV], slack |
| **Transformer** | Two-winding, series impedance `r + jx`, no magnetizing branch | `S_n` [MVA], `U_1n`/`U_2n` [kV], `x` and `r` [%], side Z is referred to |
| **Line / Cable** | PI equivalent, n identical lines in parallel | `r`, `x` [Ω/km], `b_c`, `g` [S/km], length [km], number in parallel |
| **Generator** | Load flow only: PV (holds `P` and `U`), slack (holds `U` and the angle) or PQ | control, `P` [MW], `Q` [MVAr], `U set` [pu], angle [°], `Q min`/`Q max` [MVAr] |
| **SVC** | Voltage-controlled shunt (SVC, synchronous condenser): holds its bus voltage in the load flow with whatever `Q` it takes, reported with `B = Q/U²` | `U set` [pu], `Q min`/`Q max` [MVAr] |
| **Load** | Constant impedance at its nominal voltage; in the load flow, constant power when its model says so | `P` [MW] with cos φ (lagging/leading) or `Q` [MVAr], nominal `U` [kV], model, connection Y0 / Y / Δ, `Zn` |
| **Shunt** | Capacitor (`Q > 0`) or reactor (`Q < 0`); in the load flow a constant susceptance rated at its nominal voltage or a constant `Q` | `Q` [MVAr], nominal `U` [kV], model, connection Y0 / Y / Δ |
| **Impedance** | Series R + jX between two buses, or to ground | `R`, `X` in Ω or in pu |

**Sequence data** (the *Zero sequence* group of each element's properties): a line gives its
zero-sequence impedance as factors of r, x, b or as per-km values, and zero-sequence studies refuse
to run while a line has none; a transformer has a vector group (YNyn, YNd, Dyn, ...) and neutral
impedances; loads and shunts a connection; the external grid its `Z0` (in Ω, from a single
line-to-ground fault current `Ik1` with cos φ, or as `Z0/Z1`). The negative sequence equals the
positive one, without source voltages; transformer phase shifts are not modelled, as in the course.

Every component has a name and an *In service* checkbox: an out-of-service component is drawn
grey and dashed, and no study takes it into account. A new component starts with sensible values
(e.g. a 10 MVA 40/12 kV 7 % transformer and a 0.16 + j0.08 Ω/km cable, as in S1).

### Studies and study cases (*Calculate* menu, `F5` runs every case)

| Study | What it shows |
|---|---|
| **Per-unit model** | Bases of every bus (`U`, `Z`, `I`), voltage zones and every component in pu, with the `(U_b/U_n)²` factor visible so it can be audited |
| **S1a) Two-port matrix** | F matrix of each element in cascade order, the product `F` between the two buses with the convention `[U₁; I₁] = F·[U₂; I₂]`, `det(F)` and the comparison between methods |
| **S1b) Equivalent impedance** | `Z = U/I` looking into the network from a bus, in pu and in Ω, and the check against `(A·Z+B)/(C·Z+D)` |
| **S1c) Bus voltages** | Solves the linear network: voltage of every bus (pu, angle, kV), flows and losses per branch, current and power of each source, voltage drop and the chain `U_a → I_a → U_e` |
| **Admittance matrix (Y-bus)** | The Y-bus of the positive, negative or zero sequence of the energized network, with MATLAB's common factor, its node list and a symmetry check. Sources behind an impedance appear as an extra internal node (the S2a form), as a shunt to ground (the S3a form), or not at all |
| **Sequence model** | Every element in the positive- and zero-sequence networks, with the path it gives (a Dyn transformer as a shunt on its star side, a delta load open, ...) |
| **Bus voltages (linear network)** | The S1c solve for any network (S2d) |
| **Thevenin equivalent** | `U_th` from the pre-fault solve and `Z_th,1`, `Z_th,2`, `Z_th,0` from the impedance matrices, in pu and SI, with the three-phase and line-to-ground fault currents and `S_sc`; cross-checked against Kron reduction and against the MATLAB route `Z(k,s) E / Z(s,s)` |
| **Short circuit** | Three-phase, single line-to-ground, line-to-line or double line-to-ground fault through `Z_f` (and `Z_g`): sequence and phase currents in pu and kA, the fault power (`U_th conj(I_f) S_base`, the course's definition, and `√3 U_base |I_f|`), and every bus phase voltage and branch phase current during the fault |
| **Unbalanced load** | A load drawing a different power on each phase (S3e: half of phase a disconnected), solved with the sequence networks; the phase voltages it causes at every bus |
| **Load flow (Newton-Raphson)** | Polar Newton-Raphson from a flat start with the course's bus types (Slack, PU, PQ, derived from what is connected): every bus voltage, the slack generation, the reactive power of the voltage-controlled buses (and `B = Q/U²` of an SVC), loads and shunts at the solved voltage, branch flows, total losses cross-checked by the power balance, the iteration log and the labelled Jacobian `[H N; J L]`. Options: load and shunt models for the whole network, reactive limits (a PU bus at its limit becomes PQ), tolerance and iterations |

Faults and unbalanced loads are one solver: the condition at the bus is written as three phase
equations and solved in symmetrical components against the sequence Thevenin equivalents.

The load flow sees the same network as the other studies, plus what only it uses: generators,
SVCs, constant-power loads and constant-Q shunts become injections, while constant-impedance loads
and constant-B shunts stay in the admittance matrix. The linear, Thevenin and fault studies always
treat loads as impedances and ignore generators and SVCs.

Right-click a bus for *Thevenin equivalent here* or *Three-phase short circuit here*: the study
runs at once, without adding a case to the project.

A **study case** is a study plus its options and, optionally, a variant of the network: elements
taken out of service and fields overridden by an expression (`0.5*value` halves a load, and the
project variables are available too). The drawn network is never changed, so one project can hold
"industry disconnected", "load at 50 %" and "+10 % load" side by side. Cases live in the *Study
cases* panel (New, Edit, Duplicate, Delete, Run selected, Run all) and are saved with the project;
every change can be undone. `F5` runs all the cases; a project without cases runs every study with
its default options.

From the *Calculate* menu, a study opens the dialog of its own case (its options are remembered
in the project). In S1a the dialog lists the shunts sitting on the port buses: they are excluded
because they are the termination, but you can tick them to include them. An ideal source on a
port can never be included, because it would short-circuit the port. Whatever hangs off a port
without leading to the other one (the rest of a feeder beyond port 2) is part of the termination
as well.

**Every two-port matrix is computed twice**, by cascading the element matrices and by Kron
reduction of the Y-bus, and the two are compared. This is a real check, not decoration: the
line's charging lives inside `F_pi` in one method and on the diagonal of Y in the other, so
counting it twice would give a relative error of ~1e-5, invisible at four decimals in `U_e` but
obvious in this residual. In a meshed network the cascade does not exist and only the Y-bus is
used, with a warning.

Also:

- **Validation before computing**: missing reference bus, branches joining a bus to itself,
  zero-impedance branches, transformer loops with incompatible ratios (it says at which bus and
  because of which component), buses without a base, islands, floating buses and decoupled ports.
  Errors are explained in a message, not with a traceback. A component that is not wired yet only
  gives a warning and is left out, so a half-drawn element never blocks the studies.
- If a study cannot run, the others are still shown and the failed one appears with the reason.
- **Diagram annotations** (`Ctrl+R`): the study in the selected result tab annotates the diagram;
  for the bus voltages, each bus shows its voltage in kV and in pu with angle, and each branch
  `|I|` and `S`. Badges follow their element when you move it. Any change to the model clears
  them and marks the results as stale.
- **Copy results**: select cells and press `Ctrl+C`, or right-click a table for *Copy table*
  (tab-separated, ready for a spreadsheet). A matrix table (Y-bus, Jacobian, `Z_ph`, `F`) also
  offers *Copy as MATLAB*, which keeps every digit: `Y = [45.466333698683 - 401.554516317015i, ...];`.
- **Export** (*File* menu): *Export results* (`Ctrl+E`) writes the last run as text, CSV, a LaTeX
  `booktabs` table or JSON with the full-precision numbers behind the tables; *Export diagram*
  (`Ctrl+Shift+E`) saves the drawing as PNG, SVG or PDF, with the annotations shown and without
  the grid.
- **Data manager** (bottom panel, `Ctrl+Shift+D`): every element of a type in one editable table,
  a tab per type. Cells take the same input as the property editor (expressions too), every edit
  can be undone, the columns sort and the filter box narrows the rows. Fields the element's mode
  does not use are greyed out; cells driven by an expression are tinted. Double-click an id to
  show the element on the diagram.
- **Find** (`Ctrl+F`): select an element by name or id and centre the diagram on it.
- **Colour by** (*View* menu): voltage level (a colour per base voltage, for buses, one-level
  elements and wires) or bus voltage (the band each bus's `|U|` falls in, for the result shown;
  the lowest phase during a fault), with a legend in the corner of the canvas.
- **Course presentation**: impedance and power in rectangular form; voltage and current in polar
  form with the angle in degrees, in MATLAB's `format short` style.

What it does **not** do (yet): breakers as a component of their own, off-nominal taps and phase
shifters, transformer phase shifts in the sequence networks, or a distributed slack.

## Usage

### The window

- **Library** (left): the component palette.
- **Variables** (left): project variables and those of the linked script.
- **Properties** (right): form for the selected element, generated from its fields.
- **Study cases** (right): the cases `F5` runs, with their buttons.
- **Results** (bottom): one tab per study with the headline, the warnings and the tables, plus a
  *Log* tab with the full text. If you change the model, the tabs are marked with ` *` and a
  notice appears over the canvas.
- **Data manager** (bottom, next to *Results*): the component tables.
- Menus: *File* (projects, export), *Edit* (clipboard, find), *View* (zoom, annotations, colouring,
  show/hide panels), *Network* (*Base settings...*), *Calculate* (*Run all cases* and one submenu
  per study group) and *Help*.

### Drawing and connecting

Click a component in the *Library* and then on the canvas, or drag it. With `Shift` held the tool
stays active to place several; a right-click deactivates it. Each terminal connects by itself to
the nearest free bus (within a small radius), so a series element dropped between two buses ends
up connected to both, and a load dropped next to a bus attaches to it.

To connect by hand, drag from the terminal (the small circle) to a bus; the bus it would land on
is marked, and if you release on empty canvas a new bus is created there. `Esc` or a right-click
cancels the connection. Double-clicking an element takes you to the *Properties* panel. Elements
snap to the grid.

- **Right-click** any element for its menu: properties, cut, copy, duplicate, rotate (or switch a
  bus between vertical and horizontal), in service, delete; a bus also offers the Thevenin and
  short-circuit studies.
- **Busbars**: select one and drag the handle at either end to make it longer or shorter.
- **Wires** leave each terminal the way the symbol points and run at right angles. Double-click a
  wire to add a bend and drag the bend's handle; right-click a wire to add or remove bends.
- **Labels** stay upright when a symbol is rotated; drag a name to put it somewhere else.
- **Copy and paste** keep the connections among the copied elements and their expressions; a
  connection to a bus that was not copied is dropped.
- **Several elements of one type** selected together are edited together in *Properties*; fields
  whose values differ show "(several values)". Values outside a field's range are refused.

### Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+N` / `Ctrl+O` / `Ctrl+S` / `Ctrl+Shift+S` | New / open / save / save as |
| `Ctrl+Q` | Exit |
| `Ctrl+Z` / `Ctrl+Y` (or `Ctrl+Shift+Z`) | Undo / redo |
| `Ctrl+X` / `Ctrl+C` / `Ctrl+V` / `Ctrl+D` | Cut / copy / paste / duplicate (on the canvas) |
| `Del` | Delete the selection; deleting a bus warns and takes along whatever hangs from it |
| `Ctrl+A` | Select all |
| `Esc` | Cancel a connection, or drop the component picked in the Library |
| `Ctrl+Alt+R` / `Ctrl+Alt+O` | Rotate 90° / switch the bus between vertical and horizontal |
| `Ctrl` + wheel, `Ctrl++` / `Ctrl+-` | Zoom |
| `Ctrl+0` / `Ctrl+1` | Fit all / actual size |
| Middle button or `Space` + drag | Pan the canvas |
| `F5` | Run all study cases |
| `Ctrl+R` | Show or hide the diagram annotations |
| `Ctrl+F` | Find an element by name or id |
| `Ctrl+Shift+D` | Data manager |
| `Ctrl+E` / `Ctrl+Shift+E` | Export results / export the diagram |

Undo covers adding, deleting, moving, connecting, editing properties, the bases, the variables
(the network goes back to its previous values) and the buses chosen for each study. It does not
cover reloading the script or the annotations toggle. Leaving a field without changing it does
not create an undo step.

### Bases

`Network > Base settings...`: you define `S_base`, the frequency and the bus where you know the
base voltage. The others are derived by propagating through each transformer's rated ratio. You
can also pin the `U base` of a particular bus in its properties. If a transformer loop is
inconsistent, the app says at which bus and because of which component, instead of choosing
silently. Bases follow the nameplate, not the switching state: taking a branch out of service
does not remove a bus's base.

### Parametric variables

Numeric fields accept expressions, like a spreadsheet. Define `A = 80` in the *Variables* panel
(*Add variable*) and write the length as `(217 + A)/1000`; the field is coloured and shows the
evaluated value below it, or the error in red. Change `A` to `0` and everything is re-evaluated:
that is how the result is checked against the answer the assignment itself publishes for A = 0
(`Z_I = 22.5156 + 11.0767i pu`).

Expressions support `+ - * / // % **`, complex numbers (`1j`) and the functions `sqrt`, `sin`,
`cos`, `tan`, `asin`, `acos`, `atan`, `atan2`, `exp`, `log`, `log10`, `hypot`,
`degrees`/`deg`, `radians`/`rad`, `abs`, `min`, `max`, `round`, `float`, `int` and the constants
`pi` and `e`. They do not support attributes, indexing or lambdas. Complex fields (the source's
`Z_th`) also accept `1.6+0.3j` or `1.6+0.3i`. For example, the external grid of S2 (70 kV, 25 kA,
cos φ = 0.15) is written directly in `Z Thevenin`:

```
70/(sqrt(3)*25)*(0.15 + 1j*sqrt(1 - 0.15**2))        # = 0.2425 + 1.5983i Ω
```

You can also define the variables in a Python script of your own (*Link script...*), such as
`examples/variables_S1.py`, and use them in the expressions. It runs as ordinary Python, so the
app asks for confirmation when you link it **and every time you open a project that links it**;
if you say no, the values saved in the project are used and *Reload* runs it later. Only public
numeric scalars (no leading `_`) are kept.

### The `.psn` format

A project is a UTF-8 JSON file, readable and easy to compare in a diff:

| Key | Contents |
|---|---|
| `format`, `version` | `"psa-project"` and the format version (2 today; version 1 files are migrated on open) |
| `meta` | Title and notes |
| `network` | `S_base`, frequency, reference bus and voltage, buses and components (complex numbers as `[re, im]`) |
| `parameters` | `script_path`, project variables and *bindings* `"COMPONENT.field": "expression"` |
| `layout` | Grid, position of every symbol, and wires |
| `cases` | Study cases: `id`, `study`, `title`, `options`, `overrides`, `exclude` |
| `view` | Annotation state |

Only `network` is mandatory: a file without `layout` opens with an automatic ladder layout
starting from the reference bus.

## Structure

```
run_gui.py            GUI startup
run_headless.py       console mode: any project's cases, variables, export
run_s1_headless.py    console mode: the S1 report
psa/core/     numpy + networkx, no Qt at all (a test checks it in a subprocess)
  components  dataclasses Bus / ExternalGrid / Transformer / Line / Load / Shunt / Impedance /
              Generator / SVC, with their terminals and editor fields
  network     container, graphs, structural validation
  bases       base propagation by BFS through rated ratios
  model/      element models and sequence networks (where physical units become numbers):
              elements (one model per component class: stamps and load-flow
              injections), stamps, sequence (build_model, Y-bus)
  solve/      solvers over the sequence networks: nodal solve, Z-bus, Thevenin, the shunt
              condition solver (faults, unbalanced loads), Newton-Raphson load flow
  perunit     the per-unit view the S1 studies read
  ybus        Y-bus and Kron reduction, with conditioning guards
  twoport     termination rule, cascade, general method, cross-check
  analysis    equivalent impedance, nodal solve, Thévenin (basis for S2)
  params      project variables, user script, safe-eval, bindings
  study       STUDY_REGISTRY, cases, and the S1 studies
  studies/    the other studies: grid (Y-bus, linear, Thevenin, short circuit), sequence,
              loadflow
  results     result tables; units: `format short` formatting
  export      results as text, CSV, LaTeX, JSON; matrices as MATLAB literals
  serialize   project <-> `.psn` JSON, with a migration chain
  examples    the assignment networks, parametrized by the A-number, with their cases
  errors      typed errors carrying the ids of the elements involved
psa/gui/      PySide6 (Qt 6); importing it first works around Anaconda's ICU on Windows
  app         QApplication, initial document, exception hook
  mainwindow  menus, panels and study execution
  document    owner of the project and of the QUndoStack
  commands    the QUndoCommands: the only code that mutates the network
  scene       canvas, grid, connection state machine, overlay
  items       single-line symbols
  panels      palette, property editor, variables, results
  dialogs     bases and study options
  datamanager the component tables
  editing     typed text into field edits (shared by properties and data manager)
  colouring   colour by voltage level or bus voltage, and the legend
  export      the diagram as PNG, SVG or PDF
  style       colours, pens and style sheet
```

### Adding a study

Write the function in `psa/core/` and register it with `study.register(Study(key, title,
menu_group, run, option_fields, defaults, annotate, ...))`. That makes it appear by itself in the
*Calculate* menu, in the *New case* menu, in `F5`, in the results tabs and in console mode's
`--study`; the case dialog is generated from its `OptionSpec`s and `annotate` draws its results on
the diagram. No GUI code has to change.

## Verification

```
python -m pytest                              # 263 tests, a few seconds, the GUI runs offscreen
python -m unittest discover -s tests -t .     # the same suite without pytest
python run_s1_headless.py                     # prints the full S1 table
python run_s1_headless.py --a-number 0        # the answer published in the assignment
```

| File | Tests | What it covers |
|---|---|---|
| `test_s1_a80.py` | 28 | Bases, pu values, `F_ae`, `det`, cascade vs Y-bus, termination rule, `Z_I`, phasor chain, A sweep |
| `test_matlab_refs.py` | 3 | S1 and the S2b Thévenin against every digit of the MATLAB solutions, A = 80 and A = 0 |
| `test_core_model.py` | 17 | Element models and sequence networks: parity with the previous per-unit model, the S2 Y-bus (9x9 and 8x8) against MATLAB, the unchanged console output, robustness |
| `test_cases_and_results.py` | 16 | Study cases and network variants, the version 1 to 2 file migration, diagram annotations, MATLAB's matrix print |
| `test_serialize_and_validation.py` | 28 | `.psn` round trip, the script is not run on open, automatic layout, validations, meshed network, parallel cables |
| `test_core_units_and_params.py` | 20 | `format short` formatting, safe-eval, bindings, variable script, Qt-free core |
| `test_gui.py` | 90 | Canvas, placement, undo (variables too), parametric and complex fields, connection drag, main window, study cases and their dialog, annotations, property editing in place and in bulk, context menus, clipboard, bus handles, wire bends, draggable labels, responsiveness at IEEE-39 size, startup, load flow in the window, export, data manager, find, colouring |
| `test_export_and_cli.py` | 14 | Text, CSV, LaTeX, JSON and MATLAB export (every digit kept); `run_headless.py` on every case of every example, variables, export, exit codes |
| `test_s2.py` | 10 | S2a-S2d from the example projects against the MATLAB solution, A = 80 and A = 0; the external grid from its fault level |
| `test_s3.py` | 18 | S3a-S3e from the example projects against the MATLAB solution, A = 80 and A = 0; closed-form fault currents; zero-sequence element models |
| `test_lf.py` | 17 | LFa-LFd and the constant-B variant from the example project against the MATLAB solution, A = 80 and A = 0; the Jacobian against finite differences and the course formulas; reactive limits, conflicting setpoints, islands without slack, divergence; pandapower cross-check (opt-in) |
| `test_s2_thevenin.py` | 2 | Thévenin at bus 7 of S2 for A = 0 and A = 80 |

The pandapower cross-check is skipped unless `PSA_ORACLES=1` is set, because importing
pandapower alone takes longer than the rest of the suite.

The S1 reference values come from the MATLAB reference solution of the assignment (A = 80). The
A = 0 case is checked against the number printed in the assignment statement itself, so it is an
independent validation and not a comparison against our own earlier output. Summary of what the
app prints for A = 80:

```
Ubase (a; b,c,d; e) = 40, 12, 6 kV     Zbase = 160, 14.4, 3.6 Ω
Z_TI1 = 0.0000 + 0.1167i   Z_TI2 = 0.0000 + 0.1600i
Z_LIbc = 0.0016 + 0.0008i   ysh_LIbc = 0.0000e+00 + 1.2830e-05i
Z_LDb = 2.1892e+02 + 1.0603e+02i pu   Z_LDe = 25.0774 + 12.1455i pu
F_ae: A = 1.0002 + 0.0004i   B = 0.0049 + 0.2792i
      C = 0.0037 - 0.0018i   D = 1.0003 + 0.0006i        det(F) = 1
Z_I  = 22.5232 + 11.1298i pu = 3.6037e+03 + 1.7808e+03i ohm
U_e  = 0.9952 pu < -0.5345 deg = 5.9715 kV, voltage drop 0.4751 %
```

For S2, `test_s2_thevenin.py` builds the network of figure 2 with the current components and
checks `U_Th7 = 0.9732∠−1.4224°`, `Z_Th7 = 0.0649 + 0.0356i pu` (assignment statement, A = 0) and
`U_Th7 = 0.9348∠−3.0196°`, `Z_Th7 = 0.1645 + 0.0677i pu`, `I_sc7 = 0.7584 kA` (MATLAB reference
solution, A = 80).

## Conventions

- Per unit with one global `S_base` and one `U_base` per voltage zone.
- Constant-impedance loads evaluated at their own nominal voltage.
- Transformers without a magnetizing branch, with the ratio absorbed by the change of base.
- Shunt with `Q > 0` capacitive, the same sign as line charging.
- A line's `b_c` is the **total** susceptance per km of a single line; the PI puts half at each
  end.
- Presentation as the course requires: **impedance and power in rectangular form; voltage and
  current in polar form with the angle in degrees**, in MATLAB's `format short` style.
- Bases follow the nameplate, not the switching state.
- Load flow: bus types named as in the course (Slack, PU, PQ). An external grid is the slack at
  its own bus; its impedance is short-circuit data and does not enter the load flow.

## Example projects

| File | What it holds |
|---|---|
| `examples/s1_a80.psn` | Assignment S1: the factory network, cases S1a-S1c |
| `examples/s2_grid.psn` | Assignment S2 and S3a-S3c: the meshed 40 kV grid fed from 70 kV, with its sequence data |
| `examples/grid_industry.psn` | The S2 grid with the S1 industry at bus 7: S2a-S2c and S3a-S3c (industry out of service), S2d (load LDe halved), S3d and S3e (half of phase a of LDe disconnected) |
| `examples/lf_220kv.psn` | Assignment LF: eight 220 kV buses with the slack generator at bus 1, an SVC at bus 3 and a PV generator at bus 7; cases LFa-LFc (one run), LFd (P_LD4 up 10 %) and the bus-5 shunt as a susceptance rated at 220 kV |

All of them are parametric in the variable `A`: set it to 0 to reproduce the answers printed in
the assignment. `python tools/make_examples.py` rewrites the generated files (all but
`s1_a80.psn`) from their builders in `psa/core/examples.py`.

## Known limitations and roadmap

The full v1 plan is in [`docs/ROADMAP.md`](docs/ROADMAP.md). In short:

**Load flow:** one slack per island (no distributed slack), no off-nominal taps or phase
shifters, and a PU bus that reaches a reactive limit stays PQ (no switching back).

**GUI:** one diagram page per project, no breakers or switches, and the data manager edits one
cell at a time.
