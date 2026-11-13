# Brief 1 report: firmware build

Branch: `brief-1-firmware`. `main` was merged in first (a fast-forward to
`92af092`), which brought in the formats, examples and the other areas'
code. The earlier WIP commit `d5e9a2c` held the build tooling and binaries;
this session added everything else and the director's three additions.

Source citations are `file:line` in the pinned sources: driver
`dresco/STM32H7xx` at `bfe5d4f6ec27`, grblHAL core (`grbl/`) at
`26d44fe73329`, Trinamic plugin (`motors/`) at `7567c340cf97`, Trinamic
library (`trinamic/`) at `9b304175318d`. `build.py` checks out exactly these
into `output/firmware-build/STM32H7xx/`.

**Nothing in this report has been run on the real board.** Every expected
response is from reading the source.

## 1. What was built

All in `firmware/`:

| File | Deliverable |
| --- | --- |
| `build.py`, `foamcutter.ini` | Build from a clean checkout, pinned to driver, core, all 15 submodules and the toolchain. Checks the commits and the binaries. (From the WIP commit.) |
| `release/*.bin`, `release/SHA256SUMS.txt` | The firmware: one binary for STM32H723, one for STM32H743 (section 5, question 1). Names carry the core and driver commits. |
| `README.md` | Build and flashing instructions, which binary to use, build options. |
| `wiring.md` | One-page wiring sheet: driver slots, DIAG jumpers, power, e-stop. |
| `baseline-settings.txt`, `settings.py` | Baseline `$` settings with comments; `settings.py` strips the comments for sending (grblHAL passes a comment on a `$` line to the setting parser). |
| `bench-test.md` | Bench-test script: commands and the expected response for each, including additions 2a and 2b. |
| `bench/make_excerpts.py`, `bench/excerpt-a-g93-with-g0.nc`, `bench/excerpt-b-short-moves.nc` | The G-code excerpts for 2a and 2b and the script that makes them from the pinned source. |
| `commissioning.md` | Manual commissioning procedure for ioSender, ending with the four G54 zeros (addition 1). |
| `test_build.py`, `test_bench.py`, `test_docs.py` | 32 tests. Run with `python -m pytest firmware`. |

### Bench excerpts (addition 2), as directed

Both excerpts come from `examples/gcode/tapered-spar.nc` at `1769fcc` (the
version the simulator rechecked); each file records the full commit hash.
The file is unchanged from `1769fcc` to the head of `brief-3-cam-fixes`.
Each excerpt starts with `G21 G54 G90 G93` and a G0 to its first position, and
ends with `G94`. Every position is shifted by a whole number of mm per axis so
that each axis's minimum lies between 10 and 11 mm; the envelope is stated at
the top of the file. F words are copied character for character.

The shift leaves every move's timing as it was: in G93 the F word is the
inverse of the move time, and the shift does not change any move's length.
With a whole number of steps per mm, a whole-mm shift is a whole number of
steps, so each move also rounds to the same step counts as the unshifted
source.

| File | Source lines | Envelope (G54, mm) | Notes |
| --- | --- | --- | --- |
| `excerpt-a-g93-with-g0.nc` | 15–30, start = end of 14 | X 10.2–48.0, Y 10.6–12.2, Z 10.3–117.0, A 10.8–11.7 | Source lines 1–30 have no G0 between feed moves, so the 16 G1 moves run twice with a G0 back to the start between them. Line 13 (`G0 X500 Y200 Z500 A200`, a park move) is left out: it would make the envelope large. 78.7 s of feed. |
| `excerpt-b-short-moves.nc` | 60–70, start = end of 59 | X 10.8–21.1, Y 10.1–11.7, Z 10.9–16.7, A 10.9–11.8 | Source line 67 is the shortest move in the file: X 0.0031, Y 0.0004, Z 0.0018, A 0.0003 mm in 1/50908.6 min (1.2 ms). 3.85 s of feed. |

Excerpt A's envelope is not small on Z (107 mm): source line 15 is a lead-in
that moves the right horizontal axis 100 mm. I kept the lines as directed
rather than cut them.

## 2. Findings, task by task

**Task 1. Board, target, crystal, flash offset.**
- Board define `BOARD_BTT_SKR_30`, map `boards/btt_skr_v3.0_map.h`, headed
  "Board map for BIGTREETECH SKR 3 / SKR 3 EZ".
