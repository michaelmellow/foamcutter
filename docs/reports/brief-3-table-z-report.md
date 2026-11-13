# Brief 3 follow-up report: table height (`table_z`) and homed zeros

Branch: `brief-3-table-z`, from `main` at `a26fa94`.

The machine's engineers have fixed the G54 zero at the homed position of
each axis: 1 mm off the bottom stop of each vertical axis, and 1 mm off the
operator-side stop of each horizontal axis. The wire is square to the towers
by construction. The table top is therefore no longer `z = 0`. Its height is
now a required profile field, `table_z`.

**Do not merge yet.** The simulator rejects the new field (§3, item 1), so
`python check.py sim` fails on this branch.

## 1. What was built

### Formats

- `docs/formats/machine-profile.md` §3:
  - Rewritten. The zero of each axis is now its homed position.
  - The x reference face and the table-top zero are removed. The axis
    directions, the frame, the carriage planes and the wire points are
    unchanged.
  - New §3.3 states the squareness assumption and gives a one-time check.
  - New §3.4 defines `table_z` and how to measure it.
  - The operator's steps move from §3.4 to §3.5.
- `docs/formats/machine-profile.md` §4: new required field `table_z`, which
  takes any number. §5: one sentence now points to §3.5.
- `docs/formats/job-file.md` §5: the block occupies `z` from `table_z` to
  `table_z + height`. §10.6: the rapid check names that range.

### Code (`src/foamcam/`)

Every place that assumed the table is at `z = 0` now uses `table_z`:

| File | Rule | Change |
| --- | --- | --- |
| `profile.py` | §4 | Reads the required field `table_z`, which takes any number. A profile without it is refused with `table_z: missing`. |
| `sections.py` (`check_in_block`) | job §6.7 validity | The `z` range is `table_z` to `table_z + height`. |
| `cam.py` (`rapid_hits_block`) | job §10.6 rapid check | The block's box starts at `z = table_z`. |
| `kerftest.py` | kerf-test generator | The slots are placed from the table top: `z = table_z + margin + i · spacing`. The block-height check is now relative to the table, and so is the rapid clearance box. The slot comments give the absolute `z`. |
| `output.py` | text report, SVG preview | The block's `z` range in the report, and the block rectangle in the SVG, start at `table_z`. |

Nothing else in the CAM depends on the table:

- The kerf offset, the synchronisation, the projection, the timing and the
  other checks are all translation-invariant in `z`.
- Each section's `z` is an absolute G54 coordinate that the job gives.

### Examples

`examples/profiles/sample-machine.yaml` has the new field `table_z: 0.0`, and
its header comment no longer mentions the reference face. The three worked
examples were regenerated and are byte-identical to the committed
`examples/gcode/*.nc`. The job files and the G-code are unchanged.

### Tests (`tests/cam/`)

- `test_acceptance.py`: the hand-built profile in `constant_kerf_profile` now
  has `"table_z": 0.0`. Without it, that profile is refused because the field
  is required. No assertion changed. This is the only edit to an existing
  test.
- New `test_table_z.py` (10 tests):
  - **`test_raising_the_table_and_the_sections_shifts_only_axes_2_and_4`**,
    the test the brief asked for. It runs for each worked example and for
    `dz = +40` and `dz = −20`. It raises `table_z`, both section `z` values and
    the vertical axes of `safe_position` by `dz`. It then checks every line:
    - Axes 2 and 4 move by `dz`, within one unit in the 4th decimal.
    - Every other word is unchanged: the axis 1 and 3 words, `F`, the `G`
      words and the comments.
    - The line count is the same.

    So that negative values are allowed, the profile's vertical soft limits
    are widened by 100 mm. A first assertion shows that this alone changes
    nothing.

    `safe_position` is raised too because it is an absolute axis position.
    Otherwise the two `G0` lines to the safe position would not move.
  - `test_section_must_lie_above_the_table`: raising the table to the
    sections' position `z` makes the job invalid.
  - `test_rapid_check_uses_the_raised_block`: a safe position inside the
    raised block is refused. That position is above the block when the table
    is at 0.
  - `test_kerf_test_slots_sit_on_the_table` (`dz` = +25 and −15): the
    kerf-test program follows the same shift rule. Each slot comment's `z`
    moves by `dz`.

## 2. What was verified, and how

- `python check.py cam` passes: ruff, mypy (strict) and 69 tests (59 before,
  plus 10).
- With `table_z = 0`, all 59 existing cam tests pass. The only change to them
  is the one fixture field described above.
- All three worked examples are byte-identical. I checked this two ways:
  - with `cmp` of freshly generated files in `output/table-z/` against
    `examples/gcode/`;
  - with `test_worked_examples.py`.
- `python check.py` on `main` (`a26fa94`), before any change: 160 passed.
- I checked that the new tests catch each fix being undone, by reverting one
  fix at a time and running `test_table_z.py`:
  - rapid check reverted: 1 test fails;
  - validity range reverted: 8 fail;
  - kerf-test slot heights reverted: 2 fail.

  Each fix was then restored, and the cam check passes again.

