# Commissioning procedure (ioSender)

Carry this out once on the assembled machine, after the bench test
(`bench-test.md`) has passed. Hot wire supply **off** for every step,
including step 8 (the zeros are set with a cold wire). No foam on the table.

Work in ioSender's console (MDI) unless a step says otherwise. After each
step write the final values into the machine record (this file's last
section) and, where named, into the machine profile
(`docs/formats/machine-profile.md`). The profile is the only record of the
machine that the software reads.

**Axis letters.** This firmware names the four axes X, Y, Z, A and nothing
else (axis 4 can be renamed U only with a rebuild; see the Brief 1 report).
The machine profile's `letter` fields must therefore be `"X" "Y" "Z" "A"` for
axes 1 to 4, which is the profile default. Check (a) in step 8.8 confirms it.

| Axis | Letter | Tower | Moves | Positive direction |
| --- | --- | --- | --- | --- |
| 1 | X | left | horizontal | toward the front |
| 2 | Y | left | vertical | up |
| 3 | Z | right | horizontal | toward the front |
| 4 | A | right | vertical | up |

## 0. Before you start

1. The bench test has passed, and `$14` reads 6 (`$14`). With `$14=70` there
   is no e-stop.
2. **E-stop cuts wire heat** (required, `wiring.md`). Leave the wire clear of
   everything and connect a voltmeter across the hot-wire supply output.
   Switch the hot-wire supply on: the meter shows its voltage. Press the
   e-stop: the meter must drop to 0 V. Release the e-stop and switch the
   supply off again. If the voltage stays, stop: the machine must not be run
   with a heated wire until this works.

## 1. Steps per mm (`$100`–`$103`)

1. For each axis: `$10n = 200 × 16 / lead`, with `lead` from the machine
   profile (mm/rev) and 16 microsteps (`$15n`). 200 full steps per revolution
   is the 1.8° step angle that every Creality 42-40 listing gives; check the
   motor label.
2. Check: mark the carriage, jog 100 mm (`$J=G91 G21 X100 F300`), measure.
   If it moved `d` mm: new `$100` = old `$100` × 100 / `d`. A difference of
   more than 1 % means the lead in the profile is wrong: fix the profile, not
   only `$10n`.
3. Repeat for Y, Z, A.

## 2. Motor current (`$140`–`$143`, `$210`–`$213`)

1. Read the rated current per phase on each motor's label. The project could
   not verify it (listings give 1.0 A or 1.5 A).
2. Set `$14n` to at most 70 % of the label value, in mA RMS. Baseline is 700.
3. Check with `M122` that `Set current` shows the new value for each axis.
4. Run the axis back and forth over its travel at `$11n` for 10 minutes
   (`$J=G91 G21 X300 F1200`, then `X-300`, repeated). The motor case must stay
   cool enough to hold a hand on. If not, lower `$14n`.
5. `$21n` (standstill current, %) stays at 50 unless the wire tension pulls a
   carriage out of position at rest. Check: after a move, the carriage
   cannot be moved by hand.

## 3. Direction (`$3`)

For each axis jog +10 mm (`$J=G91 G21 X10 F300`) and watch the carriage.
It must move in the positive direction in the table above. If it does not,
add that axis's bit to `$3` (X 1, Y 2, Z 4, A 8). Example: Y and A wrong →
`$3=10`. Re-check all four.

## 4. Speed and acceleration (`$110`–`$113`, `$120`–`$123`)

1. **Check (b): each axis's `max_rate` in the machine profile must be at or
   below its `$11n`.** grblHAL silently slows any move that would take an
   axis above `$11n` (planner.c:326-327). A G93 move then takes longer than
   its F word commands, and the cut runs slower than programmed, which
   changes the kerf. Repeat this check in step 8.8 after the profile is
   final.
2. Find the limit: jog the full travel at increasing rates (`F600`, `F900`,
   `F1200`, …). At the first rate where the motor stalls, skips or sounds
   rough, stop. Set `$11n` to 70 % of the last good rate. If that is below
   the profile's `max_rate`, lower `max_rate` in the profile.
3. Acceleration: with `$11n` set, increase `$12n` (mm/s²) from 100 in steps
   of 50, jogging 20 mm back and forth, until the axis skips. Set 50 % of
   the last good value.
4. Check for lost steps: mark, jog the full travel back and forth 10 times
   at `$11n`, return to the mark. The carriage must be back on the mark.