- H723 target: PlatformIO board `generic_stm32h723vg` with linker script
  `STM32H723VGTX_BL_FLASH.ld`.
- Crystal: 25 MHz. The map stops the build for any other `HSE_VALUE`
  (`btt_skr_v3.0_map.h:26-28`).
- Flash offset for the BTT bootloader: `0x08020000` (128 KB), from
  `STM32H723VGTX_BL_FLASH.ld:51` (`ORIGIN = 0x08020000, LENGTH = 1024K - 128K - 128K`).
  The top 128 KB is the settings sector.
- **Conflict:** BTT's SKR 3 EZ wiki page lists only the STM32H743VI.
  Third-party sources (teamgloomy, the Klipper forum) say boards with either
  chip exist. Both variants are built (section 5, question 1).

**Task 2. 4 axes, USB, storage, Trinamic over UART.** `N_AXIS=4`;
`TRINAMIC_ENABLE=2209`, which turns on `TRINAMIC_UART_ENABLE`
(`grbl/driver_opts.h:204-206`). The 4th axis is motor M3, on driver slot E0
(`btt_skr_v3.0_map.h:77-87`). Each driver has its own UART pin
(`:191-203`). USB CDC comes from the upstream `[usb_h723]` section.
Settings go to MCU flash, because no EEPROM is enabled
(`grbl/driver_opts2.h`, `FLASH_ENABLE`).

**Task 3. DIAG to limit inputs.** With UART drivers the plugin does not poll
the drivers for stalls (`TMC_POLL_STALLED` is 1 only for I2C,
`motors/trinamic.h`), so a stall reaches grblHAL only through the limit input.
Limit pins: X PC1, Y PC3, Z PC0, A (E0) PC2 (`btt_skr_v3.0_map.h:68-84`). This
agrees with the independent teamgloomy pin list (xstop PC1, ystop PC3, zstop
PC0, e0stop PC2). BTT: fit the DIAG jumper under each driver used for
sensorless homing; the DIAG pin need not be cut. Which header pins the jumper
bridges is shown only in BTT's diagram image, which I could not read
(`wiring.md`, "Not verified").

**Task 4. Sensorless settings.** These are documented in `baseline-settings.txt`, with
the numbers checked against `grbl/settings.h` and `motors/trinamic.c`:
- `$338`: Trinamic driver enable mask. It exists because `TRINAMIC_MIXED_DRIVERS`
  defaults to 1 (`grbl/driver_opts2.h:276-277`, `motors/trinamic.c:964-966`).
  Its default is all axes.
- `$339`: sensorless homing mask, default 0 (`trinamic.c:1129`).
- `$14x`: run current, mA RMS.
- `$15x`: microsteps.
- `$20x`: SGTHRS for the seek phase, 0–255 (StallGuard4 on the 2209).
- `$21x`: standstill current, %.
- `$22x`: SGTHRS for the locate phase, 0–255.

`$20x` and `$22x` are `Setting_AxisExtended0` and `Setting_AxisExtended2`
(`trinamic.c:51-53`). They are written straight to SGTHRS
(`trinamic/tmc2209hal.c:167`). During homing the driver is switched to
StealthChop (`:159`) and TCOOLTHRS is set to 2/3 of the homing rate (`:165`).
The plugin's defaults are 500 mA, 50 %, 16 microsteps and SGTHRS 22.

**Task 5. Compile-time vs runtime.**
- Fixed at compile time, with the values in this build:
  - `R_SENSE` 110 mΩ. The plugin default matches BTT's figure for its 2209 drivers (a secondary source).
  - `TMC_STEALTHCHOP` 0, so the driver runs in CoolStep/SpreadCycle when not homing.
  - `PWM_THRESHOLD_VELOCITY` 0.
  - `TMC_SFILT_ENABLE` 0.
  - `TRINAMIC_EXTENDED_SETTINGS` 0, so the chopper and CoolStep registers keep the library defaults.
  - `TMC_HOMING_ACCELERATION` undefined.
  - The TCOOLTHRS factor of 1.5.
