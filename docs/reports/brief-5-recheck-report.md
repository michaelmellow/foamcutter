# Brief 5 recheck report: regenerated examples

Branch `brief-5-recheck`, created from `brief-3-cam-fixes` (1769fcc). Changes
are in `src/foamsim/compare.py`, `tests/sim/test_simulate.py`,
`tests/sim/test_examples.py` and this report.

## Summary

- On the regenerated programs, straight-wing and tapered-swept-washout meet
  0.05 mm without any change to the simulator (0.0001 and 0.0081 mm).
- tapered-spar failed at the tip with 0.6137 mm. **The simulator was wrong,
  not the program.** At the tip trailing edge the kerf changes from about
  1.23 mm on the upper and lower surfaces to 1.025 mm on the vertical TE
  moves. The last lower-surface move and the first upper-surface move are each
  shorter than half the TE kerf. The TE move's slot therefore melts away the
  whole surface those two moves would leave. The simulator still kept that
  surface, and drew a step to a point that has been melted. I wrote a
  hand-calculated test, saw it fail (0.7000 against 0), then fixed the
  simulator. tapered-spar is now 0.0090 mm.
- The two failing 1 % tests on tapered-spar came from the same error. They
  pass now, and their values are below.
- `python check.py sim`: ruff clean, mypy clean, **101 passed**. The
  expected-failure marker is gone from the 0.05 mm examples test, for all
  three examples (see item 2).
- Independence. I did not open, search, run or import anything in
  `src/foamcam/` or `tests/cam/`. I did not read `docs/reports/brief-3-report.md`,
  `docs/reports/brief-3-fixes-report.md` or job file §10 and §11. I saw their
  file names in a directory listing. My sources were the G-code, the job and
  profile files in `examples/`, my own Brief 5 code and report, and the facts
  in the prompt.

## 1. `python check.py sim` on the regenerated programs (before any change)

| Step | Result |
| --- | --- |
| ruff | All checks passed |
| mypy | no issues in 22 source files |
| pytest | 95 passed, 1 xfailed, 4 failed |

The four failures:

| Test | Result |
| --- | --- |
| `test_example_deviation_below_tolerance[straight-wing]` | XPASS(strict): now below 0.05 mm |
| `test_example_deviation_below_tolerance[tapered-swept-washout]` | XPASS(strict): now below 0.05 mm |
| `test_example_axis_scaled_one_percent_fails[Y-root-tapered-spar]` | `root 0.3331 > tip 0.5998` is false |
| `test_example_axis_scaled_one_percent_fails[A-tip-tapered-spar]` | `0.7382 > 2 × 0.6137` is false |

The 1 xfail was `test_example_deviation_below_tolerance[tapered-spar]`.
Every other examples test passed: all checks (soft limits, wire angle, axis
rate, speed ratio, cut speed, start/end outside, rapids, lead-in/exit, kerf
in table) on all three programs, and the other 10 of 12 corruption cases.

## 2. Expected-failure marker

straight-wing and tapered-swept-washout now pass 0.05 mm, so the marker had to
come off for them. After the item 4 fix, tapered-spar passes too. It cannot
stay a strict expected failure, because a strict XPASS fails `check.py`.
Director's decision 2 on the Brief 5 report also says "the marker comes off
then". So I removed the marker from the test as a whole, not just from the
two named examples. Nothing else in the test changed.

Deviations, sample profile, tolerance 0.05 mm:

| Example | Section | Before fix | After fix | Worst point after fix |
| --- | --- | --- | --- | --- |
| straight-wing | root | 0.0001 | 0.0001 | surface off target (221.479, 34.167) |
| straight-wing | tip | 0.0001 | 0.0001 | same |
| tapered-spar | root | 0.0024 | 0.0024 | surface off target (299.711, 26.250), line 67 |
| tapered-spar | tip | **0.6137** | 0.0090 | surface off target (180.995, 16.292), lines 351 / 358 |
| tapered-spar | spar hole, both | 0.0000 | 0.0000 | (corner rounding 0.212, as before) |
| tapered-swept-washout | root | 0.0009 | 0.0009 | surface off target (321.601, 30.783) |
| tapered-swept-washout | tip | 0.0081 | 0.0015 | surface off target (293.976, 24.436) |

## 3. tapered-spar: where the largest deviation is

Line numbers are lines of `examples/gcode/tapered-spar.nc`. Line 15 is the
lead-in, lines 16 to 540 the contour, line 541 the exit.

**Root (y = 150): 0.0024 mm, outer contour, surface off target, at
(299.711, 26.250).** The cause is line 67, a 0.0029 mm move at the root
(`G1 X308.8199 Y27.2111 Z254.5724 A24.5068 F50908.6`) between lines 66 and
68. Because positions are written to 4 decimals, its root speed comes out at
148.08 mm/min instead of 150. That gives kerf 1.0298 instead of 1.0250, so
its surface sits 0.0024 mm further into the part. This is rounding in a very
short move. It is real but negligible, and not an error.