## 5. Stall sensitivity (`$200`–`$203`, `$220`–`$223`, `$24`, `$25`)

Sensorless homing uses the TMC2209's StallGuard4. The plugin's own README
says sensorless tuning is not trivial; set aside time for it. If an axis
cannot be made reliable, fit a normally closed switch to it instead
(`wiring.md`, no rebuild needed).

Background, from the plugin source: during homing the plugin switches the
driver to StealthChop (`tmc2209hal.c:159`), writes `$20n` (seek phase) or
`$22n` (locate phase) unchanged to the SGTHRS register (`:167`), and enables
stall output only above 2/3 of the homing rate (`TCOOLTHRS`, `:165`). Per the
TMC2209 datasheet (not re-checked in this session) a stall is flagged when
SG_RESULT ≤ 2 × SGTHRS, so a **higher** `$20n` is **more** sensitive.

1. Pick the seek rate `$25`. StallGuard needs speed; start at 400 mm/min
   (baseline). Set the locate rate `$24` to at least 100 mm/min to begin with.
2. Watch live StallGuard values for one axis: `M122 S1 P100 X`. The board
   prints `[SG:<value>]` about every 100 ms while X steps.
3. Jog X slowly through free travel at the seek rate
   (`$J=G91 G21 X-50 F400`). Note the lowest `[SG:]` value seen, `SGfree`.
4. Jog gently into the mechanical stop at the same rate with the current
   reduced for safety (`$140` at about half, then restore), and note the
   value at the stop, `SGstall` (it drops toward 0).
5. Set `$200` so that 2 × `$200` lies between them, nearer `SGstall`:
   start at `$200 = (SGstall + SGfree) / 4`, rounded down. Then turn the
   report off: `M122 S0`.
6. Test by homing that axis alone (`$HX`, step 6). Too low (not
   sensitive enough): it grinds against the stop, so stop it with the e-stop
   or reset at once and raise `$200`. Too high: it stops in free travel, so
   lower `$200`.
7. Repeat steps 2–6 at the locate rate `$24` for `$220`. If the locate phase
   is unreliable at any `$220`, raise `$24` (StallGuard is weak at low speed).
8. Do the same for Y (`$201`, `$221`), Z (`$202`, `$222`), A (`$203`, `$223`).

## 6. Homing (`$22`, `$23`, `$27`, `$43`–`$45`)

Baseline: phase 1 homes the vertical axes Y and A up (`$44=10`); phase 2 homes
the horizontal axes X and Z toward the back (`$23=5` sets X and Z negative,
`$45=5`). Lifting first keeps the wire clear of the table and the x reference
face. If the machine's stops make another order safer, change `$44`, `$45`
and `$23`, and record why.

1. Home each axis alone first: `$HY`, `$HA`, `$HX`, `$HZ`. Each carriage must
   move toward the stop named above, stop at the stop, pull off by `$27`
   (2 mm), and then do one slow locate pass (`$43=1`).
2. `$27` must leave the carriage clear of the stop before the locate pass
   starts. If a locate pass begins already stalled, raise `$27`.
3. Full cycle: `$H`. Expect `ok`. Failure gives `ALARM:` 6 to 9 or 18; record
   the number.
4. Repeatability. The controller's own position cannot show this, because
   homing resets it. Fix a dial indicator (or a block and feeler gauges)
   against each carriage at a chosen machine position, for example 50 mm from
   home. Then 10 times: `$H`, jog to that machine position
   (`G53 G0 X.. Y.. Z.. A..`), read the indicator. The spread of the 10
   readings is the homing repeatability of that axis. Record it.