- **Every stall-detection parameter is already a runtime setting:**
  - The SGTHRS values are `$20x` and `$22x`, also settable with `M914`.
  - The StallGuard filter is set with `M122 H1`/`H0`.
  - The speed where stall detection starts follows the homing rates `$24`/`$25`.
- No rebuild was needed.
- `TRINAMIC_EXTENDED_SETTINGS=1` would expose the chopper and CoolStep registers.
  None of them is a stall-detection setting, so it was left off.

**Task 6. G93 and G94 on 4-axis moves** (source reading, not run):
- **G93.** The planner sets the move speed to F × its length
  (`grbl/planner.c:562-563`), so the move takes 1/F minutes, whatever the
  length. That holds unless an axis would exceed its `$11x`: the speed is
  capped there silently (`planner.c:326-327`) and the move takes longer.
- In G93, every G1 must carry F (`gcode.c:2339-2344`, `error:22`). G0 needs
  no F.
- **G94.** F is applied to the 4-dimensional length
  √(Δx² + Δy² + Δz² + Δa²) (`grbl/nuts_bolts.c:343-361`, used at
  `planner.c:532`). That is not the wire speed at either foam face. The
  rotary-axis special case (`planner.c:497-519`) is compiled out
  (`ROTARY_FIX` 0, `planner.c:34-36`).
- `DEFAULT_AXIS_ROTATIONAL_MASK=0` makes sure A is not marked rotary (`$376`).
- Bench steps 5.1 and 5.2 measure both cases.

**Task 7. U and V as the 3rd and 4th axis.** Only the 4th axis can be
renamed:
- The first three letters are fixed as X, Y, Z (`grbl/nuts_bolts.h:126-128`).
- Axis 4 can be renamed with `-D AXIS3_LETTER='U'` (or V, B, C, W;
  `nuts_bolts.h:57,131-138`), or with `AXIS_REMAP_ABC2UVW`, which the core
  marks "Experimental" (`grbl/config.h:45-52`).
- Getting U and V would need `N_AXIS=5`: X Y Z U V, with axis 3 on the E0 slot and
  axis 4 on E1, and the Z slot empty. The E1 limit input then needs a jumper
  wire from PWRDET (`btt_skr_v3.0_map.h:96-100`). Not built.
- The released build uses X Y Z A. **So the machine profile's letters must be
  `"X" "Y" "Z" "A"`** (section 5, question 6).

**Task 8. E-stop and feed hold.** Both are enabled by default, together with cycle start:
- `Inc/driver.h:120-121` sets `CONTROL_ENABLE` to halt, feed hold and cycle start.
- `ESTOP_ENABLE` is 1 at `COMPATIBILITY_LEVEL` 0, so the halt pin is an e-stop
  (`grbl/driver_opts.h:264-282`).
- The pins are e-stop PA4, feed hold PA5 and cycle start PA6
  (`btt_skr_v3.0_map.h:155-175`).
- An input is active when it reads high, after `$14` (`Src/driver.c:1424-1454`).
- The baseline `$14=6` therefore expects a normally closed e-stop to GND (a
  broken wire stops the machine) and normally open buttons to GND.
- The limit inputs stay usable for normally closed switches with no rebuild:
  `$5=0`, `Src/driver.c:1310-1345`.

**Task 9.** Build and flash: `README.md`. PlatformIO, as preferred.

**Task 10.** Bench test: `bench-test.md`.

**Addition 1. G54 zeros.** `commissioning.md` step 8 is the last step and
follows `machine-profile.md` §3.2–3.4:
- Fit the references.
- Home.
- x zeros for axes 1 and 3: the wire against the x reference face along its
  whole length, clear of the table, then `G10 L20 P1 X0` and `G10 L20 P1 Z0`.
- z zeros for axes 2 and 4: the wire on the table, paper drag at both ends,
  clear of the face, then `G10 L20 P1 Y0` and `G10 L20 P1 A0`.
- Check, record `$#`, then measure the profile's min/max.

It says when to re-check, in the words of §3.4: after every homing cycle, until
the homing repeatability is measured (commissioning 6.4 and first-cut plan cut
4) and known to be small enough; after that, whenever a reference, a wire point
or the wire is changed. `G10 L20` stores the offset in settings storage
(`gcode.c:4253`), so it survives a power cycle.