**Tip (y = 750), before the fix: 0.6137 mm, outer contour, surface off
target, at (291.000, 19.004).** This is the lower trailing-edge corner. The
upper TE corner has a second spot, 0.5656 mm at (291.000, 20.787). The moves
involved, as traced at the tip plane:

| Line | Tip trace | Length | Tip speed | Kerf |
| --- | --- | --- | --- | --- |
| 537 | (290.6166, 18.9980) → (291.0645, 19.0009) | 0.4479 | 103.9 | 1.2343 |
| 538 | (291.0645, 19.0009) → (291.5125, 19.0038) | 0.4480 | 103.9 | 1.2341 |
| 539 | (291.5125, 19.0038) → (291.5125, 19.8952) | 0.8914 | 150.0 | 1.0250 |
| 540 | (291.5125, 19.8952) → (291.5125, 20.7866) | 0.8913 | 150.0 | 1.0250 |
| 16 | (291.5125, 20.7866) → (291.0908, 20.8334) | 0.4243 | 102.7 | 1.2445 |
| 17 | (291.0908, 20.8334) → (290.6690, 20.8803) | 0.4244 | 102.7 | 1.2443 |

The target TE at the tip is the vertical edge x = 291 from z = 19.6175 to
20.2175 (0.6 mm, `min_te_thickness`).

As the prompt suspected, the value is close to half the tip kerf.
0.6137 = 1.2341 / 2 − 0.0034, where 0.0034 comes from the lower surface's
slope over 0.5125 mm. It is the distance from the program's lower TE corner,
z = 19.0038, up to the target corner at z = 19.6175, measured at x = 291.

After the fix, the tip's worst point is 0.0090 mm, outer contour, surface off
target, at (180.995, 16.292). That is where the slit meets the lower surface.
The kerf changes there from 1.2702 (line 351) to 1.2341 (line 358), so the two
surfaces step by (1.2702 − 1.2341)/2 = 0.018 mm. That is the change the
prompt describes, and it is within tolerance. The very short moves (down to
0.0020 mm, tip line 67) all go to the outer contour, and none is more than
0.009 mm off it.

## 4. Which side is wrong: the simulator

**The program is right.** From the G-code alone:

- The lower tip surface (target edge (290.4765, 19.6140)–(291, 19.6175)),
  offset by 1.2341/2, meets the TE line x = 291 offset by 1.0250/2 at
  (291.5125, 19.0038). That is exactly the end of line 538 and the start of
  line 539, to all 4 decimals.
- The upper surface (target edge (291, 20.2175)–(290.4825, 20.2750)), offset
  by 1.2445/2, meets the same TE line at (291.5125, 20.7866). That is the end
  of line 540 and the start of line 16.
- The kerf on the TE moves is 1.025 because on lines 539 and 540 the tip face
  is the faster face (0.891 mm at the tip against less at the root), so the
  tip runs at the full 150 mm/min. Along the surfaces it runs at about
  103 mm/min. The program uses each move's own kerf, which is correct.

**What the simulator did wrong.** It built the surface by joining each moved
edge only to the next one. On line 538, moved up by 0.617, the surface runs
from x = 291.0645 to 291.5125 at z ≈ 19.62. Line 539's wire runs along
x = 291.5125 and melts everything within 0.5125 of it, which is all of
x > 291.0. So line 538 leaves no surface at all, because its whole moved edge
is at most 0.4525 from line 539's wire. The two moved lines cross at
(291.0, 19.62), which lies before the start of line 538. The joining rule
found no crossing on the two edges, so it fell back to a straight step from
the end of 538's moved edge to the start of 539's, (291.000, 19.004). That
point was melted by line 538's own slot. Line 16 at the upper corner is the
same case, joined across the chain's closing point. At the root the kerf
also changes at the TE: 1.025 on the surfaces and 1.0709 on lines 539 and
540, which are 0.7823 mm long at the root and run at 131.6 mm/min there. But
lines 538 and 16 are 0.6467 and 0.6199 mm long at the root, more than the TE
half kerf 0.5354. So the moved edges still cross on both edges, and the error
does not show.

**Test first.** `test_short_edge_inside_the_slot_of_a_wider_corner_edge` in
`tests/sim/test_simulate.py` is a hand-written rectangle that reproduces the
geometry with round numbers:

- a kerf table that gives 1.4 at 50 mm/min and 1.0 at 150 mm/min;
- top and bottom at 50 mm/min (wire 0.7 outside), sides at 150 mm/min
  (wire 0.5 outside), every corner an exact offset;
- top and bottom each split 0.3 mm from the right-hand side.

The hand calculation is in the test's comment. The right-hand slot melts the
0.3 mm pieces, and the correct deviation is 0. The old code gave **0.7000 at
(200.0, 40.7)**, which is half the wider kerf at a melted point. That is the
same failure as the spar tip. The test was run and seen to fail before the
fix.

