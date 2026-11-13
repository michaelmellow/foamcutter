# Foam cutter: state of play, discrepancies and next steps (handoff for an agent)

Written 2026-11-13. Read this whole file before touching anything. It reconciles three sources that disagree:

| Source | Where | What it is |
| --- | --- | --- |
| **A. Owner's handoff** | `C:\Users\mined\Downloads\hotwire-cutter-handoff.md` | What was actually done on the real machine: firmware flashed, settings, homing experiments. The most up-to-date source on the hardware. |
| **B. Colleague's project** | `C:\Users\mined\Documents\foamcutter` (git, branches `main`, `brief-1-firmware`, others) | Specs, CAM core (`foamcam`), simulator (`foamsim`), firmware build and commissioning docs. Run as "briefs" reviewed by a "project director". |
| **C. Owner's quick slicer** | `C:\Users\mined\Documents\foam_slicer` (`main.py`, `probe.py`, `machine.json`) | A single-file DXF-to-G-code tool plus a block-probing script, written in one session. Tested only against generated DXFs and a fake controller. |

Nothing in B or C has run on the machine. Nothing in B was changed while writing this.

---

## 1. Decision just made: homing by hitting the stops

The owner homes all four axes at once by driving them into their mechanical stops at low current, letting the motors skip, re-seating against the stops, then backing off and zeroing. This is the macro (the same one as in the owner's handoff, A, section 4):

```gcode
M906 X200 Y200 Z200 A200            ; TMC2209 current 200 mA on all axes (live, not saved)
G91                                 ; relative mode
G1 X-300 Y-300 Z-300 A-300 F2000    ; drive 300 mm toward the negative stops; motors skip there
G4 P1                               ; wait 1 s
G1 X20 Y20 Z20 A20 F500             ; back off 20 mm
G1 X-25 Y-25 Z-25 A-25              ; push 25 mm back in to re-seat against the stops (F500)
G4P1                                ; wait 1 s
G1 X1 Y1 Z1 A1                      ; back off 1 mm
G90                                 ; absolute mode
G10 L20 P1 X0 Y0 Z0 A0              ; G54 zero = here, 1 mm off each negative stop
M906 X500 Y500 Z500 A500            ; back to running current 500 mA
```

It runs from an ioSender macro button; ioSender macros cannot be called by name from the MDI line. It replaces the stall-detection homing in B. Wear and noise are accepted costs.

Consequences: G54 zero is 1 mm from the negative end of every axis, so all job coordinates are positive. Because both towers are driven into their stops, the paired axes (X with Z, Y with A) start square to each other. The macro assumes negative is the stop direction on every axis after `$3` (direction invert).

### Things to check or fix in this macro

1. **Vertical axes going down.** Driving Y and A in the negative direction moves the wire **down**, if negative is down after `$3`. The wire can hit the table or a foam block before the carriages reach their stops. B homes the verticals **up** for exactly this reason (`$23=5`, `firmware/commissioning.md` §6). Either make sure the bottom stops hold the wire above the table (and nothing is on the table during homing), or flip the vertical directions so the stop is at the top.
2. **All four axes move at once.** The wire moves diagonally down and back toward the corner. With a block on the table the wire can sweep through it. Clear the table, or split the move: verticals first (if homing up), then horizontals.
3. **300 mm must be more than the travel.** If an axis has more than 300 mm of travel from where it starts, it never reaches the stop and is zeroed in the wrong place without any error. Travel per axis is still unmeasured.
4. **Early stall at F2000.** At 200 mA and 2000 mm/min a motor may stall from friction before reaching the stop. The re-seat (back 20, in 25) only corrects a shortfall of up to 5 mm. Check after homing that every carriage is really 1 mm off its stop. If not, lower the seek feed or raise the homing current a little.
5. **Repeatability is unknown.** B's first-cut plan has a cut for exactly this (cut 4, "homing repeatability"). It decides whether the stop is a good enough reference.
6. **Flash wear.** This firmware has no EEPROM enabled, so every `G10 L20` writes the MCU flash (B, `docs/reports/brief-1-report.md` §6). One write per homing is probably acceptable, but be aware of it.
7. **The vertical screws are not self-locking.** At 200 mA a vertical carriage holds less. Keep the wire weight and tension in mind while homing.

### Firmware settings this implies (different from B's baseline)

| Setting | B's baseline (`firmware/baseline-settings.txt`) | Needed for macro homing |
| --- | --- | --- |
| `$22` homing | `3`, later `7` ("homing required at start-up") | Homing cycle **off** (`0`). With bit 2 set the board starts in `ALARM:11` and only `$H` clears it, which the macro does not do. Verify the bit meanings in grblHAL before changing. |
| `$339` sensorless homing axes | `15` | `0` (not used) |
| `$20` soft limits | `1` after commissioning step 7 | Stays `0`. Grbl-family soft limits rely on a completed `$H`; check whether grblHAL allows them without it. Until then **only the software checks** (profile `min`/`max` in B, `travel_mm` in C) protect the travel. |
| `$21` hard limits | `0` | `0` (no switches) |
| `$140`-`$143` current | `700` mA | `500` mA running (A). Rated motor current is still unknown: read a motor label. B's report says listings give 1.0 A or 1.5 A. |
| `$23` homing direction | `5` | Not used by the macro; directions are in the macro itself. |
| DIAG jumpers | fitted (sensorless) | Not needed. Leave them as they are unless hard limits are ever turned on. |

---

## 2. The zero (G54) conflict: must be resolved first

This is the biggest discrepancy and decides whether any G-code lands in the right place.

- **B** defines G54 by physical references (`docs/formats/machine-profile.md` §3.2-3.4, `firmware/commissioning.md` §8): `x = 0` is a straight reference bar at the back of the table, `z = 0` is the table top, set by touching the cold wire to them and sending `G10 L20 P1 <axis>0`. Every program from B's CAM uses those coordinates. B says to re-check the zeros after every homing.
- **The macro** ends with `G10 L20 P1 X0 Y0 Z0 A0`, which moves the G54 zero to 1 mm off the stops **every time it runs**. After homing, B's programs would be offset by the distance between the stop and the reference bar or table.

Options (owner or director to decide):

1. **Keep B's frame, home to the stops (recommended).** Set B's zeros once from the references. Read the G54 position of each axis at the "1 mm off the stop" point and write those numbers into the macro instead of 0: `G10 L20 P1 X<x> Y<y> Z<z> A<a>`. Homing then restores B's reference zeros from the stops. Accuracy is limited by homing repeatability (point 4 above).
2. **Make the stop the zero.** Change B's machine-profile §3 (an approved format, which needs the director's approval) so `x`/`z` are measured from the stops. Then the foam block position must come from somewhere else, for example probing (C's `probe.py`) or a measured fence.

Also check: B's frame has `x` pointing **toward the front** and `x = 0` at the **back**. So the X/Z negative stop should be at the back of the machine for B's convention to hold without negative coordinates. Set `$3` (direction invert) so that is true.

---

## 3. Firmware: what exists and what to do with it

### What is on the board now (from A)

- grblHAL from the **web builder**: STM32H7xx driver, BTT SKR 3 board, **non-bootloader** entry. Flashed over **USB DFU with STM32CubeProgrammer** (application at `0x08000000`). This has most likely erased BTT's SD-card bootloader.
- 4 axes, one motor each, native USB, TMC2209 plugin. Spindle 1 PWM possibly enabled (not confirmed).
- `M906` works for live current changes. `$140`-`$143` = 500, 200 during homing.
- E-stop input polarity was **inverted** to clear `ALARM:10` with nothing wired.
- `$20 = 0`, `$21 = 0`. Steps/mm 400 (T8 lead 8 mm, 16 microsteps), not yet verified by a measured move.
- Unknown: the exact MCU (H743 or H723), whether the A axis is set as rotary (`$376`).

### What B built (`firmware/` on `brief-1-firmware`, partly on `main`)

- `firmware/build.py` + `foamcutter.ini`: reproducible PlatformIO build of `dresco/STM32H7xx` pinned to commits, board `BOARD_BTT_SKR_30`, 25 MHz crystal, `N_AXIS=4`, TMC2209 over UART, `DEFAULT_AXIS_ROTATIONAL_MASK=0` (all axes linear).
- Two binaries in `firmware/release/`, **H743 and H723**, both linked for the **BTT bootloader at `0x08020000`** and flashed from an SD card (`firmware/README.md`).
- Wiring sheet, baseline settings, bench test (`firmware/bench-test.md`), commissioning procedure, report. **Not bench-tested.** The director approved it for bench testing.
- Open questions from the director: H723 or H743, motor rated current, homing direction.

### Plan for the firmware

1. **Keep the web-builder build that is on the board.** It works, and macro homing needs no sensorless features. Do not flash B's binaries as they are.
2. **Do not flash B's `.bin` over DFU.** It is linked for `0x08020000` and will not boot from `0x08000000`. B's SD-card procedure needs the BTT bootloader, which is probably gone. Using B's build would need either the BTT bootloader restored or a non-bootloader environment added to `firmware/build.py` / `foamcutter.ini` (STM32H7 linker script without `_BL_`).
3. **Record what is flashed.** Read `$I` and `$$` from the board and commit them, so the repo describes the real firmware. In particular: the MCU, `$376` (rotary mask; if A is rotary, `G94` feeds are wrong, `G93` is unaffected), `$14` (e-stop polarity), `$22`, `$339`.
4. **Optional later:** make B's build reproduce the web-builder configuration (non-bootloader) so the firmware can be rebuilt from the repo.
5. **Run B's bench test**, adapted: skip the sensorless and `$H` parts, keep `$I`, `M122`, the 100 mm jog check of steps/mm, the timed 4-axis `G93` move, and the e-stop tests.
6. **E-stop.** Once a normally closed e-stop is wired, restore normal polarity (B: `$14=6`, never run with the inverted value). B requires the e-stop to also open the hot-wire supply; the controller e-stop only stops motion.

---

## 4. All discrepancies

### 4.1 Machine and firmware

| # | Topic | A: owner's handoff / reality | B: colleague's project | Action |
| --- | --- | --- | --- | --- |
| 1 | Homing | Stall detection abandoned (start-up current spike false-triggered). Now: hit the stops at low current (section 1). | Stall-detection homing everywhere: CLAUDE.md, briefs, `$339=15`, `$22=3`/`7`, DIAG jumpers, tuning in commissioning §5-6, risk table in `docs/work-package-briefs.md`. | Update B's docs and baseline to macro homing (section 1 table). |
| 2 | G54 zero | Macro sets G54 = 1 mm off each stop. | Zero at reference bar and table top, set by touch-off. | Decide (section 2). |
| 3 | Homing direction | All axes negative. | Verticals up, horizontals toward the back (`$23=5`). | Home verticals up or check table clearance. |
| 4 | Flashing | Web builder, no bootloader, USB DFU. | SD card via BTT bootloader, `0x08020000`. | Keep current firmware; do not DFU B's `.bin`. |
| 5 | MCU | Not confirmed. | Both H743 and H723 built; BTT manual shows H743, brief says H723. | Read the chip marking or `$I`. |
| 6 | Motor current | 500 mA running, 200 mA homing. | 700 mA baseline; rated current unverified. | Read the motor label. |
| 7 | E-stop | Polarity inverted, nothing wired. | `$14=6` (NC to GND), `$14=70` only during the bench test. Must also cut wire heat. | Wire NC e-stop, restore polarity. |
| 8 | Soft limits | Off. | On after commissioning step 7 (needs `$H`). | Stay off; rely on software checks. |
| 9 | A axis rotary | Unverified. | Forced linear in the build. | Check `$376` on the real board. |
| 10 | Wire heat | Lab supply by hand; maybe later `M3 S` via spindle PWM and a MOSFET. | Controller never switches the wire; G-code may contain no M-codes (`job-file.md` §13). | Owner to decide; B's format would need a change. |
| 11 | Tower spacing | 690 mm (owner, 2026-11-11). | Sample profile 900 mm (marked as sample). | Put 690 in the real profile as `wire_span`. |
| 12 | Settings storage | — | No EEPROM enabled: settings and G54 in MCU flash. | Note for flash wear (section 1). |
| 13 | Steps/mm | 400, unverified. | 400 (lead 8), unverified. | Agree; verify with a 100 mm jog. |

### 4.2 Software: C (quick slicer) vs B (CAM core)

| Topic | C: `foam_slicer/main.py` | B: `foamcam` + `foamsim` |
| --- | --- | --- |
| Input | Two **DXF** outlines + span | `.dat` airfoils (Selig, Lednicer) + YAML job with chord, twist, position. **No DXF reader.** STEP front end (`foamstep`, Brief 4) is empty. |
| Machine config | `machine.json` (root/tip tower naming, travel, tower spacing) | `machine-profile.md` YAML: axes 1-4 left/right, `wire_span`, min/max, `max_rate`, kerf tables, wire-angle and speed-ratio limits |
| Frame | Stop-based G54, DXF origin at the probed block's front-bottom corner | Reference-face G54 (section 2), block given in the job and resting on the table |
| Block position | Probed by jogging the wire (`probe.py` → `block.json`) | Typed into the job (`block.x`, `length`, `height`) |
| Sync | Arc length, LE/TE anchors, fixed point count with cosine spacing | Arc length between fixed pairs (S, LE, TE, slits, features), `max_segment_length` |
| Kerf | One constant | Speed-dependent tables, per edge |
| Feed | `G93`, faster face at the cut speed, capped by axis speed | `G93`, faster face at `cut_speed`; refuses if an axis exceeds `max_rate` |
| Checks | Travel, table clearance, block fit | Soft limits, wire angle, speed ratio, axis rate, rapids through the block |
| Features | None | Spar holes with slits, open cuts, mirror, twist, trailing-edge thickening |
| G-code | `M5`/`M30`, optional `M3`, comment on the `G93` line, 3 decimals, rapids with partial axis words | Strict subset (`job-file.md` §13): 4 decimals, all four axis words, no M-codes. **foamsim may reject C's output.** |
| Tests | None | ~140 tests, CI, an independent simulator |
| Known weakness | Arc-length pairing cuts across `.dat`/DXF vertices | Same: foamsim measured 0.107-0.144 mm, above its 0.05 mm limit (Brief 5 report) |

Recommendation: carry the work forward in **B**. C's only unique parts are the DXF reader and the probing; port them into B as a shape source (DXF → `shape.points`) and as a way to fill in the job's `block`, if wanted.

### 4.3 Environment

- The owner's machine runs **Python 3.14.5** on Windows. `matplotlib` had **no wheel** for it (install failed in C's pipenv). B's `sim` group needs matplotlib, so `foamsim` may not install there. Check before relying on it. B targets Python 3.11+.
- `pipenv` 2024.4.1 failed to lock with matplotlib; `ezdxf`, `numpy`, `pyserial` installed fine.

