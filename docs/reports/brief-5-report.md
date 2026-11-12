# Brief 5 report: simulator and independent check

Branch `brief-5-simulator`. Package `src/foamsim/`, tests `tests/sim/`, plan
`docs/first-cut-plan.md`.

## Summary

- The simulator is built and `python check.py sim` passes (ruff, mypy strict,
  97 tests passed, 3 expected failures; see below).
- **Acceptance criterion 1 is not met.** All three Brief 3 worked examples
  pass every check except deviation. Their maximum deviations are 0.107,
  0.144 and 0.109 mm, all above the 0.05 mm limit. The excess comes from two
  rules in the CAM method itself (job file §10.3 and §10.7 step 1), not from
  a CAM coding error and not from the simulator (evidence below). The
  director needs to decide what to do (open questions 1 and 2).
- **Acceptance criterion 2 is met.** Scaling any one axis by 1 % in any
  example fails, and the report names the affected section. 12 of 12 cases.
- Independence. I did not open, search, run or import anything in
  `src/foamcam/` or `tests/cam/`, and did not read
  `docs/reports/brief-3-report.md` or the Brief 3 brief. My only sources on
  the CAM were `docs/formats/machine-profile.md` and
  `docs/formats/job-file.md`. Two things I saw by accident: the file names in
  `src/foamcam/` (in a directory listing) and the first 20 lines of
  `examples/gcode/straight-wing.nc` (before the examples step). Neither
  affected the design.

## What was built

`python -m foamsim PROGRAM.nc --job JOB.yaml --profile PROFILE.yaml [--plane Y ...] [--tolerance 0.05] [--out output/sim] [--no-plots]`

Exit code 0 means pass, 1 fail, 2 unusable input. It writes
`<name>-report.json` (machine-readable), `<name>-overlay.png` (2D target
against simulated sections, plus deviation along the contour) and
`<name>-cut.gif` (3D animation of the wire through the block), and prints a
text summary.

| Brief task | Module | How |
| --- | --- | --- |
| 1. Parse the subset, reject anything else | `gcode.py` | Accepts G0 G1 G4 G21 G54 G90 G91 G93 G94, the profile's four axis letters, F, P (only with G4) and whole-line `( )` comments. Everything else is an error naming the line: M/N/S/T words, other G codes, inline or nested comments, exponents, repeated words, two motion codes on one line, F missing on a G93 G1, motion before G21/G54/G90 are set, a first move that is not a G0 with all four axes. |
| 2. Wire line in 3D | `wire.py` | L = (a1, 0, a2), R = (a3, span, a4) (profile §3.3). The trace in a plane y = c is P(c) = L + (c/span)(R − L), and it is linear within each move. |
| 3. Intersect with root, tip and operator planes | `sim.py` | Traces at both section planes (mirrored when `mirror: true`) and at every `--plane`. |
| 4. Remove kerf, compare | `compare.py` | See "Comparison model". Maximum deviation per section and per element (outer, each hole, each open cut), with the worst point. |
| 5. Checks | `sim.py` | Soft limits on every motion end. Wire angle. Axis rate. Foam-face speed ratio (lead-in and exit exempt and reported, job §10.5). Faster-face speed = `cut_speed` (job §3). Wire outside the block at start of cut, end of cut and end of program. Rapids clear of the block. Lead-in and exit not through the part. Kerf lookups inside the table. Face speeds and times reported. |
| 6. Animation and overlay | `plots.py` | matplotlib, no display needed. |
| 7. Pass/fail plus report | `sim.py`, `cli.py` | `SimResult.passed` and `to_dict()`, written as JSON. |
| Inputs | `profile.py`, `job.py`, `dat.py`, `target.py` | Own implementation of profile §4–§8 and job §1–§9, including the target geometry §6–§8 (reversal, TE thickening, twist, placement, slits). Every violation is reported with its field path. |

### Comparison model (task 4)

In a section plane the wire centre moves along the trace. One G1 makes one
edge. That edge melts a slot of full width k, where k is the kerf-table value
at the wire's real speed *in that plane during that move* (edge length divided
by the move time from F). The surface the edge leaves is the edge moved by
k/2 toward the material.