**Fix (`compare.py`, `_in_slot` and `_trim`).** Before the joints are
computed, an edge is left out of its element's chain if both ends of its
moved edge lie strictly inside the slot of the edge before or after it.
"Inside the slot" means within that edge's kerf/2 of its wire path. The slot
is convex, so checking the two ends is enough. The rule repeats until nothing
changes, because each removal gives new neighbours. The remaining edges are
joined as before. This is the existing kerf model (a slot of full width k
around each move) applied to its neighbours as well as to the move itself.
No tolerance and no other rule changed.

What the fix changed on the examples:

- Edges left out: tapered-spar tip lines 16, 538 (the two TE cases), 67 and
  521 (0.002 and 0.005 mm); tapered-swept-washout root line 580, tip lines
  61, 580 and 586 (0.003 to 0.017 mm); none in straight-wing.
- Results: the spar tip went from 0.6137 to 0.0090 and the swept tip from
  0.0081 to 0.0015. Every other figure is unchanged to 4 decimals (table
  above).

All earlier hand-written tests still pass with their exact values. Among
them, the kerf step between nearly parallel edges (1.2), the uncompensated
program (0.70711) and hole corner rounding (0.20711) are the cases nearest to
this rule.

## 5. The two failing 1 % tests on tapered-spar

Both came from the simulator error in item 4, which added about 0.6 mm to the
tip of every tapered-spar run. Corrupted or not, the TE geometry hardly
changes, so the stub stays.

- **Y (axis 2) × 1.01, near section root.** The program did fail, and named
  root, but the test also requires root > tip. Root was 0.3331, but the
  tip's stub gave 0.5998, so that comparison failed. After the fix the tip is
  0.0666.
- **A (axis 4) × 1.01, near section tip.** The program failed and named tip,
  but the test requires the deviation to be more than twice the clean value.
  It was 0.7382 against a clean 0.6137, because the stub inflated the clean
  value. After the fix it is 0.2630 against a clean 0.0090.

In both cases the corruption itself was detected and the right section was
named. The checks that failed were the comparisons against a wrong baseline.

tapered-spar 1 % results after the fix:

| Axis × 1.01 | Root | Tip | Failed sections |
| --- | --- | --- | --- |
| X (axis 1) | 2.9399 | 0.5880 | root, tip |
| Y (axis 2) | 0.3331 | 0.0666 | root, tip |
| Z (axis 3) | 0.4654 | 2.3272 | root, tip |
| A (axis 4) | 0.0526 | 0.2630 | root, tip |

All 12 corruption cases (3 examples × 4 axes) pass.

## What was verified, and how

- The new test fails on the old code (0.7000) and passes on the new.
- The TE corners of tapered-spar were recomputed from the target and the
  G-code kerfs, independently of the simulator, and match the program to 4
  decimals (item 4).
- Before/after deviations of every element of every example were compared.
  Only the two tips listed changed.
- `python check.py sim`: ruff clean, mypy clean, 101 passed (was 97 + 3 xfail
  at Brief 5; +1 new test). The full `python check.py` was not run.

## What was NOT verified

- **Slit slots are not subtracted from the outer surface.** The remaining
  tapered-spar tip value, 0.0090 at (180.995, 16.292), is at a point that the
  slit move (line 352) itself melts. The point is about 0.06 mm from the slit
  wire, inside its 0.593 half kerf. So the real part has the slit opening
  there, not this surface. The simulator overstates the deviation, which is
  the safe direction. I did not change it: this brief's rule leaves slits out
  of the comparison, and the effect is small.
- **The slot model at short moves.** The kerf is taken from each move's
  average speed, and the slot ends are taken as round discs of radius k/2.
  Real wire lag and heat at a 0.002 mm move are out of scope (Brief 5).
- **Short moves near the 4-decimal limit.** A move of 0.002 to 0.003 mm has
  its direction and speed set mainly by rounding: tip line 67 has kerf 1.234
  against 1.2445 for its neighbours. The simulator handles this, but the
  numbers at those moves depend on the rounding.
- grblHAL, Windows and Python 3.11 are unchanged from the Brief 5 report
  (not verified there either).

## Open questions for the project director

1. **Very short moves.** The programs now contain moves down to about
   0.002 mm. Is that intended? At 4 decimals their speed is off by up to
   about 1.3 % (tip line 67: 148.1 instead of 150 at the root). grblHAL's
   planner may also handle a run of such moves differently from long ones.
   I have not checked that against grblHAL source.
2. **Kerf change at the TE.** At the tip the TE moves run at 150 mm/min
   (kerf 1.025) and the surfaces at about 103 (kerf 1.23). The program offsets
   each correctly, but the real part's TE then depends on the kerf table being
   right at both speeds. That is a point for the first-cut plan's kerf
   measurement.
3. **Slit slot.** Should the simulator subtract slit slots from the outer
   surface (see "What was NOT verified")? It would only ever lower the
   reported deviation near a slit.
