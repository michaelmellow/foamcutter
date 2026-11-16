# DXF front end report (`foamdxf`)

Not a numbered brief: added on `daniill's-dev-branch` at the owner's request, so
that a panel can be cut from two DXF side profiles, the distance between them
and the stock size. Needs the director's review like any brief.

## 1. What was built

`src/foamdxf/` turns two DXF outlines into a standard job file
(`docs/formats/job-file.md`) and, by default, runs `foamcam` on it. No format
was changed: the job uses `shape.points`, so the CAM, its checks and the
simulator treat it like any other job.

```text
python -m foamdxf ROOT.dxf TIP.dxf --span 450 --stock 280,50 --profile PROFILE.yaml [--out output]
```

Writes `output/ROOT-TIP.yaml` (the job), then `.nc`, `.txt` and `.svg` as
`python -m foamcam cut` does. `--job-only` stops after the job. Other options:
`--speed`, `--kerf-table`, `--direction`, `--lead-in`, `--block-x`,
`--safe-clearance`, `--min-te`, `--segment`, `--simplify`, `--name`.

**Window** (`gui.py`, `python -m foamdxf --gui`). Tkinter only, so no extra
packages. A form (DXFs, machine profile, span, stock, cut settings, output
folder), a Preview button that builds the job and runs the CAM in memory, and a
Generate button that writes the four files. The Preview tab draws, looking along
the wire: the shared travel, the stock, the drawn outlines, the kerf-compensated
wire path at each foam face and both towers' carriage paths; the mouse position
is shown in G54 mm. The 3D tab (`view3d.py`, also plain Tkinter: an orthographic
wireframe) shows the stock between the foam faces, the cut at both faces, both
carriage paths at the tower planes and the surface the wire sweeps; drag to
rotate, right-drag to pan, wheel to zoom. A slider and Play button move the
wire through the program with its elapsed time. The default view is the
operator's: +x into the screen. A test checks the projection is not mirrored.
Under the 3D view a readout shows, for the slider position,
the four axis positions (with their letters), the wire's position at both foam
faces, the phase (entry, lead-in, cutting, exit) and the wire speed at each face.
File > Save project writes a `.foamproj` (JSON, `project.py`): every form value
plus a copy (base64 and SHA-256) of the root DXF, tip DXF and machine profile.
Opening it uses the original files if they are unchanged; otherwise the saved
copies are written to `<project>_files/` next to it and used, with a note. The
window title shows the project and `*` for unsaved changes; closing asks to save.
`python -m foamdxf --gui PROJECT.foamproj` opens a project directly.
The Align stock tab lists the four long stock edges (parallel to
the wire) with the position that puts the wire exactly on each (axes 1 and 3 at
the edge's x, axes 2 and 4 at its z), a Copy G-code button (lift to the safe
height, across, down: three MDI lines) and a Show in 3D button that draws the
wire on that edge. The operator jogs the cold wire there and pushes the stock
against it to place and square it. Positions are the wire's centre.
The Report tab shows the placement summary and foamcam's
report, or the error with every reason. Last values are kept in
`~/.foamdxf-gui.json`. The CLI and the window share `pipeline.py`.

**Reading a DXF** (`outline.py`). Lines, arcs, circles, ellipses, polylines and
splines, also inside block references, flattened to 0.01 mm and joined end to
end (gaps up to 0.1 mm). Units from `$INSUNITS` (mm, cm, m, inch, foot;
unitless is taken as mm). An open trailing edge (gap up to 5 % of the chord) is
closed with a straight line and a warning. If there are several closed
outlines the largest is used and the others are reported (spar slots are not
turned into features). The outline is put in Selig order: trailing edge = the
point with the largest x, counter-clockwise from there runs over the upper side,
and for a blunt trailing edge it starts at the upper corner so the trailing-edge
face is the closing edge. Points are then thinned (Ramer-Douglas-Peucker,
0.01 mm, leading edge and both trailing-edge points kept), because dense CAD
points make very short moves.

