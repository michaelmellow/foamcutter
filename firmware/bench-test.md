# Bench test

A list of commands to send from ioSender (console / MDI) and the response to
expect for each. Run it after flashing (`README.md`) and before commissioning
(`commissioning.md`). **Hot wire supply off, no foam on the table**
throughout.

Record for each step: pass or fail, and the actual response if it differs.
Expected responses are from the grblHAL source at the pinned commits; none of
them has been seen on the real board yet.

**Order.** Steps 1, 2, 4, 5 and 6 run with **nothing connected to EXP2**,
using a temporary `$14=70` (step 1.4). Step 3 (inputs) runs **last**: it
first identifies the EXP2 pins with a meter, then wires the switches and
restores `$14=6`. The step numbers are kept as before so they match the
review.

## 0. Before power-on

- Wiring as in `wiring.md`, with the warning at its top done first:
  - all four motor-voltage jumpers checked by continuity;
  - one 24 V supply on Power In, nothing on Motor Power;
  - USB/CAN switch in the popped-up (USB) position;
  - VUSB jumper off.
- Four EZ2209s in X, Y, Z, E0, and DIAG jumpers fitted under those four.
- Motors connected.
- **Nothing connected to EXP2. Nothing on the STOP connectors.**
- The TMC2209 needs motor supply voltage to answer on UART, so `M122` fails
  on USB power alone.
- Each carriage away from both of its mechanical stops.

## 1. Identity