**Addition 3. A G93 move shorter than one step.**
- **It is dropped.** `plan_buffer_line` rounds each axis's absolute target to
  whole steps (`grbl/planner.c:443`, `lroundf(target × steps_per_mm)`) and
  subtracts the planner's current step position (`:432`, `:444`). If every
  axis rounds to zero steps, the function returns `false` at
  `planner.c:485-486` ("Bail if this is a zero-length block") before anything
  is queued. The buffer head (`:657`) and the planner position (`:654`) are
  not updated.
- **The caller does nothing with `false`.** `mc_line` checks the result only
  for laser mode (`grbl/motion_control.c:182`).
- **No distance is lost.** The parser still moves its own position to the
  target (`grbl/gcode.c:4569`). Because targets are absolute, the next move is
  rounded from the last executed step position, so the dropped fraction of a
  step is carried into it.
- **Its time is lost.** Nothing carries the dropped move's 1/F minutes
  forward. The next move runs in its own 1/F, because its speed is F × its own
  step-rounded length (`planner.c:532`, `:562-563`).
- **A move of at least one step on some axis is kept.** Its length is the
  step-rounded length, so its time is still 1/F. The other axes move
  rounded step counts, so there is up to ½ step of shape error per axis.
- For the bench excerpt at 400 steps/mm, source line 67 rounds to 1 step on X
  and Z and 0 on Y and A, so it is kept, not dropped (checked by
  `test_line_67_steps_at_400_steps_per_mm`). With a coarser setting (fewer
  steps/mm), it could be dropped. The time that would be lost here is 1.2 ms.

## 3. What was verified, and how

- **Build.** `build.py` re-ran from the pinned sources, with PlatformIO's build
  directory deleted first (a full compile). It produced byte-identical
  binaries: the SHA-256 of both files is unchanged from `d5e9a2c`. This ran on
  one machine (macOS); it was not repeated on Windows or Linux.
- **Binaries.** `build.py --verify` passes for both:
  - size fits the application area;
  - the initial stack pointer is in RAM;
  - the reset vector is a Thumb address inside the image at `0x08020000`.

  That shows they are linked for the bootloader, not that they run.
- **Excerpts.** The tests check that:
  - each excerpt is self-contained: `G21 G54 G90 G93`, then a G0 with all
    four axes, then G94 at the end; every G1 has an F word and no G0 has one;
  - every position is inside the stated envelope, and the envelope bounds are
    reached;
  - excerpt A is rapid, feeds, rapid, feeds;
  - excerpt B contains the 0.0031 mm move;
  - the committed files equal a fresh run of the generator on `1769fcc`;
  - every move keeps the source's per-axis deltas and its exact F text;
  - the shifts are whole millimetres;
  - source line 67 is the shortest move in the file.
- **Settings file.** The tests check that it parses, has no duplicates and
  holds the values the documents rely on, and that the stripped output is
  bare `$n=v` lines.
- **Documents.** The tests check that:
  - commissioning ends with the G54 section;
  - all four `G10 L20 P1 <letter>0` commands are there, x zeros first;
  - the re-check rules are there;
  - all brief topics are covered;
  - every cited source file exists in the build checkout;
  - every referenced `bench/` or `release/` file exists.
- **Every source citation** in this report was read in the checkout at the
  pinned commits during this session.
- **Checks.** `python -m pytest firmware`: 32 passed. `ruff check firmware`
  and `mypy --strict` on the firmware Python files: clean. `python check.py`
  (all areas): passed, with 138 passed and 3 expected failures, all in the
  simulator tests from `main`.

## 4. What was NOT verified

- **Anything on hardware.** None of the brief's acceptance tests has been run:
  `$I`, `M122`, the axis jogs and the timed 4-axis G93 move all need the
  bench test on the real board.
- **Which MCU the board has.** BTT's own manual and its board photo show
  the STM32H743VI.
- **The flashing procedure.** It now follows BTT's official SKR 3 EZ manual
  (section 6), but it has not been carried out.
- **The Creality 42-40's rated current.** Retail listings give 1.0 A (most,
  including Creality-branded ones) or 1.5 A; I found no Creality datasheet.
  The baseline uses 700 mA RMS, 70 % of the lower figure. The 200 steps/rev
  (1.8°) figure is from the same listings, all of which agree.
