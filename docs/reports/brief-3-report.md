# Brief 3 report: CAM core

Branch: `brief-3-cam-core` (based on `setup-fixes`, which was not yet merged
into `main` when this work started).

## 1. What was built

**Formats** (approved by the director, with the changes requested in review):

- `docs/formats/machine-profile.md`: the machine profile format and the work
  frame. It defines G54, the physical zero of each axis, soft limits, cutting
  limits and kerf tables.
- `docs/formats/job-file.md`: the job format, the exact target geometry of a
  section, the CAM method and the G-code produced.
- `examples/profiles/sample-machine.yaml`: sample profile. **All its values are
  invented**, and its own comments say so.

**Library and tool** (`src/foamcam/`, Python 3.11+, one third-party library: PyYAML):

| Module | Purpose |
| --- | --- |
| `profile.py`, `job.py`, `fields.py` | YAML-only loaders and validators. Every violation is reported with its field path; unknown fields are errors. |
| `dat.py` | Selig and Lednicer `.dat` reader (§7). |
| `sections.py` | Target geometry (§6): clean, orient, normalise, trailing-edge thickening, scale, twist, place; feature and slit rules (§8). |
| `geometry.py` | Arc-length resampling, crossing tests, mitred polygon offset. |
| `cam.py` | Traversal, synchronisation, kerf compensation, projection to the towers, inverse-time timing, checks, mirror, G-code (§10 to §13). |
| `output.py` | Text report (travel used, wire angle, speed ratios incl. lead-in/exit, kerf values, path lengths, time) and SVG preview. |
| `kerftest.py` | Kerf test-cut generator: one horizontal slot per speed, wire parallel to y. |
| `cli.py` | `python -m foamcam cut / check / profile / kerf-test`. |

**Worked examples**: `examples/jobs/` and `examples/gcode/`:

1. `straight-wing`: E205, 200 mm chord, no twist or sweep. 3.26 min.
2. `tapered-swept-washout`: Clark Y 240 → 150 mm, tip leading edge 60 mm aft,
   3° washout. 4.23 min.
3. `tapered-spar`: E205 220 → 150 mm, 10 × 6 mm spar slot from the lower
   surface. 4.50 min.

The airfoils are from the UIUC Airfoil Coordinates Database
(`m-selig.ae.illinois.edu/ads/coord/`): `clarky.dat` (Lednicer layout, CRLF)
and `e205.dat` (Selig layout, closed trailing edge). Both files are as
downloaded. All three examples use the sample profile.

**Repository changes outside my folders**, each authorised:

- `check.py`, `pyproject.toml`: the two temporary exceptions are removed
  (step 2). mypy now checks `tests/` as well. PyYAML is in the `cam` group and
  `types-PyYAML` in `dev`.