**Trailing edge.** A trailing edge smaller than 0.3 mm (CAD airfoils often end
in a tiny arc) is merged into one sharp point, and the job's `min_te_thickness`
defaults to 0.6 mm (as in the worked examples), so §6.2 opens it into a 0.6 mm
face. Found with the owner's first real DXFs: a 0.05 mm trailing-edge arc made a
stretch far shorter than the kerf, and foamcam's kerf iteration (§10.7 step 6)
oscillated and stopped after 10 rounds. A sharp trailing edge without the 0.6 mm
face trips the spike guard (§10.7 step 8) instead. Use `--min-te 0` only for a
drawing that already has a trailing-edge face. Open question for the director:
should foamcam itself handle stretches much shorter than the kerf (for example by
giving a stretch shorter than the kerf the kerf of its neighbours)?

**Drawing convention.** x along the chord, leading edge on the left (smaller x),
y up. Both DXFs share one coordinate frame, so sweep, dihedral and twist are
what the drawings show.

**Placement** (`jobgen.py`):

- Section planes centred between the towers: `root.y = (wire_span − span) / 2`,
  `tip.y = root.y + span`. Root nearer the left tower (`mirror` in the job for the
  other hand).
- The bounding box of both outlines together is centred in the stock (length
  along x, height along z); the stock rests on the table at `z = table_z`.
- Stock back face x: by default the stock plus the lead-in is centred in the
  travel shared by axes 1 and 3; `--block-x` overrides.
- Each section: `shape.points` in chord fractions with the leading edge at
  (0, 0), `chord` = x extent, `twist = 0`, `twist_axis = 0`, `position` = placed
  leading edge. With these, job-file.md §6.1 to §6.5 reproduce the drawn outline
  exactly (`min_te_thickness` defaults to 0 for that reason).
- `safe_position`: all four axes at the entry x, `safe_clearance` (20 mm) above
  the stock.

**Project wiring.** `dxf` dependency group in `pyproject.toml` (`ezdxf>=1.1`,
`PyYAML>=6`), `dxf` area in `check.py`, CI installs the group. `tests/dxf/`
skips when the group is not installed, like `tests/step/`.

## 2. What was verified, and how

- `python check.py dxf` passes (ruff, mypy strict, 41 tests, including a real Tk window running Preview and Generate) on Python 3.12.9,
  Windows. The packages were not installed: run with `PYTHONPATH=src`.
- Tests cover: Selig order and leading edge; clockwise drawings; open trailing
  edge; splines and lines in pieces, drawn in mixed directions, with a blunt
  trailing edge; inch drawings; inner loops; open and empty drawings; thinning
  stays within tolerance; **the job reproduces the drawn outlines through
  foamcam's own `build_outline` and `place` to 1e-5 mm**, with the same shift for
  root and tip; centring in the stock and between the towers; `--block-x`;
  stock too small; span too long; the generated job cuts; the CLI writes all four
  files and reports errors.
- End to end at a 690 mm wire span (copy of the sample profile with
  `wire_span: 690`): 220 mm root of fit-point splines plus a trailing-edge line,
  150 mm tip swept 35 mm as one open polyline, span 450, stock 280 × 50. foamcam
  accepted it: wire angle 4.5°, speed ratio 1.46, 568 feed moves, 4.15 min.
- `python check.py cam` and full `ruff check .` still pass. The full test run has
  the same 21 failures as before this change (section 3).

## 3. What was NOT verified

- **The simulator.** foamsim rejects every profile with `table_z` (19 failures
  that predate this work, `brief-3-table-z-report.md` §3), so no foamdxf job has
  been checked by foamsim.
- **Real CAD files.** Tested with DXFs written by ezdxf only. Files from the
  owner's CAD program (layers, splines with weights, polylines with bulges,
  3D entities) have not been tried.
- **Short moves.** Thinning removes dense DXF points, but foamcam's breakpoint
  rule (job-file.md §10.3) still adds a breakpoint at every vertex of either
  outline, giving moves of a few milliseconds. The director accepted short moves
  in Brief 1; whether ioSender streams them fast enough is still a bench-test item.
- Nothing has been cut or run on the machine.

## 4. Open questions

1. **Spar slots.** Inner loops in a DXF are ignored. Should they become job
   features (`kind: "hole"`), matched between root and tip by position, with a
   slit side to choose?