## 3. What was not verified

1. **The simulator rejects `table_z`.** On this branch, 19 of 101 sim tests
   fail with `table_z: unknown key`. That message comes from
   `foamsim.errors.InputError` when it reads
   `examples/profiles/sample-machine.yaml`. This is correct behaviour under
   profile §1, where unknown keys are an error. I did not open `src/foamsim/`
   or `tests/sim/`. The simulator needs to read `table_z`, and probably to
   use it wherever it uses the block's `z` range. Until it does, merging this
   branch breaks the sim check on `main`.
2. **Homing settings.** `settings.txt` on `main` has `$22=0` and `$27=5.000`.
   The file's own comments call these "Homing cycle" and "Homing switch
   pull-off distance". I have not checked how these grblHAL settings produce
   the 1 mm figure, or whether they do. I changed nothing outside my folders.
3. **The `G10 L20 P1` command with all four axis words in one line** (§3.5,
   step 2). The old text used one axis at a time. I believe the multi-axis
   form is accepted, but I have not checked it against grblHAL source or
   documentation, and have not tried it on the controller.
4. **The squareness check and the `table_z` measurement** are written but
   have not been done on the machine.
5. **Paper thickness** in the `table_z` measurement is not subtracted. The
   old z-zero procedure did not subtract it either.

## 4. Open questions for the director

1. **Which side is the operator's side?** Profile §3.1 puts the operator at
   the front, and +x points toward the front.
   - If the horizontal axes home 1 mm off the front stop, every reachable
     `x` is at or below about +1 mm, so all real positions are negative.
   - The sample profile (`x` 0 to 600) and every worked example (block at
     `x` 100 to 400, safe position at `x` 550 to 600) would then be
     unreachable.
   - I kept them unchanged: the brief asked for byte-identical examples, and
     the sample profile's numbers are marked as not measured.

   Please confirm the homing side. Then choose one of:
   - (a) accept negative `x` and later move the examples;
   - (b) home the horizontal axes at the back stop;
   - (c) reverse the direction of axes 1 and 3. This changes the frame,
     which the brief asked me to keep.
2. **Tolerances** are needed for the squareness check (§3.3) and for "axes 2
   and 4 must agree" (§3.4). The format states no number.
3. **Format version.** A required field was added but the profile stays at
   `version: 1`, so an old profile is now refused with `table_z: missing`.
   Should this be version 2?
4. **Brief 1 commissioning procedure.** It refers to the operator's steps,
   which moved from §3.4 to §3.5. It also refers to setting zeros against the
   reference face, which is gone. Its owner needs to update it. I did not
   check its text in `firmware/`.
5. **Simulator.** It must accept and use `table_z` before this branch is
   merged (§3, item 1).

## 5. Every sentence changed in the formats

### `docs/formats/machine-profile.md`

**§3 introduction.**

Old:
> The controller's own machine coordinates (after homing) are never used by
> the software; the link between them and G54 is the G54 offset set by the
> operator (§3.4).

New:
> The controller's own machine coordinates are never used by the software;
> the link between them and G54 is the G54 offset set by the operator (§3.5).

**Old §3.2 "Physical references" removed in full:**