- **EZ2209 sense resistor.** 110 mΩ (the plugin default) comes from a search
  summary of BTT's wiki. BTT's own EZ2209 page does not state it. A wrong
  value scales every current setting.
- **The DIAG jumper positions and the EXP2 pin numbers.** The board map
  comments conflict with the usual EXP2 layout. Bench step 3A now
  identifies the EXP2 pins on the board. BTT's manual (§3.3.2) mentions no
  UART-mode jumper for EZ drivers.
- **The motor-voltage jumper positions.** They are shown only in the
  manual's photographs. `wiring.md` gives a power-off continuity check.
- **The TMC2209 datasheet relation** "stall when SG_RESULT ≤ 2 × SGTHRS",
  which the tuning method relies on. Not re-read this session.
- **Whether ioSender handles G93 programs** (time estimate, preview), and
  whether it streams fast enough for runs of 1 ms moves without starving the
  planner. Excerpt 2b has one such move among 0.4 s moves, so it does not
  test sustained short moves.
- **The baseline START values** (rates, accelerations, homing speeds,
  thresholds). They are first guesses, marked as such.

## 5. Open questions for the project director

1. **H723 or H743?** The brief says H723; BTT's wiki shows H743. Please read
   the chip marking. Both binaries are committed; once you know, I can
   remove the other one if you want only one released file.
2. **Motor rated current.** Please read it off a motor label (or send a
   photo). `$14x` should be set from it, not from listings.
3. **Short moves in the CAM output.** Dropped sub-step moves lose their time
   (here about 1 ms), and kept 1-step moves carry up to ½ step of shape
   error. Should Brief 3 merge moves shorter than one step? That needs
   steps/mm, but the profile records `lead` only, not microsteps. Adding
   microsteps to the profile is a format change, so it is your call.
4. **Homing directions.** The baseline homes verticals up first, then
   horizontals toward the back, to keep the wire clear of the table and the
   x reference face. Is the back the right end for the horizontals on your
   frame?
5. **Hard limits off.** With sensorless homing, `$21=0` because DIAG also
   flags driver errors. That means no stop if a carriage hits a mechanical
   stop during a cut; only soft limits protect it. Acceptable?
6. **Axis letters.** CLAUDE.md makes the letters a profile setting, but this
   firmware accepts only X Y Z A (or axis 4 as U after a rebuild). Should the
   CAM and the simulator reject a profile whose letters the firmware cannot
   accept, or should that stay a commissioning check?
7. **E-stop power cut.** The controller e-stop stops motion only. Do you want
   the wiring sheet to require a second contact that opens the hot-wire
   supply?
8. **`tapered-spar.nc` on `main` is older than `1769fcc`.** The line numbers
   in your additions match only the `brief-3-cam-fixes` version. The excerpts
   are pinned to `1769fcc`, so nothing here changes when that branch is
   merged. The excerpt tests read `1769fcc` from git and skip if it is
   missing, so `brief-3-cam-fixes` must reach `main` before or with this
   branch for those tests to run.

## 6. Changes after the director's review

The director approved the brief for bench testing, subject to six changes.
All six are made, and tests for them are in `firmware/test_docs.py`.

1. **Motor supply and jumpers** (`wiring.md`, with a warning at the top).
   Sources: BTT's official SKR 3 EZ user manual and V1.0 schematic from
   github.com/bigtreetech/SKR-3 at `b5d842e9`, and BTT's EZ2209 page.
   - **Maximum motor supply for the EZ2209: 28 V.** BTT's EZ2209 page gives
     "Power voltage（VM） 4.75V---28V DC". The board's 48 V (manual §1.2,
     "Motor Voltage: DC12V-DC48V") is for the separate Motor Power terminal
     with other drivers; the manual names the TMC5160 and EZ5160 (§1.1
     item 20). The sheet says plainly that 48 V must not be used with the
     EZ2209.
   - **Jumpers.** Each driver slot has a 3-pin "Motor Power selection"
     header. The schematic gives its pins as `<slot>-Motor_Power`, `VBB` and
     `Motor_Power`. The manual (§3.2) shows two positions: §3.2.1
     "Motherboard Power Voltage" and §3.2.2 "Motor Supply Voltage".
   - **Setting for one 24 V supply on Power In:** all four used slots in the
     §3.2.1 position, with Motor Power left unconnected.
   - **What is confirmed:** the positions are shown only in photographs, and
     the text names no pins. I could not confirm from the text that `VBB` is
     the Power In rail. The sheet therefore carries a warning and a power-off
     continuity check: the joined pin must beep to Power In +, and the free
     pin to Motor Power +.
   - **Also from the manual, now in the bench preconditions:** the USB/CAN
     switch must be popped up (USB, §3.4), and the VUSB jumper stays off
     (§3.1).