2. **Placement.** Centred in the stock and between the towers. Is a fixed
   reference (for example the stock's back-bottom corner = DXF origin) wanted as
   an option?
3. **Root on the left.** The root plane is always nearer the left tower; the
   other hand uses `mirror`. Enough?
4. **Thinning tolerance** 0.01 mm (plus 0.01 mm flattening). Acceptable against
   the 0.05 mm simulator tolerance?
5. The 21 pre-existing failures (foamsim and `table_z`; `firmware/test_docs.py`
   reading UTF-8 files with the Windows default encoding) need their owners.

## 5. Change to foamcam: rapids move one way at a time (owner's request)

The owner asked that the approach to the lead-in never cut diagonally: lift
first, then go across. Before, a program went `G0 <safe_position>` from wherever
the wire was (from home: up and across at once), and from the safe position
straight to the entry point (diagonal whenever the safe position is not above
the entry point).

`src/foamcam/cam.py` now writes:

1. `G0 <axis 2> <axis 4>`: lift both vertical axes to the safe height where the
   wire is. Only the two vertical axis words, because the program does not know
   the horizontal positions when it starts.
2. `G0 <safe_position>`: across at the safe height.
3. `G0` above the entry point (entry x, safe height), if that differs from the
   safe position: across.
4. `G0 <entry point>`: straight down. Then the cut as before.
5. After the exit: straight up to above the entry point, then across to the
   safe position (step 3 in reverse, skipped if the safe position is above the
   entry point).

The §10.6 block check now covers every rapid between the safe position and the
entry point. If the wire starts above the safe height, step 1 lowers it in
place: the operator still starts the program with the wire clear of the foam.

Tests: `tests/cam/test_units.py::test_rapids_never_move_diagonally` (both
cases). Three helpers and two tests that assumed four axis words on every `G0`,
or the entry point on the second `G0`, now recognise the opening lift (they
assert it is the first motion line with exactly the two vertical axes) and
otherwise check what they checked before. `examples/gcode/*.nc` regenerated as
`test_worked_examples.py` describes: 3 lines added to each, no other change.
`python check.py cam` passes (71 tests).

**Needs the director's approval: the code now differs from the approved formats.**
`docs/formats/` was not edited. Proposed changes:

- job-file.md §10.6, replace the first paragraph with: "The program starts by
  lifting axes 2 and 4 to the safe position's values, with no other axis word.
  It then moves by rapids to `safe_position`, to the point above the entry
  points at the safe position's axis 2 and 4 values (left out if equal to
  `safe_position`), and down to the entry points. It ends with a rapid up to
  that point above the entry points (if not left out) and a rapid to
  `safe_position`. No rapid moves a horizontal and a vertical axis together,
  except where the safe position itself is not level. The CAM rejects the job
  if any of these rapids, other than the first, would pass through the block at
  any plane between the two foam faces."
- job-file.md §13: "`G0 <a1> <a2> <a3> <a4>`: rapid move. All four axis words,
  except the first motion line, which is `G0 <a2> <a4>`" and the program order
  block to match.
- **Simulator (Brief 5).** foamsim requires the first motion line to set all
  four axes, so it will reject the opening lift once its `table_z` failure is
  fixed. It needs to accept a first `G0` with only axes 2 and 4 (no cut happens
  before the first full position).

## 6. STEP models as an alternative to two DXFs (owner's request)

The DXF workflow is unchanged. As a second source, the window (Files > Source:
STEP model) and the CLI (`--step MODEL [--faces ROOT,TIP] [--list-faces]`) take
a STEP model whose root and tip are **flat end faces**:

- `src/foamdxf/step.py` reads the model with gmsh (OpenCASCADE, `pip install
  gmsh`, optional group `dxf-step`) and lists every flat face. The suggested
  pair is the parallel flat faces farthest apart (weighted by the smaller
  face's area); the larger is the root. The operator can pick any two.
- The span is the distance between the two faces; the span field becomes
  read-only and shows it.
- Both faces are mapped into one 2D frame: x along the root chord with the
  leading edge (the thicker end) at the smaller x, z up, the model moved
  rigidly (never mirrored), so sweep, dihedral and twist come from the model.
  If the upper side would face down with the picked root at the axes 1-2
  tower, the model is turned over (180° about the chord) and the root is cut at
  the axes 3-4 tower instead; a warning says so. Which happens depends only on
  the wing's hand.
- The outline steps after that (joining, Selig order, trailing-edge handling,
  simplify) are the DXF ones (`outline.outline_from_pieces`).
- Projects store the STEP file like the DXFs (`project.FILE_KEYS`).

Not covered: wings without flat end faces (rounded tips), sections cut at
arbitrary stations, panel splitting and deviation reports. Those are Brief 4
(`src/foamstep`), which this does not replace.

Tests: `tests/dxf/test_step.py` builds NACA 2412 wings with gmsh in code and
checks chords, sweep, span, rigidity (handedness and distances) in four
placements, bad face choices, the CLI, the window and project restore. Skipped
when gmsh is not installed.

Also in this change: the Section tab's horizontal axis is inverted (+x, away
from the operator, runs right to left, as seen from the X/Y side). The 3D tab
is unchanged.

## 7. Inner contours cut through a channel (owner's request)

Closed contours inside the outline (a circular carbon-spar hole, a rectangular
slot, lightening holes) are no longer ignored. They become `kind: "hole"`
features (job-file.md §8), which the CAM already cuts: in along a straight
channel (slit) from the outer contour, once round the hole, back out the same
channel. No change to foamcam or to the formats.

- `outline.outline_from_pieces` keeps every loop inside the outline as a hole;
  a loop inside a hole (a tube drawn with both diameters) is ignored with a
  warning, so the hole is the outer diameter. STEP faces get holes the same way.
- `holes.py`: root and tip need the same number of holes; they are paired by
  position. Both loops are re-sampled to the same vertex count (§8), piece by
  piece between matching corners: straight sides stay one move each (short
  pieces at corners make the kerf iteration oscillate), curves get sides of
  about `max_segment_length`, circles at least 16. The first point is the top
  of the hole for an upper channel, the bottom for a lower one.
- Channel side: form field "Hole channel from" / `--channel`, `shortest`
  (default, per hole the shorter channel at root and tip together), `upper` or
  `lower`.
- The preview draws the holes; the summary lists each hole's channel.
- Examples: `examples/dxf/spar-root.dxf`, `spar-tip.dxf` (the dry-run wing with
  a 6 mm tube).

Limit found: holes whose radius is close to the kerf (about 2 mm radius or
less with the sample kerf table) make the kerf compensation fail to settle,
and the CAM reports it. Real spar holes (6 mm and up) are fine.

The channel leaves a kerf-wide cut in the part from the surface to the hole;
glue fills it when the spar goes in.

## 8. Built-in sender, stage 1: Machine tab (owner's request)

The window can send the cut to grblHAL itself, with no file saving and
picking. Code: `src/foamdxf/sender.py` (protocol, no Tk) and
`src/foamdxf/machine.py` (the tab). New dependency: `pyserial` (dxf group).

- **Connect** to a serial port. The board's STM32 virtual COM port is picked
  by default. Before the first status report the tab says "Connecting…".
  After 3 s with no answer it reports "no answer" and releases the port.
- **Live DRO**: polls `?` five times a second. It shows the work position
  (MPos − WCO; the last WCO is kept, because grblHAL sends it only now and
  then), the machine position, the state badge, feed, free planner blocks and
  rx bytes, and active input pins. Axes are shown in the controller's order,
  X Y Z A, each labelled with the tower axis the profile drives with it.
- **Streaming** uses character counting against the receive buffer size
  read from `$I` `[OPT:…]` (1024 on the as-flashed board; Grbl's 128 until
  it is read). Comments and blank lines are dropped.
- **Program**: the last Preview (refused if the form changed since then), or
  any `.nc` file. Start needs state Idle and asks for confirmation (stock,
  work zero, wire heat on, path clear).
- **Hold / Resume**: `!` and `~`. **Stop**: hold, then soft reset once a
  fresh status report says `Hold:0` (or after 10 s). **Unlock**: `$X`.
- **On the first `error:`** nothing more is sent and the machine is held.
  Resume is disabled, because grblHAL would otherwise run the following lines
  with one move missing. The operator presses Stop. **ALARM** ends the program.
- Closing the window, or Disconnect, during a cut asks first, then stops and
  releases the port.