- Each edge of the cut (all G1 except the first and last, which are the
  lead-in and exit, job §13) is assigned to the target element it lies on:
  the outer contour (material inside), a hole (material outside), an open cut
  (not offset) or a slit (cut, but not compared; job §8). It goes to the
  element its five sample points lie closest to. For a slit the test also
  runs the other way, because one move cuts a whole slit. This uses only the
  job's target geometry and nothing from job §10–§12.
- The moved edges of each element are chained in program order and joined:
  - where they cross, at the crossing (the material keeps that corner);
  - where they stop short at a corner turning toward the material, at the
    line intersection. The real surface there is an arc of radius k/2, and
    the distance is reported as `inside_corner_rounding`, not as deviation;
  - where they meet nowhere near the edges (nearly parallel edges with
    different kerfs), with a straight step.
- Deviation is the symmetric Hausdorff distance between this surface and the
  target polygon or polyline. Both are sampled at 0.01 mm or finer, so the
  value is exact to 0.005 mm. A target with no cut at all scores infinity.
  The worst point is reported either as "target not reached" (the part
  misses target material there) or "surface off target".

## What was verified, and how

1. **Hand-written programs (tests/sim, 82 tests outside test_examples.py).** Each expected number is
   worked out by hand in a comment next to the program (`conftest.py`,
   `test_simulate.py`, `test_features.py`):
   - straight and tapered rectangles: deviation 0 (< 1e-6). The tower
     positions were projected by hand. Feed times 3.83 and 4.83 min. Face
     speeds 100 / 50.495 / 52.381 mm/min. Lead-in ratio 1.7194.
   - kerf removal: uncompensated program 0.70711 (= 0.5√2); kerf interpolated
     to 1.2 gives 0.14142; kerf outside the table gives the exact profile §7
     message.
   - a step between nearly parallel edges with kerfs 1.4 and 1.0: 1.2 mm.
   - 1 % on axis 1 gives root 1.86075 and tip 0.20675; on axis 4, tip 0.3645
     and root 0.0405, so only the tip fails.
   - hole and open cut: deviation 0, hole corner rounding 0.20711
     (= 0.5√2 − 0.5); an unoffset hole gives 0.70711; an open cut 2 mm short
     gives 2.0; a missing hole gives infinity.
   - each check fails on a program built to break it, and names the line,
     axis and amount (soft limit 50.000, wire angle 3.647°, axis rate 2000.0,
     speed-ratio lines 9 and 11 only, cut speed, start inside the block, a
     rapid through the block, a lead-in through the part).
   - mirror, an operator plane at y = 500, G91, G4, G94, compact words, and
     18 kinds of rejected line.
   - profile and job validation, Selig and Lednicer `.dat`, §6 reversal,
     de-duplication, TE thickening and twist.
2. **Worked examples (`tests/sim/test_examples.py`).** All checks pass. Every
   one-axis 1 % corruption fails and names the right section. The < 0.05 mm
   criterion is recorded as `xfail(strict=True)` (see open question 3).
3. `python check.py sim`: ruff clean, mypy strict clean, 97 passed,
   3 xfailed. The full `python check.py` was not run, as instructed.

## Worked examples: results

Sample profile, tolerance 0.05 mm.

| Example | Root deviation | Tip deviation | Checks | Face speeds root / tip (mm/min) | Feed time | Result |
| --- | --- | --- | --- | --- | --- | --- |
| straight-wing | 0.1073 (target not reached, (120.462, 18.877)) | 0.1073 (same) | all pass | 150–150 / 150–150 | 3.263 min | FAIL |
| tapered-spar | 0.1437 (target not reached, (120.508, 18.765)); spar hole 0.0000 | 0.0855 (surface off target, (141.145, 19.422)); spar hole 0.0000 | all pass | 140.8–150 / 97.9–150 | 4.498 min | FAIL |
| tapered-swept-washout | 0.0494 (passes, by 0.0006) | 0.1094 (surface off target, (329.215, 27.422)) | all pass | 138.1–150 / 93.7–150 | 4.227 min | FAIL |