> The work frame is tied to two physical references, which must be in place
> before the zeros are set:
>
> - **The table top.** A flat surface under the wire. Its top is the
>   **z reference plane**.
> - **The x reference face.** A straight, flat, vertical face fixed to the
>   table near the back, facing the front, running from the left tower side to
>   the right tower side (for example a straight aluminium bar clamped to the
>   table, or the table's machined back edge). It must be perpendicular to the
>   table top and square to both towers, i.e. perpendicular to both carriage
>   planes. The longer it is, the more accurately it sets the zero; ideally it
>   reaches close to each tower.

**§3.3 "Axes and zeros" is renumbered §3.2.**

Axis table, `z` row:
- Old: `z = 0` is the table top (the z reference plane).
- New: `z = 0` is the homed position of the vertical axes (axes 2 and 4).

Axis table, `x` row:
- Old: `x = 0` is the x reference face.
- New: `x = 0` is the homed position of the horizontal axes (axes 1 and 3).

The check `x = y × z`, the carriage planes, the wire points and the
directions are unchanged.

"Zero of each axis" is replaced. Old:

> Each axis has its own zero. The four zeros are defined by where the wire
> is, not by the controller:

This was followed by the four-row table "Zero is where / Set it like this"
(the wire against the x reference face for axes 1 and 3; the wire on the
table top with a paper strip for axes 2 and 4). Then came:

> Because the wire is straight, if it touches a flat face at two points far
> apart it lies in that face's plane, and so do both wire points. The x zeros
> must be set with the wire clear of the table and the z zeros with the wire
> clear of the x reference face.

New:

> The zero of each axis is its homed position:

This is followed by a four-row table: axes 1 and 3 home "1 mm off the
operator-side stop"; axes 2 and 4 home "1 mm off the bottom stop". Then:

> These positions are fixed by the machine's construction and its homing
> set-up, not by this format. The accuracy of every zero is the homing
> repeatability of its axis.

**New §3.3 "Squareness":**

> The wire is square to the towers by construction: when `a1 = a3` and
> `a2 = a4`, the wire is perpendicular to both carriage planes (parallel to
> `y`). Every tool assumes this. The software cannot check it.
>
> **One-time check** (cold wire, after the machine is built and whenever a
> tower, a carriage or a wire point is moved). Check at two positions far
> apart: the homed position, and all four axes at the same reading near the
> far end of their travel. At each position and at each tower, hold an
> engineer's square with its stock against the carriage plane and its blade
> along the wire, and check that the wire runs along the blade, seen from
> above and seen from the front. A gap `g` at the end of a blade of length `b`
> means the wire is out of square by about `g / b` radians, which moves its
> far end by about `wire_span · g / b`. The measurement of `table_z` (§3.4) is
> a second check of the vertical part: the two vertical axes must agree.

**"Consequences that every tool relies on", second bullet.**

Old:
> The table top is at `z = 0`, so a foam block resting on the table has its
> bottom face at `z = 0`.

New:
> The table top is at `z = table_z` (§3.4), so a foam block resting on the
> table has its bottom face at `z = table_z`.

The first bullet is unchanged.

**New §3.4 "Table height":**

> The table is a flat surface under the wire. The foam block rests on it. Its
> top is the plane `z = table_z`, where `table_z` is a profile field (§4). It
> may be negative, zero or positive.
>
> **Measuring `table_z`** (cold wire, after homing and setting G54, §3.5):
>
> 1. Move both horizontal axes to the same reading, with the wire over the
>    table along its whole length.
> 2. Lower the wire, one vertical axis at a time, until a strip of paper drags
>    under it at that axis's end of the table but does not slide freely. Do
>    this at both ends of the table.
> 3. Read axes 2 and 4. They must agree. If they do not, the table top is not
>    parallel to the wire, or the wire is not square (§3.3); correct this
>    before cutting.
> 4. Enter the reading as `table_z`.

**Old §3.4 "What the operator must do" is renumbered §3.5.** Its steps 1 to 3
are replaced.

Old:
> 1. Fit the two physical references (§3.2).
> 2. Set the four zeros (§3.3) by storing them as the G54 offset of each axis
>    in the controller. With the wire held at the zero position of an axis,
>    the grblHAL command `G10 L20 P1 <letter>0` makes that axis read 0 in G54.
> 3. Re-check all four zeros after every homing cycle, until the homing
>    repeatability of each axis has been measured and is known to be small
>    enough for the parts being cut. After that, re-check them whenever a
>    reference, a wire point or the wire is changed.

New:
> 1. Home the machine.
> 2. With every axis at its homed position, make all four axes read 0 in G54:
>    the grblHAL command `G10 L20 P1 <letter1>0 <letter2>0 <letter3>0
>    <letter4>0` (the axis letters of §5) stores the G54 offsets.
> 3. Do the squareness check (§3.3) once.
> 4. Measure `table_z` (§3.4) and enter it in the profile. Measure it again
>    whenever the table or the wire is changed.
> 5. Until the homing repeatability of each axis has been measured and is
>    known to be small enough for the parts being cut, repeat step 2 of §3.4
>    after every homing cycle and check that axes 2 and 4 still read
>    `table_z`.

"The software cannot check any of this. The Brief 1 commissioning procedure
refers to this section." is unchanged.

**Top-view diagram:** the line `═══ x reference face (x = 0) ═══` is removed.

**§4 Top-level fields.**
- In the YAML outline, `table_z: 0.0` is added after `wire_span`.
- New table row: "`table_z` | number, mm | yes | G54 `z` of the table top
  (§3.4). Any value: negative, zero or positive."
- Removed: "There is no table-height field. The table top is `z = 0` by
  definition (§3.3)."

**§5 Axis entries.**

Old:
> Measure them after the zeros are set: jog the axis to the end of its safe
> travel at each end and read its G54 position.

New:
> Measure them after homing and setting G54 (§3.5): jog the axis to the end
> of its safe travel at each end and read its G54 position.

### `docs/formats/job-file.md`

**§5 Foam block, block extent.**
- Old line: `z from 0 to block.height`
- New line: `z from table_z to table_z + block.height`

**§5.**

Old:
> The block rests on the table, whose top is `z = 0` (machine profile §3.3).

New:
> where `table_z` is the G54 `z` of the table top, from the machine profile
> (machine profile §3.4). The block rests on the table.

**§10.6 Safe position.**

Old:
> The CAM rejects the job if either rapid move would pass through the block
> at any plane between the two foam faces.

New:
> The CAM rejects the job if either rapid move would pass through the block
> (§5, from `z = table_z`) at any plane between the two foam faces.

§6.7 (the validity rule on the block's `x` and `z` range) refers to §5 and is
unchanged. It now follows from the new §5.