**ioSender at the same time:** Windows lets one program open a COM port at a
time. While ioSender is connected, Connect fails with "COM6 is in use by
another program. Disconnect ioSender (or close it) first." This was seen on
this PC, with ioSender holding the board's port.

Verified: `tests/dxf/test_sender.py` runs against a fake controller that
fails the test if its receive buffer is ever overfilled. It covers:
- a whole program streamed in order;
- error → hold, nothing more sent, Resume refused, Stop resets;
- the reset comes only after Hold:0;
- hold and resume;
- alarm;
- Start refused unless Idle;
- no answer;
- the port-in-use message;
- the window test: Preview → Connect → Start → finished → stale preview
  blocks Start → Disconnect.

NOT verified: anything on the real board. Not yet tested on the board: that
reset after Hold:0 keeps position without ALARM (documented Grbl behaviour),
the real status fields with `$10=511`, and streaming speed with G93 short
moves. First real test: wire cold, no foam, a dry-run program.

Not built (stages 2 and 3): jogging, homing macro, MDI, go-to-edge from the
Align tab, overrides. Wire heat is switched by hand: the controller does not
control it (CLAUDE.md), so the app cannot switch it either.

### 8.1 Additions: speeds in the DRO, live section, feed override, dry runs

- **DRO feed and actual cutting speed.** The DRO shows two things:
  - **Controller feed**: the `FS:` field, the rate the controller reports.
  - **Wire speed**: how fast the wire is really moving at the root face, at
    the tip face, and at each tower. It comes from the position change
    between two status reports, about 0.2 s apart. A face point is the
    point on the straight wire at that face's fraction of the tower distance.
  - The face speeds need a current Preview to know where the faces are; with
    a picked .nc file only the tower speeds are shown. Code:
    `machine.wire_speeds`.
- **Live section.** While connected, the Section tab draws both carriages
  (squares), the wire between them, and where the wire is at the root and tip
  faces (dots). A test checks that the preview's carriage positions are the
  G-code's coordinates, so the dots sit on the cut paths. This holds only
  when the machine's work zero is the one the program was made for.
- **Feed override 50-200 %** with a slider and a "100 %" button. It uses the
  grblHAL realtime bytes 0x90-0x94, steps of 10 then 1, so it never passes
  through other values. The slider follows the `Ov:` field the controller
  reports, for example back to 100 % after a reset. grblHAL itself allows
  10-200 %. The kerf table is speed-dependent, so a different override
  changes the kerf; the tab says so.
- **Dry run at ×2, ×3, ×5 or ×10.**
  - What it changes: every F word in the program is multiplied, which makes
    the cut that many times faster in both G93 (F = 1/min) and G94.
  - What it leaves alone: rapids, and the controller's per-axis max rates,
    which still apply.
  - What it looks like: Start reads "Start DRY RUN", and the confirmation
    asks for wire heat OFF and no foam.
  - It is off every time the window opens, so a real cut is never sent fast
    by mistake.
  - It goes beyond 200 % because the program itself is rewritten; the override
    stays the controller's.

Verified by tests (`tests/dxf/test_sender.py`):
- the override bytes and clamping, through the fake controller;
- feed scaling, with comments and G0 untouched;
- tower mapping for the real profile's letters (axis 1 = Z);
- face speed arithmetic;
- a window test: dry run ×5 sends exactly the scaled program, the speed line
  appears while moving, live markers appear and are removed on disconnect,
  and the override slider reaches the controller.

NOT verified on the board: whether grblHAL applies the feed override to G93
moves as expected (Grbl applies it to all feed moves; not checked in the
source), and what `FS:` reports during G93 moves.

### 8.2 Stage 2: jog, homing macro, MDI, go to an edge; section beside the console

**Layout of the Machine tab**, from top to bottom:
- port bar;
- DRO;
- a run bar that is always visible: Start / Hold / Resume / Stop / Unlock
  and the progress bar;
- a **controls** notebook: Program, Jog, Home;
- a **views** notebook: Section and Console. The two notebooks are split by
  a draggable divider.
The Section view moved here from the main tabs, so the live position is
visible while running. It now fits the stock and cut paths, not the whole
travel, and its legend is laid out in two columns.