---

## 5. Rules if you work in B (`foamcutter`)

From `CLAUDE.md` there:

- Work on a named branch, never commit to `main`. Sessions never merge; the owner merges after the director reviews.
- Do not edit `docs/briefs/`. Do not edit the approved `docs/formats/` without asking first. The zero change in section 2, option 2, is such a change.
- `python check.py <area>` must pass. Do not weaken tests.
- Finish with `docs/reports/brief-N-report.md`: what was built, verified, not verified, open questions.
- Do not invent hardware facts; say when something is unverified.

## 6. Suggested order of work

1. Decide the zero question (section 2) with the owner.
2. Read `$I` and `$$` from the board; record the real firmware and settings.
3. Check the homing macro against the machine (vertical direction and table clearance, 300 mm vs travel, early stalls at F2000), and apply the settings in the section 1 table.
4. Measure travel, tower spacing (690 mm), max rates; fill in a real machine profile for B.
5. Update B's firmware docs (baseline settings, commissioning §5-§8, wiring DIAG notes, CLAUDE.md homing line) to macro homing. This touches the brief-1 branch; coordinate with the director.
6. Run the adapted bench test, then B's first-cut plan (`docs/first-cut-plan.md`), cold air cuts first.
7. Optional: DXF input and block probing in B.

## 7. Safety reminders

- Wire cold and power off for homing, probing and the first run of every program.
- The e-stop must cut both motor motion and wire heat.
- ioSender's preview is meaningless for this kinematics; use the project's own preview or foamsim.
- Ventilate: hot-wire fumes from polystyrene.