5. When homing works on all four axes, set `$22=7` (adds "homing required at
   start-up"). From now on the board starts in `ALARM:11` until `$H` succeeds.

## 7. Travel and soft limits (`$130`–`$133`, `$20`)

1. After `$H`, jog each axis slowly away from its home stop to the far end,
   stopping 2 mm before the far mechanical stop. The distance from home is
   that axis's travel. Read it from the `MPos` difference.
2. Set `$13n` to that travel. Homing searches up to 1.5 × `$13n`
   (`machine_limits.c:306`), so this also limits how far a failed homing
   search can drive.
3. `$20=1` (soft limits on). Check: jog toward each far end; the jog must stop
   short of the stop, and a G1 beyond it must give `ALARM:2` (soft limit)
   without moving.
4. `$21` (hard limits) stays 0 with sensorless homing: the TMC2209 also drives
   DIAG on driver errors. Turn it on only for axes converted to switches.

## 8. Set the four G54 zeros

This step follows `docs/formats/machine-profile.md` §3. It must be the last
step: everything above changes the machine coordinates, and G54 ties the
program coordinates to them.

**8.1 References.** Fit the two physical references (§3.2):

- **The table top**: a flat surface under the wire. Its top is the z
  reference plane.
- **The x reference face**: a straight, flat, vertical face fixed to the
  table near the back, facing the front, running from the left tower side to
  the right tower side. It must be perpendicular to the table top and square
  to both towers. The longer it is, the better; ideally it reaches close to
  each tower.

**8.2 Home** the machine: `$H`. The wire must be cold.

**8.3 x zeros, axes 1 (X) and 3 (Z).** Keep the wire clear of the table
(raise Y and A if needed). Jog X and Z until the wire lies against the x
reference face along its whole length, touching it at both ends of the face.
Then send:

```text
G10 L20 P1 X0
G10 L20 P1 Z0
```

`G10 L20 P1 <letter>0` makes that axis read 0 in G54 at the current position.
The offsets are stored in the controller's settings storage
(`gcode.c:4253`), so they survive a power cycle.

**8.4 z zeros, axes 2 (Y) and 4 (A).** Jog X and Z toward the front until the
wire is clear of the x reference face. Jog Y and A down until the wire lies
on the table top along its whole length, just touching: a strip of paper
drags under the wire at both ends of the table but does not slide freely.
Then send:

```text
G10 L20 P1 Y0
G10 L20 P1 A0
```

**8.5 Check.** Send `G54`. Jog Y and A up 10 mm and X and Z toward the front
by 10 mm. The work position (ioSender's DRO) must read X10 Y10 Z10 A10. Jog back to
X0 Y0 Z0 A0: the wire must again just touch the table (paper test) and,
after raising Y and A, sit against the x reference face when X and Z are 0.

**8.6 Record.** Send `$#` and write down the `[G54:...]` line.

**8.7 Profile limits.** Now measure the profile's `min` and `max` for each
axis (`machine-profile.md` §5): jog the axis to the end of its safe travel at
each end and read its G54 position (the work position in ioSender's DRO).
Enter them in the profile.

**8.8 Final profile checks.** With the profile filled in, check both and
record the result:

- **(a) Axis letters.** The profile's `axes[0..3].letter` are `"X"`, `"Y"`,
  `"Z"`, `"A"` in that order. This firmware accepts no other letters, and a
  program in other letters would fail or move the wrong axes.
- **(b) Maximum rates.** For each axis n, the profile's `max_rate` is at or
  below `$11n` (send `$$` to read them). Otherwise grblHAL silently slows any
  G93 move that would exceed `$11n` (planner.c:326-327), and the cut takes
  longer than programmed.

### When the zeros must be re-checked

(`machine-profile.md` §3.4, item 3.)

- **After every homing cycle**, until the homing repeatability of each axis
  has been measured (step 6.4, and cut 4 of `docs/first-cut-plan.md`) and is
  known to be small enough for the parts being cut.
- After that: **whenever a reference, a wire point or the wire is changed.**
  That includes moving, refitting or re-clamping the x reference face,
  changing the table top, changing or re-tensioning the wire, and anything
  that moves the point where the wire leaves a carriage.

Re-checking means repeating 8.3 to 8.5 with a cold wire. If a zero has moved,
set it again with `G10 L20 P1 <letter>0`.

## Machine record

Fill in after commissioning. Keep a copy of `$$` and `$#` output with it.

| Item | X | Y | Z | A |
| --- | --- | --- | --- | --- |
| lead (mm/rev), from profile | | | | |
| `$10n` steps/mm | | | | |
| `$11n` max rate (mm/min) | | | | |
| `$12n` acceleration (mm/s²) | | | | |
| `$13n` travel (mm) | | | | |
| `$14n` current (mA RMS) / motor label (A) | | | | |
| `$20n` / `$22n` StallGuard thresholds | | | | |
| homing repeatability, 10 cycles (mm) | | | | |
| G54 offset (from `$#`) | | | | |

`$3` = ____  `$23` = ____  `$24` = ____  `$25` = ____  `$27` = ____  Date: ________
