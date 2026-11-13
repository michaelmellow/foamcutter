# Brief 3 follow-up report: CAM fixes (synchronisation and kerf speed)

Branch: `brief-3-cam-fixes`, from `main` at `92af092`.

The independent simulator (Brief 5) found two errors of about 0.1 mm in the CAM
method: §10.3 resampling skipped section vertices, and §10.7 looked the kerf
up from the speed of the uncompensated path. This branch fixes both with the
method the director approved in three rounds of decisions (below).

## 1. What was built

### History of the method

1. **First rule (union of vertex fractions on the uncompensated contour):**
   implemented, then abandoned. On tapered panels root and tip vertices fall at
   nearly the same arc-length fraction all along the contour, because the
   trailing-edge thickening of §6.2 is a fixed thickness in mm. That gave very
   short moves (118 of 643 under 0.05 mm on `tapered-swept-washout`), which the
   offset then distorted. On `tapered-spar` one move had a speed ratio of 16.6
   and a tip speed of 9 mm/min, so the job was refused. Merging close
   breakpoints still left kerf errors up to 0.18 mm. Reported; the director
   chose option 1.
2. **Option 1, approved:** offset first, then synchronise the offset contours,
   with one kerf per stretch per face. Two tests could not be met as written
   (the hand-calculated P2 line, and test (b) at the slit join of
   `tapered-spar`). Reported; the director approved A1 and C1 with changes.
3. **Final method,** as now written in `docs/formats/job-file.md` §10.2, §10.3,
   §10.7, §11 and §11.1:
   - **Stretches.** The outer contour is divided at the fixed points
     (`S`, leading edge, far trailing edge, slit points). Each stretch has one
     kerf at the root face and one at the tip face.
   - **Offset first.** Each face's target contour is offset by its stretches'
     half kerfs. Then the two offset contours are synchronised stretch by
     stretch. The breakpoints are every vertex of both offset pieces, by
     fraction of offset arc length, plus equal steps so that no move is longer
     than `max_segment_length` at either face. Breakpoints less than 0.001 mm
     apart at both faces are merged. So within a stretch both faces move in
     proportion and the speed at each face is constant: one kerf per stretch per
     face is exactly the kerf of every move in it.
   - **Kerf iteration.** The first lookup uses the target contour's speeds. Then
     the CAM offsets, recomputes speeds from the offset contours and looks up
     again, until no half kerf changes by more than 0.001 mm. It stops with an
     error after 10 rounds. Holes do the same per edge.
   - **Joins.** Where the kerf changes, edges that turn by 20° or more are
     joined by the exact intersection of the offset edges. Below 20° the mean
     half kerf is used.
   - **Transition vertices.** At every nearly-in-line join (turn < 20°) a target
     vertex is added on each side, `max_segment_length` away, so the kerf change
     stays within one segment of the join.
   - **Dropped lines.** A `G1` line whose four axis words equal the previous
     line's after rounding is dropped, and its time is added to the next line.
     Before, the time was lost.
   - **Kerf table range.** Only the settled lookup counts. During the
     iteration a speed outside the table takes the nearer table end. If the
     settled lookup is outside the table, it is recorded with the exact message
     of machine-profile §7, the nearer table end is used only so that the other
     checks can run and be reported in the same run, and no program is written.
     (Changed after review, director's answer to open question 2: at first,
     any round counted.)

### Code (`src/foamcam/`)

| File | Change |
| --- | --- |
| `cam.py` | `compensate()` now builds the offset outer contour (`outer_contour`) and the offset holes. The shared iteration is `settle`, with group speeds from `group_halves`. New: `transition_vertices`, out-of-table collection (`lookup`), time carry in `gcode_lines`. Constants `KERF_ROUNDS = 10`, `KERF_SETTLED = 0.001`, `EXACT_JOIN_ANGLE = 20.0`, `MERGE_GAP = 0.001`. `synchronise()` and the old `offset()` are gone. |
| `geometry.py` | `resample` replaced by `synchronise` (union of vertex fractions, equal steps, merge gap) with helpers `cumulative_lengths` and `point_at`. `offset_polygon` has the 20° join rule. New `turn_angle`. |
| `output.py` | The text report also lists the kerf per stretch per face and the number of kerf rounds. |

### Tests (`tests/cam/`)