- **Jog** sends `$J=G91 G21 … F…` jogs. The feed and the step (0.1-50 mm,
  editable) are set in the tab. "Stop jog" sends the jog cancel byte (0x85).
  - Jog either tower's horizontal or vertical axis, or both towers together
    (a straight wire stays straight).
  - **Keep inside the profile's travel** (on by default): the jog is
    shortened so no axis leaves the profile's `min`/`max`. These are G54
    positions, per the profile header. All axes are shortened by the same
    factor, so a jog of both towers keeps the wire as it was.
  - Without homing, these limits are only as good as the current zero.
- **Home**: the owner's homing macro from `foamcutter-handoff.md` §1,
  editable, with "Restore default". It runs like a program (character
  counting, stop on error, Hold/Stop work). The confirmation lists the
  handoff's warnings: table clear, wire cold, the verticals go down, under
  300 mm from each stop.
- **MDI** line under the console: Enter sends, Up/Down recall the last 50
  commands. Refused while a program runs.
- **Align stock → Go there**: sends that edge's three lines (lift, across,
  down) through the Machine tab, after a wire-cold, no-stock confirmation.
- The homing macro, jog step/feed/limit and MDI history are kept in
  `~/.foamdxf-machine.json`, not in projects.

Verified by tests:
- jog clamping, including the same factor for both towers;
- jog and jog cancel reach the fake controller;
- a window test of jog (shortened at the stops), MDI and history, the homing
  macro streamed in full, go-to-edge sending the edge's lines, the section
  view inside the Machine tab, and the settings saved on disconnect.
- `test_gui.py`'s align-tab test now also expects the "Go there" button.

NOT verified on the board:
- that `M906` (Trinamic plugin) is accepted in a streamed program;
- jog cancel behaviour;
- the macro itself: see the handoff's list of things to check, in particular
  the verticals homing down and the 300 mm seek.

### 8.3 Section view: zoom, pan, and moving the wire with the mouse

- **Mouse wheel** zooms about the cursor (×1.2 per notch, 0.2-50×).
  **Right-drag** pans. **Fit view** resets both.
- **"Mouse moves the wire"** (a checkbox above the section, off at every
  start) enables the moves below. They are also refused unless the machine
  is connected, Idle or jogging, and no program is running.
- **Left click**: both towers go to the clicked (x, z), so the wire is
  straight there. It sends an absolute jog `$J=G90 G21 Z.. A.. X.. Y.. F<jog
  feed>`, kept inside the profile travel when the Jog tab's box is ticked.
  Stop jog on the Jog tab cancels it.
- **Hold X or Z + click**: only the tower whose horizontal axis has that
  letter moves (X → the X/Y tower, Z → the Z/A tower, read from the profile).
  The keys are tracked window-wide. The canvas takes the keyboard focus when
  the mouse enters it with moves enabled, so a held X is not typed into the
  MDI line.
- **Drag**: the towers follow the mouse. The newest target is sent only once
  three things are true:
  - the last jog was answered;
  - a status report has arrived since then;
  - the planner (`Bf:`, size from `$I`) holds at most the running block.
  So moves do not pile up behind the mouse. Releasing the button sends the
  final point. Status polling runs at 10 Hz during a drag, otherwise 5 Hz.
- A cross marks the last target: red for both towers, the tower's colour for
  one.

Verified by tests:
- absolute-jog clamping and tower targets;
- zoom keeps the point under the cursor, and panning;
- a click is ignored until moves are enabled;
- a click sends both towers;
- X held sends only X/Y;
- a drag sends several but far fewer jogs than mouse events, ends on the
  release point, and restores the polling rate.

NOT verified on the board: how smooth following feels with real jog
acceleration (Grbl stops at the end of each jog block unless the next is
queued in time).
- **Mouse speed slider** above the section: from 50 mm/min up to the
  controller's rapid rate. It sets the feed of click and drag moves only;
  the Jog tab keeps its own feed.
  - The rapid rate is the slowest of `$110`-`$113`, read with a `$$` the
    sender sends at connect. Its output is kept out of the console; a `$$`
    typed in MDI is still shown.
  - At the top the label reads "RAPID … mm/min".
  - Until connected, the slider tops out at 3000 and says so.
  - The setting is remembered in `~/.foamdxf-machine.json`.
  - Tests: max rates parsed quietly (the slowest axis wins), the slider top
    follows the controller, the move is sent at F = rapid, and the setting
    is saved.

