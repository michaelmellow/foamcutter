# First-cut plan

An ordered list of test cuts on scrap foam, from a plain block to a tapered,
swept panel. Each cut tests one new thing on top of the cuts before it. Do not
go to the next cut until the current one meets its "pass" line, or the project
director has accepted the difference.

## Before any cut

1. Commission the machine (Brief 1) and set the four G54 zeros as in the
   machine profile format, section 3.4. Re-check them after every homing
   cycle.
2. Replace the sample profile with measured values: `wire_span`, axis
   `min`/`max`, `max_rate`. The kerf table comes from cut 1 below; until then
   use the sample table only for cut 1.
3. For every program, run the simulator first and keep its report with the
   cut record:

   ```text
   python -m foamsim PROGRAM.nc --job JOB.yaml --profile PROFILE.yaml
   ```

   Cut only if it passes, or if the director has accepted the reported
   deviation in writing. Look at the 3D animation (`output/sim/*-cut.gif`)
   once for every new kind of cut.
4. Run every new program once as an **air cut**: wire cold, no foam. Watch
   that the wire starts and ends where the simulator says it does, and time
   the program with a stopwatch.

## Tools

Digital caliper (0.01 mm), steel rule (0.5 mm), engineer's square,
feeler gauges, a flat reference plate, and an incidence meter (or a digital
angle gauge) for the last cut. Print the target sections 1:1 from the job
coordinates as templates.

## Record for every cut

Foam batch and density, wire supply setting, room temperature, program file
and its simulator report, stopwatch time against the report's total time,
every measurement below, and photos of both end faces.

## The cuts

| # | Cut | New thing it tests | Measure after the cut | Pass |
| --- | --- | --- | --- | --- |
| 1 | **Kerf test.** Straight horizontal slots through a 50 mm thick block with the wire straight (a1 = a3, a2 = a4), one slot per speed: 30, 60, 120, 240, 480 mm/min, each at a different height. | Wire heat, kerf against speed. | Slot width at both faces, at 3 points along each slot, with feeler gauges. Width at both faces must agree; if not, the wire is not straight or not evenly hot. | Repeatable to 0.05 mm over two runs. Enter the means as the profile's kerf table. |
| 2 | **Square block.** A 100 × 40 mm rectangle through the full block length, wire straight, one speed in the middle of the table. | x and z scale (steps per mm), kerf compensation, both towers moving alike. | Width and height at both faces and in the middle. Diagonals of each face. | Within 0.1 mm of 100 × 40 at both faces; diagonals equal within 0.1 mm. |
| 3 | **Located block.** The same rectangle with its back face 20 mm from the x reference face and its bottom 10 mm above the table. | The G54 zeros (x reference face, table top). | Distance from the x reference face to the part's back face, and from the table to its bottom face, at both ends. | Within 0.2 mm of 20 and 10 at both ends. |
| 4 | **Tapered block.** Root rectangle 100 × 40, tip 60 × 24, same leading-edge corner, section planes at the block ends. | Projection to the towers, different speeds at the two faces, kerf at the slower face. | Both end faces: width, height, corner positions against a template. Compare the stopwatch time with the report. | Within 0.15 mm at each face. The slower (tip) face shows if the kerf table holds at low speed. |
| 5 | **Swept block.** Root as in 4, tip shifted 40 mm toward the front, wire angle a few degrees. | Wire angle, sweep. | As cut 4, plus the 40 mm offset measured from the x reference face at both ends. | Within 0.2 mm. |
| 6 | **Straight wing.** `examples/jobs/straight-wing.yaml` with the real profile. | Airfoil contour, leading and trailing edge. | Chord and maximum thickness at both faces and in the middle; trailing-edge thickness; fit of the 1:1 template (light gap). | Chord within 0.2 mm, thickness within 0.15 mm, TE not thinner than 0.6 mm. Read the simulator's deviation first: see the Brief 5 report. |
| 7 | **Tapered wing with spar slot.** `examples/jobs/tapered-spar.yaml`. | Features, slit, taper with an airfoil. | Slot width, height and position from the leading edge at both faces; fit of the real spar; slit width. Section checks as in 6. | Spar slides in without force and without play over 0.2 mm. The slot corners are rounded (radius about half the kerf); check the spar's corners clear them. |
| 8 | **Tapered swept wing with washout.** `examples/jobs/tapered-swept-washout.yaml`. | Everything together, including twist. | As 6 at both faces, plus incidence of root and tip on a flat plate (twist = 3° between them), and the sweep offset of the leading edge. | Twist within 0.2°, sweep within 0.3 mm, sections as in 6. |

## After each cut

- If a cut fails, find the cause before going on. Compare the measured part
  with the simulator's overlay: a difference that the simulator also shows
  is a program problem; a difference it does not show is a machine, heat,
  foam or set-up problem (or wire lag and sag, which the simulator does not
  model).
- If the kerf at the slower face in cut 4 is off by more than 0.05 mm, repeat
  cut 1 at the slow speeds before cutting 6 to 8.
- Update the profile (kerf table, limits) only from measurements, and record
  which cut each value came from.
