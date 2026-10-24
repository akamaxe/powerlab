# PowerLab

Interactive single-line diagram editor and power system analysis tool, started from the course
**EG2100 Power System Analysis (KTH)**.

You place buses, transformers, PI-model lines, loads, shunts and sources on a canvas in the style
of DIgSILENT PowerFactory / ETAP, and the app converts the network to per unit and computes what
**Assignment S1** asks for: the two-port (ABCD) matrix, the equivalent impedance and the bus
voltages, with the results formatted the way the course requires.

| Assignment | Status in PowerLab |
|---|---|
| **S1** — two-port, equivalent Z, voltages | Complete in the GUI and in console mode, validated against the assignment statement (A = 0) and against the MATLAB reference solution (A = 80) |
| **S2** — Y-bus, Thévenin, three-phase short circuit | The core already computes the S2b Thévenin and a test validates it, but there is no study or menu yet (see the [roadmap](#known-limitations-and-roadmap)) |
| **S3** — sequence networks, single line-to-ground fault | Pending |
| **LF** — load flow | Pending |

The plan for v1 (all four assignments, a PowerFactory-grade editor) is in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## Installation and startup

**Requirements:** Python (tested with Anaconda's 3.12.13; 3.10 or later should work),
**PySide6** (Qt 6), **numpy** and **networkx**. Nothing else: scipy and matplotlib are not
used, and the tests run with pytest or with the standard library's `unittest`. Console mode
(`run_s1_headless.py`) only needs numpy and networkx.

Anaconda ships numpy and networkx; PySide6 comes from pip:

```
pip install PySide6 numpy networkx
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

`run_s1_headless.py` solves a project without opening any window and prints every table:

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
| **Source / Grid** | Ideal voltage source, or one behind a `Z_th` (external grid) | `U` [pu], angle [°], `Z_th` [Ω, complex], slack |
| **Transformer** | Two-winding, series impedance `r + jx`, no magnetizing branch | `S_n` [MVA], `U_1n`/`U_2n` [kV], `x` and `r` [%], side Z is referred to |
| **Line / Cable** | PI equivalent, n identical lines in parallel | `r`, `x` [Ω/km], `b_c`, `g` [S/km], length [km], number in parallel |
| **Load** | Constant impedance at its nominal voltage | `P` [MW], cos φ, lagging/leading, nominal `U` [kV] |
| **Shunt** | Capacitor (`Q > 0`) or reactor (`Q < 0`) | `Q` [MVAr], nominal `U` [kV] |
| **Impedance** | Series R + jX between two buses, or to ground | `R`, `X` in Ω or in pu |

Every component has a name and an *In service* checkbox: an out-of-service component is drawn
grey and dashed, and no study takes it into account. A new component starts with sensible values
(e.g. a 10 MVA 40/12 kV 7 % transformer and a 0.16 + j0.08 Ω/km cable, as in S1).

### Studies (*Calculate* menu, `F5` runs them all)

| Study | What it shows |
|---|---|
| **Per-unit model** | Bases of every bus (`U`, `Z`, `I`), voltage zones and every component in pu, with the `(U_b/U_n)²` factor visible so it can be audited |
| **S1a) Two-port matrix** | F matrix of each element in cascade order, the product `F` between the two buses with the convention `[U₁; I₁] = F·[U₂; I₂]`, `det(F)` and the comparison between methods |
| **S1b) Equivalent impedance** | `Z = U/I` looking into the network from a bus, in pu and in Ω, and the check against `(A·Z+B)/(C·Z+D)` |
| **S1c) Bus voltages** | Solves the linear network: voltage of every bus (pu, angle, kV), flows and losses per branch, current and power of each source, voltage drop and the chain `U_a → I_a → U_e` |

From the menu, the S1 studies open a dialog to choose the buses (they are remembered in the
project; `F5` uses the last ones chosen). In S1a and S1b the dialog lists the shunts sitting on
the port buses: they are excluded because they are the termination, but you can tick them to
include them. An ideal source on a port can never be included, because it would short-circuit
the port.

**Every two-port matrix is computed twice**, by cascading the element matrices and by Kron
reduction of the Y-bus, and the two are compared. This is a real check, not decoration: the
line's charging lives inside `F_pi` in one method and on the diagonal of Y in the other, so
counting it twice would give a relative error of ~1e-5, invisible at four decimals in `U_e` but
obvious in this residual. In a meshed network the cascade does not exist and only the Y-bus is
used, with a warning.

Also:

- **Validation before computing**: missing reference bus, unconnected terminals, branches joining
  a bus to itself, transformer loops with incompatible ratios (it says at which bus and because of
  which component), buses without a base, islands, floating buses and decoupled ports. Errors are
  explained in a message, not with a traceback.
- If a study cannot run, the others are still shown and the failed one appears with the reason.
- **Diagram annotations** (`Ctrl+R`): after computing, each bus shows its voltage in kV and in pu
  with angle, and each branch `|I|` and `S`. Any change to the model clears them and marks the
  results as stale.
- **Course presentation**: impedance and power in rectangular form; voltage and current in polar
  form with the angle in degrees, in MATLAB's `format short` style.

What it does **not** do (yet): export results or the diagram (image, PDF, CSV, MATLAB; text can be
selected in the *Log* tab), breakers as a component of their own, load flow with constant-power
loads, off-nominal taps, sequence networks or short circuits.

## Usage

### The window

- **Library** (left): the component palette.
- **Variables** (left): project variables and those of the linked script.
- **Properties** (right): form for the selected element, generated from its fields.
- **Results** (bottom): one tab per study with the headline, the warnings and the tables, plus a
  *Log* tab with the full text. If you change the model, the tabs are marked with ` *` and a
  notice appears over the canvas.
- Menus: *File*, *Edit*, *View* (zoom, annotations, show/hide panels), *Network*
  (*Base settings...*), *Calculate* (*Run all studies* and one submenu per study group) and *Help*.

### Drawing and connecting

Click a component in the *Library* and then on the canvas, or drag it. With `Shift` held the tool
stays active to place several; a right-click deactivates it. Each terminal connects by itself to
the nearest free bus (within a small radius), so a series element dropped between two buses ends
up connected to both, and a load dropped next to a bus attaches to it.

To connect by hand, drag from the terminal (the small circle) to a bus; if you release on empty
canvas a new bus is created there. `Esc` or a right-click cancels the connection. Double-clicking
an element takes you to the *Properties* panel. Elements snap to the grid.

### Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+N` / `Ctrl+O` / `Ctrl+S` | New / open / save (*Save As...* only from the menu) |
| `Ctrl+Z` / `Ctrl+Y` (or `Ctrl+Shift+Z`) | Undo / redo |
| `Del` | Delete the selection; deleting a bus warns and takes along whatever hangs from it |
| `Ctrl+A` | Select all |
| `Ctrl+Alt+R` / `Ctrl+Alt+O` | Rotate 90° / switch the bus between vertical and horizontal |
| `Ctrl` + wheel, `Ctrl++` / `Ctrl+-` | Zoom |
| `Ctrl+0` / `Ctrl+1` | Fit all / actual size |
| Middle button or `Space` + drag | Pan the canvas |
| `F5` | Run all studies |
| `Ctrl+R` | Show or hide the diagram annotations |

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
| `format`, `version` | `"psa-project"` and the format version (1 today; there is a migration chain) |
| `meta` | Title and notes |
| `network` | `S_base`, frequency, reference bus and voltage, buses and components (complex numbers as `[re, im]`) |
| `parameters` | `script_path`, project variables and *bindings* `"COMPONENT.field": "expression"` |
| `layout` | Grid, position of every symbol, and wires |
| `studies` | Buses chosen for each study |
| `view` | Annotation state |

Only `network` is mandatory: a file without `layout` opens with an automatic ladder layout
starting from the reference bus.

## Structure

```
run_gui.py            GUI startup
run_s1_headless.py    console mode
psa/core/     numpy + networkx, no Qt at all (a test checks it in a subprocess)
  components  dataclasses Bus / Source / Transformer / Line / Load / Shunt / Impedance
  network     container, graphs, structural validation
  bases       base propagation by BFS through rated ratios
  perunit     per-unit conversion (where physical units become numbers)
  ybus        Y-bus and Kron reduction, with conditioning guards
  twoport     termination rule, cascade, general method, cross-check
  analysis    equivalent impedance, nodal solve, Thévenin (basis for S2)
  params      project variables, user script, safe-eval, bindings
  study       STUDY_REGISTRY: the extension point for S2 / S3 / LF
  results     result tables; units: `format short` formatting
  serialize   project <-> `.psn` JSON, with a migration chain
  examples    the S1 network parametrized by the A-number
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
  style       colours, pens and style sheet
```

### Adding a study (S2, S3, LF)

Write the function in `psa/core/` and register it with `study.register(Study(key, title,
menu_group, run, option_fields, description))`. That makes it appear by itself in the
*Calculate* menu, in `F5`, in the results tabs and in console mode's `--study`, and the options
dialog is generated from `option_fields`. Today the GUI still has to be touched in three cases:

- the options dialog only knows how to show bus-type fields (`gui/dialogs.py`);
- that dialog's default values live in `MainWindow._fill_defaults`;
- the diagram annotations only know voltages and flows (`SchematicScene.show_overlay`).

## Verification

```
python -m unittest discover -s tests -t .     # 117 tests, ~1 s, the GUI runs offscreen
python run_s1_headless.py                     # prints the full S1 table
python run_s1_headless.py --a-number 0        # the answer published in the assignment
```

| File | Tests | What it covers |
|---|---|---|
| `test_s1_a80.py` | 27 | Bases, pu values, `F_ae`, `det`, cascade vs Y-bus, termination rule, `Z_I`, phasor chain, A sweep |
| `test_serialize_and_validation.py` | 28 | `.psn` round trip, the script is not run on open, automatic layout, validations, meshed network, parallel cables |
| `test_core_units_and_params.py` | 20 | `format short` formatting, safe-eval, bindings, variable script, Qt-free core |
| `test_gui.py` | 40 | Canvas, placement, undo (variables too), parametric and complex fields, connection drag, main window and startup |
| `test_s2_thevenin.py` | 2 | Thévenin at bus 7 of S2 for A = 0 and A = 80 |

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

## Known limitations and roadmap

The full v1 plan is in [`docs/ROADMAP.md`](docs/ROADMAP.md). In short:

**For S2**, the main gap is exposing what the core can already do:

- S2a: a study that shows the Y-bus matrix with the Thévenin of the external grid (`build_ybus`
  already exists).
- S2b: register `analysis.thevenin()`, which is already validated.
- S2c: three-phase short-circuit current and power in kA and MVA.
- S2d: combine the S2b Thévenin with the S1 network and the reduced load.
- A source defined directly by its short-circuit level (kA or MVA and cos φ); today `Z_th` is
  given in Ω, for example with the expression above.
- Study options other than buses (fault type or impedance) in the dialog.

**For S3:** zero-sequence models of transformers (Y0/Δ connections), loads and lines, and the
single line-to-ground fault. **For LF:** Newton-Raphson with slack/PV/PQ buses, constant-power
loads and taps.

**GUI ergonomics:** wires are a single L elbow without editable waypoints and buses have a fixed
length, so a meshed network like the one in S2 is somewhat awkward to draw; the property form is
regenerated after each edit and loses focus; `Esc` does not deactivate the palette tool (use a
right-click); there is no context menu, copy/paste or export; *Save As* and *Exit* have no
shortcut on Windows.