- `docs/briefs/brief-5-simulator.md`: accepted G-code list changed to
  "G0, G1, G21, G54, G90, G91, G93, G94, G4" (director's item 5).

## 2. What was verified, and how

`python check.py cam` passes: ruff, strict mypy and 41 tests. `python check.py`
(all areas, as run by CI) also passes. Both were run on macOS with Python
3.14.7, and the cam check was repeated with Python 3.12.10.

Acceptance list, each one a test in `tests/cam/test_acceptance.py`. The tests
read the G-code with their own parser, written from job-file.md §13, and do not
use the CAM's geometry code:

| Acceptance item | Test | Result |
| --- | --- | --- |
| Identical root and tip, block centred: axis 1 = axis 3, axis 2 = axis 4 on every line | Clark Y with twist and a spar hole, sections at y = 150 and 750 (centred in 900); compares the written text of every G0/G1 line | pass, exact |
| Tapered case, hand-calculated projection, 0.01 mm | Rectangle section, chord 100 → 50, constant kerf 0.2. The full hand calculation is in the test's docstring, for independent checking. 7 lines checked in order (E, S, LE, corner, lower TE, S, E). | pass |
| Mirrored job is the exact mirror | Tapered swept Clark Y with spar hole; every motion line of the mirrored job equals the original with axes 1↔3 and 2↔4 exchanged, F identical | pass, exact |
| Job exceeding travel is rejected, naming the axis and amount | Axis 4 max lowered to 65 where the program reaches 70.1. Error: `axis 4 (A) goes to 70.1000, beyond its max 65.0000 by 5.1000 mm (program line …)`. No other axis is named. | pass |
| Total cut time = path length at faster end / cut speed, within 1 % | Σ(1/F) from the G-code against Σ max(root, tip) face distances recomputed from the axis words and the profile's wire span | pass |

Also tested:

- **§14 example.** `test_format_example.py` loads the YAML block from
  `job-file.md` exactly as written, validates it and produces a program, so the
  document cannot drift from the code (director's item 2).
- **Worked examples.** `test_worked_examples.py` regenerates each example and
  compares it with the committed `.nc`, with tolerances of 0.0002 mm and 1e-5
  relative on F, so platform rounding cannot cause a false failure.
- **Kerf offset.** With a constant kerf of 0.4 mm on the tapered Clark Y job,
  every compensated contour node is at least 0.199 mm from its target, and at
  least 95 % of nodes are 0.200 ± 0.002 mm away. Mitred convex corners are
  further, which is correct. Rectangular hole corners move exactly √2 × 0.2 mm
  into the hole.
- **Unit tests** (31 in `test_units.py`):
  - Lednicer and Selig parsing on the real UIUC files.
  - Orientation and normalisation.
  - Trailing-edge thickening reaches exactly the minimum.
  - Positive twist raises the leading edge by c·sin θ.
  - Kerf interpolation, and the exact out-of-range message (names speed and range).
  - The G-code uses only the §13 subset, with the format rules and header order.
  - `lower_first` direction, and open (hinge) features.
  - Speed-ratio rejection, and the lead-in/exit exemption.
  - Wire-angle and axis-rate limits, and a rapid move through the block.
  - Seven job-rule violations and two profile-rule violations reported with
    their paths; `.yaml`-only loading.
  - Kerf test generator, and the CLI's success and error paths.
- **Visual check.** I opened the SVG preview of `tapered-spar`. The wire path
  is outside the outer contour and inside the spar hole, the slit enters from
  the lower surface, and the root and tip spar holes coincide in x (a straight
  spar).

## 3. What was NOT verified

- **Nothing has been cut.** No G-code has been run on grblHAL, in ioSender or
  on the machine. In particular I have not confirmed that grblHAL and ioSender
  accept the F-word format, the `.nc` extension, or `G93` with `G0` lines in
  between.
- **The sample profile is invented.** Real wire span, travel, zero references
  and kerf tables are needed before any cut.
- **Windows.** Not run on Windows, and not run on Python 3.11 (3.14 and 3.12
  only). CI covers Windows and 3.11 once pushed.
- **Kerf model.** Kerf is looked up from the speeds of the uncompensated path
  and not iterated (approved). The size of that error on real foam is unknown.
- **Rapid-move block check.** It samples 101 planes between the foam faces;
  it is not an exact test.
- **Kerf test retract.** The kerf test retracts through each slot with `G0`.
  Whether that widens the slot measurably is untested; the program tells the
  operator to measure near the closed end.
- **Preview format.** The preview is SVG, not a raster image. That is
  deliberate (no plotting dependency), but it should be confirmed as
  acceptable.

## 4. Deviation from the approved formats: needs your decision

**§10.7 step 3 (kerf offset vertices).** The approved text says each vertex is
"the intersection of the two offset edges". The first tapered test showed this
fails on real jobs. Kerf differs slightly from edge to edge, because each
edge's speed differs. Where two adjacent edges are nearly collinear (for
example where a slit point splits a straight stretch), lines offset by
different amounts intersect far away: 5.96 mm in the observed case. That
tripped the 4 × (k/2) guard.

The code now mitres each vertex with the **mean** half-kerf of its two edges:
`v + h·(n1 + n2) / (1 + n1·n2)` with `h = (h1 + h2) / 2`. When the two kerfs
are equal this is exactly the approved intersection, so the hand-calculated
test is unaffected. Otherwise the error is at most half the kerf difference
between neighbouring edges (micrometres).

I have **not** edited `docs/formats/` because it is approved. Proposed
replacement for §10.7 step 3:

> 3. **Offset.** In each section plane, offset every edge of the outer contour
>    outward, and every edge of a hole into the hole. Each vertex `v` moves to
>    `v + h · (n1 + n2) / (1 + n1·n2)`, where `n1` and `n2` are the unit
>    normals of its two edges, pointing away from the material, and
>    `h = (k1 + k2) / 4` is the mean half kerf of the two edges. When both
>    edges have the same kerf this is the intersection of the two offset
>    edges. Edges of zero length are skipped.

The simulator does not depend on §10, so this changes nothing for Brief 5.

## 5. Where a tapered or swept panel in a rectangular block breaks an assumption

(Director's item 4.) Places found, besides the lead-in entry point that the
director corrected:

1. **Lead-in and exit speed ratio.** One entry `x` for both faces makes the
   lead-in lengths differ by the difference in trailing-edge positions. Now
   exempt and reported. It is 1.00, 2.24 and 1.76 in the three worked examples,
   and 2.82 in the job-file.md §14 example.
2. **Kerf table range.** On a tapered panel the small section moves slower, at
   down to `cut_speed / max_speed_ratio`. The kerf table must cover that whole
   range or the job stops (as designed: no extrapolation). The real kerf tables
   must be measured down to about a third of the cut speed.
3. **Speed-ratio ceiling caps the taper.** The contour speed ratio is close to
   the chord ratio, so a panel tapering more than `max_speed_ratio` (3 in the
   sample) is rejected even if it could be cut. With twist or sweep the local
   ratio near the leading edge can exceed the chord ratio.
4. **Foam faces = section planes.** Speed and kerf are controlled only at the
   section planes (§5). If the physical block is longer in y, the wire beyond
   the small section moves even slower. That foam is cut hotter and wider than
   any kerf table assumes, and can exceed the speed ratio unseen.
5. **Projection amplification.** Carriage motion is the face motion scaled by
   `wire_span / (y_tip − y_root)` and beyond. A short or off-centre panel needs
   much more carriage travel and speed than its sections suggest, so soft-limit
   and `max_rate` failures become likely. The report shows the travel used.
6. **Block size is set by the larger section in both x and z.** At the small
   section a large waste region remains in front of the trailing edge and
   behind the leading edge. That wastes material and time (a long lead-in at
   that face) but is not incorrect.
7. **Synchronisation by arc length.** Root and tip points are paired at equal
   arc-length fractions between fixed points. For the same airfoil at both ends
   (taper, sweep, twist) this equals pairing at equal chord fractions. For
   different airfoils at the two ends (blending), the ruled surface between
   them follows arc length, not chord fraction. That is a design choice to
   confirm before Brief 4 blends airfoils.
8. **Outer-contour corners.** Corners of a polyline section that are not fixed
   points (§10.2) are only kept if they land on a resampling step. The approved
   method keeps them for similar root and tip shapes (the hand-calculated test
   relies on that), but a non-airfoil polyline with corners at different
   arc-length fractions at root and tip will have its corners slightly cut.

## 6. Open questions for the project director

1. Approve the §10.7 replacement text in section 4, or tell me to change the
   code back.
2. `table_height` was removed from the profile because the vertical zero is now
   the table top (raised at the second review; not yet confirmed).
3. **Areas without tests now fail their own check.** Removing the temporary
   exceptions means `python check.py step` and `python check.py sim` fail
   until those areas have tests. `sim` also fails mypy, because `tests/sim/`
   has no Python files. The full `python check.py` used by CI passes.
4. **Dev tools are unpinned.** ruff 0.17.4 enables more rules by default than
   older releases, and a future release could fail CI without any code change.
   Pin ruff, mypy and pytest versions in `pyproject.toml`?
5. **Brief 1 needs to follow the new zero rules.** The commissioning procedure
   must set G54 to the physical zeros of machine-profile.md §3.3 and §3.4. The
   Brief 1 branch has a WIP commit that predates this.
6. The profile's `lead` field is recorded but unused by the software, as
   specified. Keep it?