## 9. Kerf calibration tab (owner's request)

A **Kerf** tab in the Machine tab's controls, beside Program, Jog and Home,
so the DRO, Stop and the clickable section view stay in sight. Logic is in
`src/foamdxf/kerfcal.py`, the tab in `src/foamdxf/kerftab.py`. The existing
`foamcam/kerftest.py`, a fixed program that rapids back out through each
slot, is unchanged.

Decided with the owner:

- **Speeds**: from target ÷ `max_speed_ratio` (the slowest the slower foam
  face goes in a cut at that target) to target × 1.2, spaced evenly on a log
  scale, rounded to whole mm/min. With target 150 and 5 slots: 50, 69, 95,
  131, 180. The kerf lookup refuses speeds outside its table, so the table
  has to cover that range. The list is editable. Start refuses a speed
  above the profile's `max_rate` for the cut axes.
- **Cut direction**: XZ+ / XZ− move both horizontal axes together (away
  from / toward the operator); YA+ / YA− move both vertical axes (up / down).
  The wire stays straight. Labels come from the profile's letters.
  - **Next slot**: up or down after a horizontal slot, away or toward after
    a vertical one, by the spacing.
- **Sequence**, pausing at every stage. Each slot is a set of relative
  programs, so jogging, dragging on the section, or swapping the scrap block
  between slots simply moves the next start:
  1. **Cut**. Before it: the wire must be straight (asks if not), the slot
     must stay inside the travel, and the feed override is set back to
     100 %. The confirmation asks for heat ON. The program is `G91 G94
     G1 <both axes> F<speed>`, depth counted from where the wire starts.
  2. When the motion has finished, a dialog asks to switch the heat OFF and
     wait until the wire is cold. Cancel leaves a "Pull out (wire cold)"
     button.
  3. **Pull out** cold straight back along the slot, at the pull-out speed
     (200 mm/min by default), then step to the next start. There is no step
     after the last slot. The hot wire never runs back through a slot.
  - A stopped cut leaves that slot to cut again or Skip. The wire has to be
    taken out by jogging: its depth is unknown.
- **Results**: slot widths at face 1 and face 2, averaged. If the faces
  differ by more than 0.05 mm, the slot is flagged (first-cut-plan cut 1).
  **Save to profile** appends a named table to `kerf_tables` as text:
  - every comment and line stays as it was, and the file's own line endings
    are kept;
  - the result is checked by loading it, and a duplicate name is refused;
  - a `.bak` copy is written first;
  - the new table is selected for the next Preview.

**Two fixes found on the way** (they affect the whole Machine tab):

- **"Finished" now waits for the motion.** grblHAL answers `ok` once a line
  is planned, not once it has moved. A program was reported finished while
  its last moves were still running. That would have asked for heat OFF
  mid-slot, and it also showed "switch the heat off" early after a normal
  cut. Now `done` needs every line answered and then a status report saying
  Idle with an empty planner (`Bf` free = planner size from `$I`). Until
  then the program counts as running, so Stop still works and other
  commands are refused.
- **Axis letters without a Preview** now come from the profile (Z A X Y on
  the real machine), not X Y Z A. Jog and Kerf drove the wrong letters
  before a Preview.

Verified by tests:
- `tests/dxf/test_kerf.py`: speeds, parsing, slot and pull-out programs,
  straight-wire and travel checks, face averaging, and appending a table
  (comments kept, also when another key follows, duplicates refused);
- `test_sender.py`: done waits for Idle with an empty planner;
- a window test: 3 slots against a fake controller that tracks positions.
  It checks:
  - the override is reset to 100 %;
  - each slot is cut, then a heat-off pause, a cold pull-out along the slot,
    and a step up;
  - the wire ends back at the first start, two spacings higher;
  - widths are entered and averaged, and the table is saved, loadable, with
    a `.bak` and selected.

NOT verified: anything on the machine, in particular how long "done" takes
after the last `ok` on the real board (status every 0.2 s).
