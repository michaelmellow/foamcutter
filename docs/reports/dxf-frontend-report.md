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