- New `test_sync_and_kerf.py`. Each test runs on all three worked examples,
  builds the §6 and §8 target geometry itself, and reads speeds from the
  G-code:
  - **(a)** Every vertex of both target contours (and of the holes) lies on
    the uncompensated path within 0.001 mm. The "uncompensated path" is the
    program made with a constant kerf of 1e-6 mm.
  - **(b)** For every contour move at each face, the kerf applied (twice the
    distance from the segment's midpoint to the target) matches the table kerf
    at the move's G-code speed (face length × F), within 0.005 mm. At
    nearly-in-line joins (worked out by the test from §6, §8 and §10.2), moves
    within `max_segment_length` of the join must instead lie between the table
    kerfs of the moves around the join, within 0.005 mm. Face segments shorter
    than 0.05 mm are not checked, because 4-decimal positions don't give a
    usable speed for them.
- New unit tests in `test_units.py`:
  - the join rule just below and just above 20° (mean vs exact intersection,
    checked by distance to both edge lines);
  - transition vertices just below and just above 20°;
  - breakpoint merging just below and just above 0.001 mm;
  - a dropped `G1` line's time carried into the next line;
  - an out-of-table speed reported with the exact §7 message together with a
    soft-limit error;
  - no files written by `cut` when a lookup is out of range;
  - the 10-round error (forced by limiting the rounds to 1);
  - only the settled lookup counts: with a constant 1 mm kerf the slowest
    stretch runs at 90.49 mm/min on the target contour and 91.06 mm/min
    settled. A table from 90.8 mm/min is cut; a table from 91.2 mm/min is
    refused with the §7 message.
- **Changed existing tests:**
  - `test_tapered_projection_matches_hand_calculation`: the single P2 line is
    replaced by two lines, (49.9, 9.8336, 199.9, 70.0329) where the root
    reaches the corner and (50.1649, 9.9, 199.7675, 69.9) where the tip
    reaches it. The derivation is in the docstring (A1, re-derived and
    approved by the director). The other six expected lines are unchanged and
    still pass.
  - `test_compensated_contour_is_half_kerf_from_target`: only its call into the
    CAM changed (`cam.compensate()` replaces `synchronise()` +
    `compensate(...)`, which no longer exist). All assertions are unchanged.

### Worked examples

All three were regenerated (`examples/gcode/*.nc`). Shortest move = the G1 move
whose longer face movement is smallest, measured from the G-code at the two
foam faces.

| Example | G1 moves before → after | Shortest move before (root / tip, mm) | Shortest move after (root / tip, mm) | Kerf rounds | Cut time before → after |
| --- | --- | --- | --- | --- | --- |
| straight-wing | 410 → 446 | 0.9910 / 0.9910 | 0.5737 / 0.5737 | outer contour 1 | 3.263 → 3.263 min |
| tapered-spar | 458 → 527 | 0.9885 / 0.6811 | 0.0029 / 0.0020 | outer contour 2, spar hole 1 | 4.498 → 4.501 min |
| tapered-swept-washout | 494 → 638 | 0.9989 / 0.6243 | 0.0044 / 0.0028 | outer contour 2 | 4.227 → 4.230 min |

Moves shorter than 0.05 mm at the longer face: 0, 4 and 10 (straight-wing,
tapered-spar, swept). No `G1` line was dropped as identical after rounding in
any example.

Kerf per stretch (mm, sample kerf table, cut speed 150 mm/min):

| Example | Stretch | Root | Tip |
| --- | --- | --- | --- |
| straight-wing | start to leading edge | 1.0250 | 1.0250 |
| | leading edge to far trailing edge | 1.0250 | 1.0250 |
| | far trailing edge to start (TE face) | 1.0250 | 1.0250 |
| tapered-spar | start to leading edge | 1.0250 | 1.2443 |
| | leading edge to slit "spar" | 1.0250 | 1.2700 |
| | slit "spar" to far trailing edge | 1.0250 | 1.2340 |
| | far trailing edge to start (TE face) | 1.0708 | 1.0250 |
| | spar hole, every edge | 1.0250 | 1.0250 |
| tapered-swept-washout | start to leading edge | 1.0250 | 1.3152 |
| | leading edge to far trailing edge | 1.0250 | 1.3149 |
| | far trailing edge to start (TE face) | 1.0873 | 1.0250 |

On the trailing-edge face the tip is the faster face, because both
trailing edges are 0.6 mm thick and the tip's offset face is the longer. The
spar hole is 10 × 6 mm at both sections, so its speed ratio is 1.

### Sentences changed in `docs/formats/job-file.md`

- **§3, `max_segment_length` row:** "…at either foam face, before kerf
  compensation (§10.3)." → "…at either foam face, measured along the
  kerf-compensated contour (§10.3)."
- **§10.2:** "All work in §10 uses the untwisted, unplaced contours of §6.3
  with the slit points inserted as extra vertices." is replaced by: the slit
  points are inserted into the §6.3 contour; compensation and synchronisation
  work on the placed contours of §6.5, which have the same lengths and angles.
  A new paragraph defines **stretches**.
- **§10.3:** rewritten in full. The old text (equal arc-length steps `j/m` on
  the uncompensated pieces) is replaced by the union-of-breakpoints rule on
  the offset contours, the 0.001 mm merge rule, and the statement that the
  speed is constant per stretch.
- **§10.7:** rewritten in full. Steps: kerf groups; speed of a group; kerf;
  transition vertices at nearly-in-line joins (20°); offset with the exact or
  mean join rule (formulas given); iteration (0.001 mm, 10 rounds); slits and
  open features not offset; spike guard (now "4 × the larger half kerf of its
  two edges", which is what the code has always done). The sentence "Kerf is
  looked up from speeds of the uncompensated path. The speeds of the
  compensated path differ slightly; this is accepted and not iterated." is
  removed.
- **§11:** new paragraph: a `G1` line equal to the previous line after
  rounding is not written, and its time is added to the next `G1` line.
- **§11.1:** new bullet **Kerf table range**, with the director's three
  conditions. After review it says that only the settled lookup counts, and
  that during the iteration an out-of-range speed takes the nearer table end
  and the iteration carries on.

No other section was changed. §13 is unchanged: it defines the form of the
lines written, not which moves become lines, so the dropped-line rule is
stated in §11, where the timing is.

## 2. What was verified, and how

- `python check.py cam` passes: ruff, strict mypy and 59 tests. Run on macOS
  with Python 3.14.7 (project `.venv`).
- Both new tests failed on the old code first:
  - (a): 0.1072 mm (straight-wing), 0.1437 mm (tapered-spar) and 0.0494 mm
    (swept), the first matching the simulator's 0.107 mm;
  - (b): 46, 55 and 61 face segments outside 0.005 mm.
- Test (b) does detect the kerf-speed error on its own: with the first rule
  and no iteration, the swept example failed on speed-dependent kerf errors.
- Test (b) does need the transition vertices: with them disabled,
  `tapered-spar` fails on 7 face segments at the slit join.
- The join rule of test (b) is exercised: one nearly-in-line join per face on
  `tapered-spar` (slit point) and on `tapered-swept-washout` (leading edge).
- The hand-calculated projection test passes. Its two new P2 lines match my
  hand derivation exactly, and the director re-derived them independently.
- The report change did not change the G-code (byte-identical before and
  after).
- **Simulator (full `python check.py`).** I did not open `src/foamsim/`,
  `tests/sim/` or the Brief 5 report. I ran the simulator's tests and read
  only pytest's outcome lines and one-line assertion messages:
  - On `main`: 15 passed, 3 xfailed.
  - On this branch: `test_example_deviation_below_tolerance` **now passes** for
    `straight-wing` and `tapered-swept-washout`. They are marked as strict
    expected failures (reason: "CAM method (job file 10.3 resampling, 10.7
    kerf at uncompensated speed) gives 0.05 to 0.14 mm"), so pytest reports
    them as failures (XPASS(strict)).
  - For `tapered-spar` that test still fails (it is still an expected failure).
  - `test_example_axis_scaled_one_percent_fails[Y-root-tapered-spar]` and
    `[A-tip-tapered-spar]` now fail, with `assert 0.3330836458704475 >
    0.5997627058662345` and `assert 0.7382258637872993 > (2 *
    0.6136518549503354)`.

  **So the full `python check.py` fails on this branch (4 tests in the sim
  area), and CI will fail until Brief 5's tests are updated.**

## 3. What was NOT verified

- **Nothing has been cut**, and no program has run on grblHAL or ioSender.
- **Very short moves.** The shortest moves are now 0.002 to 0.004 mm. One
  motor step is lead / (steps per rev × microsteps); the microstep setting is
  not fixed in this repository, so I can't say how many such moves are below
  one step. I don't know how grblHAL's planner handles them in G93.
- **The kerf model.** One kerf per stretch per face from a single speed, and a
  kerf change spread over one `max_segment_length` at in-line joins, are model
  choices. Neither has been checked against real foam. The sample kerf table
  is invented.
- **Why `tapered-spar` still fails the simulator.** Its tolerance and method are
  unknown to me by design. Candidates I can see from the CAM side: the
  transition zones at the slit join (kerf error up to half the kerf change,
  0.018 mm here) and the slit itself. Both are guesses.
- **Windows and Python 3.11.** Not run; CI covers them once pushed.
- **Axis-rate check.** It still uses each move's own time, not the time a
  dropped line adds to the next move. That can only overstate the rate. No
  example drops a line.

## 4. Open questions for the project director

1. **Simulator tests.**
   - Two strict expected-failure markers in `tests/sim/test_examples.py`
     (straight-wing and swept deviation) now pass and should be removed by the
     Brief 5 owner.
   - `tapered-spar` still exceeds the simulator's tolerance, and its two
     1%-axis-scaling tests fail. These need the Brief 5 owner to look, and may
     show a remaining CAM error. I'll fix it if one is found.
   - Until then `main` would fail CI if this branch is merged as it is.
2. **Strictness of the out-of-range rule.** Answered: only the settled
   lookup counts. Implemented, §11.1 updated, tested.
3. **Very short moves.** Answered: keep the 0.001 mm merge gap for now; to be
   revisited after the firmware bench test.
4. **Kerf at the trailing-edge face.** Answered: accepted, as it follows
   from the rule.