Other figures:

- The lead-in/exit speed ratios are 1.000, 2.239 and 1.763 (exempt from the
  limit).
- The rapids estimate is about 0.3 min per program.
- The spar slot corners will be rounded: the sharp corner is 0.212 mm from
  the real arc surface.
- Every program ends in G94.
- Every program starts with a G0 from an unknown position (warning only: the
  operator must check that path, job §10.6).

**First run, and one simulator fix.** On the first run the tapered tips
showed 3.3378 mm and 1.5205 mm. The cause was in the simulator. Where two
nearly parallel moved edges had different kerfs, it joined them at their line
intersection, which lay several mm away, and drew a spike that no wire could
cut. I wrote a hand-calculated test for that geometry
(`test_kerf_step_between_nearly_parallel_edges`). It failed on the old code
with 1.1001 against the correct 1.2. I then changed the joining rule (see
"Comparison model"). No other change was made after running the examples,
and none was aimed at making them pass: they still fail. The straight-wing,
spar-root and swept-root values did not change with the fix.

### Which side is wrong: the CAM method, not the simulator

There are two separate effects. Both follow from job §10 as written.

**A. Resampling skips the `.dat` vertices (job §10.3).** This affects the
straight-wing root and tip (0.107), the spar root (0.144) and the swept root
(0.049).

- The target is the polygon through every `.dat` point (job §6.6).
- At these faces, all 704 to 893 vertices of the simulated surface lie within
  0.0001 mm of the target polygon. So the kerf offset and the tower
  projection are right.
- But a few target vertices near the leading edge are not on the surface.
  §10.3 places points at equal arc length between fixed points (S, LE, far
  TE, slit points), so the cut is a chain of chords inscribed in the target.
  It cuts across any `.dat` vertex that is not a sample point.
- Check by hand for straight-wing vertex 32 at (120.462, 18.877). It lies
  between surface vertices (120.3794, 19.0782) and (121.1360, 18.4858). The
  chord direction is (0.7566, −0.5924)/0.9610. The vertex offset from the
  chord start is (0.0826, −0.2009). Cross product: −0.1031; divided by
  0.9610: **0.1073 mm**, which is exactly the reported deviation.
- E205 and Clark Y have `.dat` points about 1 mm apart, with sharp turns
  between them at the leading edge, so the chord sag there is 0.05 to
  0.15 mm at these chords.
- Effect on the part: it is up to about 0.14 mm undersize at those vertices.

**B. Kerf taken from the uncompensated speed (job §10.7 step 1).** This
affects the spar tip (0.086) and the swept tip (0.109).

- Away from corners (more than 5 mm from any fixed point), the tip edges
  agree three ways, to 4 decimals, for every piece. The kerf from the real
  G-code speed, the kerf implied by the wire's offset, and the kerf at the
  uncompensated slower-face speed 150 × L_tip/L_root are all equal. For the
  spar, piece S→LE: 102.3 mm/min → 1.2477 mm; piece LE→slit:
  98.5 → 1.2796; piece slit→TE: 103.8 → 1.2352. For the swept panel:
  93.7 → 1.3188.
- Near the leading edge and the trailing-edge corners, the offset path is
  relatively longer at the small tip than at the root. The real tip speed
  rises to about 119 mm/min, which gives a kerf of about 1.107 mm. The CAM
  still offset for the uncompensated speed.
- Spar tip, line 242: offset for 1.2796, real kerf 1.1067. Half the
  difference is 0.0864, against a measured deviation of 0.0855.
- Swept tip, line 506 at the trailing edge: offset for 1.3188, real kerf
  1.1020. Half the difference is 0.1084, against 0.1094.
- Effect on the part: oversize by that amount at the tip leading and trailing
  edges.
- §10.7 calls this difference "slight" and "accepted and not iterated". With
  the sample kerf table's slope (0.5 mm per 60 mm/min) it is not slight.