| # | Send | Expect | Pass if |
| --- | --- | --- | --- |
| 1.1 | `$I` | Several `[...]` lines then `ok`, including `[FIRMWARE:grblHAL]`, `[BOARD:BTT SKR-3]` and `[PLUGIN:Trinamic v0.33]`. The `[NEWOPT:...]` line contains `TMC=15`. | All four strings present. `TMC=15` means the plugin found drivers on all four axes (bits X, Y, Z, A). |
| 1.2 | `$RST=*` | `ok` | |
| 1.3 | each line of `output/baseline-settings.nc` (made by `python firmware/settings.py`) | `ok` for every line | No `error:` response. |
| 1.4 | `$14=70` (**temporary**) | `ok` | |
| 1.5 | `$$` | The settings list. | `$338=15`, `$339=15`, `$376=0`, `$14=70`, `$22=3`, `$100`…`$103` as sent. |
| 1.6 | Ctrl-X (soft reset; ioSender's Reset button) | Welcome line `GrblHAL 1.1f ['$' or '$HELP' for help]` | Board restarts with the settings kept. |
| 1.7 | `?` | `<Idle|...>` | State is `Idle`, not `Alarm`. The `Pn:` field, if present, has no `E`, `H` or `S`. |

**Why `$14=70` works with EXP2 unconnected** (from the source):
- With `$17=0` (the default) the control inputs have their pull-ups on
  (`grbl/ioports.c:1584`), so an unconnected PA4, PA5 or PA6 reads high.
- The driver reads each pin and XORs the result with `$14`
  (`Src/driver.c:1426-1454`). The aux-control scan after it changes nothing
  in this build (`grbl/pin_bits_masks.h:410-434`; the options it serves are
  off).
- `$14=70` sets feed hold (2), cycle start (4) and e-stop (64), so all three
  unconnected inputs read as inactive.

**While `$14=70` is set, there is no e-stop.** Keep a hand on the motor supply
switch, and do not leave it set after step 3.

## 2. Drivers (`M122`)

| # | Send | Expect | Pass if |
| --- | --- | --- | --- |
| 2.1 | `M122` | A `[TRINAMIC]` block: one column per axis (X Y Z A) with rows `Driver`, `Set current`, `RMS current`, `Run current`, `Hold current`, `CS actual`, `msteps`, `tstep`, …, then `ok`. | Four columns. `Driver` names TMC2209 for each. `Set current` 700 and `msteps` 16 for each. No `Could not communicate with stepper driver!` warning. |
| 2.2 | `$140=500` then `M122` | `ok`, then the report | `Set current` for X changes to 500, and the other three stay at 700: the values are live, read from the drivers. |
| 2.3 | `$140=700` | `ok` | |

## 4. Each axis alone

Jog one axis at a time. Mark the carriage position (tape and pencil, or a
dial indicator) and measure the distance moved.

| # | Send | Expect | Pass if |
| --- | --- | --- | --- |
| 4.1 | `$J=G91 G21 X10 F300` | `ok`; only the left horizontal carriage moves | Moves toward the front, 10 mm ± 0.2 mm. |
| 4.2 | `$J=G91 G21 X-10 F300` | `ok` | Returns to the mark. |
| 4.3 | `$J=G91 G21 Y10 F300` / `Y-10` | only the left vertical carriage moves | Up 10 mm, then back. |
| 4.4 | `$J=G91 G21 Z10 F300` / `Z-10` | only the right horizontal carriage moves | Toward the front 10 mm, then back. |
| 4.5 | `$J=G91 G21 A10 F300` / `A-10` | only the right vertical carriage moves | Up 10 mm, then back. |
| 4.6 | Repeat 4.1–4.5 with 100 mm. **First check that each carriage has at least 110 mm of clear travel in the jog direction** (toward the front for X and Z, up for Y and A) before its mechanical stop. If not, jog it back first. | | Distance within ± 0.5 mm. |

A wrong direction is fixed in commissioning step 3 (`$3`), a wrong distance
in step 1 (`$10x`). Record both here; do not tune during the bench test.

## 5. Feed modes on 4-axis moves

Set a temporary G54 first, with every carriage at least 130 mm from the stop
in its positive direction and 10 mm from the other stop:
`G10 L20 P1 X0 Y0 Z0 A0`. Commissioning sets the real zeros later.

| # | Send | Expect | Pass if |
| --- | --- | --- | --- |
| 5.1 | `G21 G90 G54 G93 G1 X20 Y20 Z20 A20 F2` | `ok`; all four axes move together and start and stop together | Takes 30 s ± 0.5 s (F2 in G93 = 1/2 min). |
| 5.2 | `G94 G1 X10 Y10 Z10 A10 F600` | all four move | Takes 2 s ± 0.3 s: in G94 the feed applies to the 4-axis length √(4 × 10²) = 20 mm (grbl/nuts_bolts.c:343-361, planner.c:532). If it takes 1 s, A is being treated as rotary (`$376`). |
| 5.3 | `G93 G1 X0 Y0 Z0 A0` | `error:22` (feed rate undefined) | In G93 each G1 needs its own F word (grbl/gcode.c:2339-2344). |
| 5.4 | `G93 G0 X0 Y0 Z0 A0` | `ok`; all axes return to 0 at rapid rate | G0 needs no F word in G93. |
| 5.5 | `G94` | `ok` | |

## 6. Program excerpts

The excerpts in `bench/` are taken from `examples/gcode/tapered-spar.nc` at
commit `1769fcc` (the version the simulator rechecked). Each one runs on its
own: it starts with `G21 G54 G90 G93` and a G0 to its first position, and ends
with `G94`. Its positions are shifted by whole millimetres so it fits in a
small envelope, which is stated at the top of the file. **Check the envelope
against the travel from the temporary G54 zero** (step 5) before running it.
Load each file in ioSender and run it with Cycle Start. Keep `?` status
reports running (ioSender polls them).

### 6a. G93 feed moves with G0 rapids between them

File `bench/excerpt-a-g93-with-g0.nc`: source lines 15–30 (16 G1 moves after
the G0 approach of source line 14), run twice with a G0 back to the start in
between. Envelope: X 10.2–48.0, Y 10.6–12.2, Z 10.3–117.0, A 10.8–11.7 mm.

| Watch | Pass if |
| --- | --- |
| Responses | Every line answers `ok`. No `error:` (in particular no `error:22`, which would mean an F word was not accepted) and no `ALARM:`. |
| F words | Values such as `F1.69515`, `F241.962`, `F1186.36`: up to 6 significant digits, no exponent. Accepted if no `error:` appears. |
| G0 between G93 moves | The G0 in the middle runs at rapid rate and the second pass starts without an error. |
| Time | Total about 79 s of feed moves (78.73 s commanded) plus two short rapids. The first move alone takes 35.4 s (F1.69515). |
| End position | `G54` position after the run equals the last line: X10.2052 Y12.1619 Z10.3133 A11.6898. |
| End state | `G94` is active afterwards: `$G` shows `G94`. |

### 6b. Very short moves

File `bench/excerpt-b-short-moves.nc`: source lines 60–70. The move from
source line 67 is the shortest in the file: 0.0031 mm on X, 0.0004 mm on Y,
0.0018 mm on Z, 0.0003 mm on A, commanded in 1/50908.6 min (1.2 ms). Envelope:
X 10.8–21.1, Y 10.1–11.7, Z 10.9–16.7, A 10.9–11.8 mm.

| Watch | Pass if |
| --- | --- |
| Responses | Every line answers `ok`. No `error:` and no `ALARM:`. |
| Stall | No carriage stops or jumps; no step loss audible; end position correct (below). |
| Pause | State stays `Run` from the first G1 to the last; it does not drop to `Idle` or `Hold` between moves. The carriages do not visibly stop at the short move. Total about 3.9 s (3.85 s commanded). |
| End position | X10.7905 Y11.6562 Z10.8719 A11.7549 in G54. |

What the controller does with the short move: grblHAL rounds every
absolute target to whole steps (`planner.c:443`). At 400 steps/mm (lead 8 mm,
16 microsteps) the move from source line 67 is 1 step on X, 0 on Y, 1 on Z,
0 on A, so it is kept as a 1-step move. A move that rounds to 0 steps on every
axis is dropped (`planner.c:485-486`); its distance is carried into the next
move and its 1.2 ms is lost. See the Brief 1 report.

## 3. Inputs (run last, after step 6)

**3A. Find the EXP2 pins.** The board map's comments give PA4 = EXP2 pin 7,
PA5 = pin 9 and PA6 = pin 10. These conflict with the usual EXP2 layout, so
identify the pins against BTT's pin diagram (`BIGTREETECH SKR 3 EZ
V1.0-PIN.pdf`, folder `Hardware (SKR 3 EZ)` of github.com/bigtreetech/SKR-3)
and confirm each one on the board. The EXP2 connector also carries GND and
the SD-card SPI lines.

| # | Do | Expect |
| --- | --- | --- |
| 3A.1 | Power **off**. Meter on continuity: find the EXP2 pins that beep to the **−** screw of Power In. | The GND pins. Mark them. |
| 3A.2 | From the pin diagram, mark the candidate pins for PA4, PA5 and PA6. | |
| 3A.3 | Power on, `$14=70` still set. Make a test lead: a **1 kΩ resistor** in series with a wire from an EXP2 GND pin. The resistor limits the current if a pin turns out to be a supply or an output. Touch the lead to the PA5 candidate and send `?`. | `Pn:` contains `H` (feed hold). |
| 3A.4 | Same on the PA6 candidate. | `Pn:` contains `S` (cycle start). |
| 3A.5 | Same on the PA4 candidate. | `ALARM:10` (e-stop). Remove the lead, soft reset, back to `Idle`. |
| 3A.6 | Write down the three pin numbers found. If any differs from `wiring.md`, report it. | |

**3B. Wire the switches** with the power off: a normally closed e-stop
contact from the PA4 pin to GND, and normally open buttons from PA5 (feed
hold) and PA6 (cycle start) to GND.

**3C. Restore the e-stop and test.**

| # | Send / do | Expect |
| --- | --- | --- |
| 3.0 | `$14=6`, then soft reset | `ok`; the welcome line. `$$` shows `$14=6`. |
| 3.1 | Nothing pressed, e-stop closed, `?` | `<Idle|...>` with no `Pn:` field, or `Pn:` without E, H, S. |
| 3.2 | Press and hold feed hold, `?` | `Pn:` contains `H`. |
| 3.3 | Press and hold cycle start, `?` | `Pn:` contains `S`. |
| 3.4 | Open the e-stop | `ALARM:10` (EStop asserted). State is not `Idle`. |
| 3.5 | Close the e-stop, then soft reset, then `$X` if needed | Back to `Idle`. |
| 3.6 | Disconnect the e-stop wire at the board | Same as 3.4: a broken wire stops the machine. Reconnect. |

## 7. Clean up

The temporary G54 from step 5 stays until commissioning step 8 replaces it.
Do not cut foam with it.

| # | Send | Expect |
| --- | --- | --- |
| 7.1 | `$#` | `[G54:...]` with the temporary offset, among the other offsets, then `ok`. Write it down. |
| 7.2 | `$14` | `$14=6`. If it still shows 70, the e-stop is not active: set `$14=6` before anything else. |

Bench test passes when every step above passes. Report failures with the
exact response text.