2. **First power-up without EXP2.** Confirmed from the source:
   - The control inputs have their pull-ups on with `$17=0`
     (`grbl/ioports.c:1584`), so an unconnected PA4 reads high.
   - `systemGetState` XORs every pin with `$14` (`Src/driver.c:1426-1454`).
   - The following `aux_ctrl_scan_status` does nothing in this build
     (`grbl/pin_bits_masks.h:410-434`; its options are off).
   - So `$14=70` (e-stop 64 + feed hold 2 + cycle start 4) makes all three
     unconnected inputs read inactive.

   Bench steps 1, 2, 4, 5 and 6 now run with EXP2 unconnected and `$14=70`
   (new step 1.4; 1.7 checks the board is `Idle`). Step 3 runs last:
   - 3A finds GND by continuity, then confirms PA5, PA6 and PA4 against
     BTT's pin diagram by touching each candidate pin with a 1 kΩ lead to GND
     and watching `Pn:`.
   - 3B wires the switches.
   - 3C restores `$14=6` and runs the original input tests.
   - Step 7.2 confirms `$14=6` at the end.

   The step numbers were kept so they match the review.
3. **Step 4.6** now requires at least 110 mm of clear travel in the jog
   direction before the 100 mm jogs.
4. **Commissioning checks.** Step 8.8, "Final profile checks", holds both:
   (a) the profile letters are `"X" "Y" "Z" "A"`; (b) each `max_rate` is at
   or below `$11n`, because grblHAL silently slows a G93 move that exceeds
   `$11n` (`planner.c:326-327`). Check (b) is also step 4.1. Step 8.8 sits
   inside step 8, so the procedure still ends with the G54 section.
5. **E-stop.** The second normally closed contact that opens the hot-wire
   supply is now a requirement in `wiring.md`. Commissioning has a new step 0
   that tests it with a voltmeter on the hot-wire supply output before any
   other step.
6. **Driver source: staying on `dresco/STM32H7xx`, no rebuild.**
   - `grblHAL/STM32H7xx` does not exist (GitHub API: 404).
   - The grblHAL organisation's repositories include STM32F1xx, F3xx, F4xx
     and F7xx drivers but no H7 driver.
   - grblHAL's own controller list (grblhal.org/docs/getting-started/controllers/)
     links the BTT SKR 3 row, and every other H7 board row, to
     `https://github.com/dresco/STM32H7xx`. Its WebBuilder entry uses
     `driver=STM32H7xx&board=BTT SKR 3`.
   - `dresco/STM32H7xx` is not a fork of anything. It is the STM32H7 driver
     grblHAL itself points to, not a fork of an official one.
   - The pinned commit `bfe5d4f6ec27` is also its current HEAD (checked with
     `git ls-remote`).

**One finding from the manual.** The board has an on-board 24C32 EEPROM
(manual §1.2). This build does not enable it, so settings and G54 offsets
are stored in MCU flash, as `README.md` says. That works, but each `G10 L20`
writes the flash. Enabling the EEPROM would need a rebuild with the EEPROM
plugin, and its I2C pins on this board have not been checked. I have left it
as it is unless you want it changed.

## 7. Director's decisions (after review)

- Question 3: do not merge sub-step moves in the CAM. 1 ms and half a step
  are negligible.
- Question 5: hard limits off is accepted.
- Question 6: the axis letters stay a commissioning check (step 8.8 (a)).
- Question 7: the e-stop must open the hot-wire supply (change 5).
- Question 8: `brief-3-cam-fixes` reaches `main` before this branch.
- Still open: question 1 (H723 or H743; the manual shows H743), question 2
  (motor label current) and question 4 (homing directions). These are
  expected from the bench results.