I cannot see the CAM code, so I cannot say whether it also has a coding
error. Everything the G-code shows is consistent with §10 as written. (A
cruder estimate that projects wire points onto the target gave mixed results
and was discarded.)

**Why the simulator is believed right here.** It reproduces the hand-checked
sag value exactly. It finds the surface on the target to 0.0001 mm wherever
neither effect applies. Its kerf model (real speed in each plane) is the
definition in profile §7: "speed of the wire through the foam". It passes
every hand-written case, including ones where its answer differs from a
naive model.

## What was NOT verified

- **grblHAL behaviour.** Not checked against grblHAL source or
  documentation in this session:
  - that G0 moves in a straight line in axis space (the rapids check assumes
    it and tests 201 planes between the foam faces, not every plane);
  - that `G4 P` is in seconds;
  - that G94 feed applies to the combined length of all four axes;
  - the real rapid rate (the time estimate uses the profile `max_rate` and
    ignores acceleration).

  The CAM writes neither G4 nor G94 motion, so only the rapid assumption
  affects the examples.
- **Real cuts.** No foam was cut. Wire lag, sag and heat effects are out of
  scope. The sample profile and kerf table are not measurements.
- The 3D animation and overlay were checked by eye on the examples, and
  tests check only that valid PNG/GIF files are written.
- **Windows.** Not run. The code is plain Python and pathlib, with no
  OS-specific calls.
- **Python 3.11.** Not run. Tested on 3.14.7 only; no 3.12+ features are used.
- **Classification.** If a program cuts a contour edge closer to a slit line
  than to its own target, that edge is not compared. The two-way slit test
  limits this. The stray-cut check flags slit-assigned edges more than 1.5
  kerf from their slit.

## Open questions for the project director

1. **Example deviations (effect A, job §10.3).** Should the CAM keep every
   `.dat` vertex, for example by inserting the target vertices into the
   resampling, or refining near the leading edge? Or should the acceptance
   compare against something other than the straight-segment polygon of
   §6.6? With the current method, 0.05 mm cannot be met on these airfoils.
2. **Example deviations (effect B, job §10.7 step 1).** Should the CAM look
   the kerf up at the compensated speed (iterate once), or should the
   tolerance allow for it? It reaches about 0.1 mm at the slow face of
   tapered panels.
3. **Strict xfail.** The < 0.05 mm criterion on the examples is in
   `tests/sim/test_examples.py` as `xfail(strict=True)`, so CI stays green
   and flips to red when the CAM is fixed. Is this acceptable, or do you want
   the test as a plain failing test (which would make `check.py sim` fail)?
4. **Dependencies (action needed).** `pyproject.toml` is outside the folders
   I was allowed to write, so I did not edit it. The simulator needs this
   change, or the `sim` area will fail in CI (`.github/workflows` installs
   `.[dev,cam,step,sim]`):

   ```toml
   sim = [
       "PyYAML>=6",
       "numpy>=1.26",
       "matplotlib>=3.8",
   ]
   ```

   Tested locally with numpy 2.5.4 and matplotlib 3.11.2, which I installed
   into `.venv`.
5. **Spar slots.** The slot corners come out rounded (about k/2, 0.5 mm
   radius). Should spars have chamfered corners, or should the CAM cut
   relief ("dog-bone") corners?
6. **Tolerance meaning.** I used the symmetric Hausdorff distance (the worst
   of "target point to cut surface" and "cut surface point to target") per
   element, excluding slits. Please confirm this is what "maximum deviation
   per section" should mean.

## Director's decisions (after review)

1. Open question 4: authorised. `pyproject.toml` now declares the `sim`
   group (PyYAML, numpy, matplotlib) exactly as proposed above, and nothing
   else in it changed. `python check.py sim` passes again: 97 passed,
   3 xfailed. `pip install --dry-run -e ".[dev,cam,step,sim]"` resolves.
2. Open question 3: the 0.05 mm examples test stays a strict expected
   failure until the CAM is changed and the examples pass. The marker comes
   off then.
3. The simulator's method and tolerance are not changed in response to the
   example results.
