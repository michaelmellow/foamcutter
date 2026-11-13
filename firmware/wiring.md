# Wiring sheet: BTT SKR 3 EZ, 4 axes, sensorless homing

Pin names are from the grblHAL board map `boards/btt_skr_v3.0_map.h` (driver
commit `bfe5d4f6ec27`) and agree with the independent SKR 3 EZ H723 pin list
at teamgloomy.github.io. Connector **pin numbers** (EXP2 pin 7 and so on) are
from comments in the board map only. Check each one with a continuity meter
before connecting anything (see "Not verified" at the end).

## Driver slots

| Axis | Letter | Tower, direction | Driver slot | STEP / DIR / EN / UART | Limit input (stop connector) |
| --- | --- | --- | --- | --- | --- |
| 1 | X | left, horizontal | X | PD4 / PD3 / PD6 / PD5 | PC1 (X-STOP) |
| 2 | Y | left, vertical | Y | PA15 / PA8 / PD1 / PD0 | PC3 (Y-STOP) |
| 3 | Z | right, horizontal | Z | PE2 / PE3 / PE0 / PE1 | PC0 (Z-STOP) |
| 4 | A | right, vertical | E0 | PD15 / PD14 / PC7 / PC6 | PC2 (E0-STOP) |
| — | — | not used | E1 | leave empty | — |

- Fit an EZ2209 in slots X, Y, Z and E0. All four talk UART over their own
  pin (separate UART lines, so no address jumpers are involved).
- Each motor goes to the motor connector of its slot. The Z slot has two
  motor connectors on this board (dual Z); use one.
- Motor: Creality 42-40. Its coil pairing and rated current were not
  verified (see the Brief 1 report). Check the coil pairs with a meter
  (about the same few ohms across each pair, open between pairs) before
  plugging in.

## DIAG jumpers (sensorless homing)

grblHAL reads a stall only through the limit input; with UART drivers the
plugin does not poll the driver for stalls (`TMC_POLL_STALLED` is 0 unless
the drivers use I2C; `motors/trinamic.h`). So each driver's DIAG output must
reach its axis's limit input.

- **Fit the DIAG jumper under each of the four used slots** (X, Y, Z, E0).
  BTT: "plug the jumper cap when using the Sensorless Homing function, and
  leave it unplugged when not in use. There is no need to cut the DIAG pin of
  the driver." The jumper's position is shown only in BTT's pin diagram image.
- With a jumper fitted, the driver's DIAG drives the signal pin of that
  slot's STOP connector. **Do not also connect a switch** to that STOP
  connector.
- Do not fit a DIAG jumper under E1.

**Keeping switches possible without a rebuild.** To change an axis to a
mechanical switch: remove its DIAG jumper, wire a normally closed switch
between the signal pin and GND of its STOP connector, and clear its bit in
`$339`. No firmware change is needed: `$5 = 0` already treats "input high"
as triggered, which is a normally closed switch opening.

## Power

| Supply | Where | Notes |
| --- | --- | --- |
| Board and motors | the board's main power input | BTT: input DC 12–24 V, motor supply up to 48 V. A 24 V supply is the usual choice. |
| PC | USB port | Serial link to ioSender (USB CDC). |
| Hot wire | its own external supply | Not connected to the board in any way. The controller does not switch it. |

Never plug or unplug a motor or a driver with the motor supply on.

## E-stop, feed hold, cycle start

All three are enabled in the build by default (`Inc/driver.h` `CONTROL_ENABLE`;
`ESTOP_ENABLE` is 1 at `COMPATIBILITY_LEVEL` 0, `grbl/driver_opts.h`). They are
3.3 V MCU inputs with internal pull-ups. **Switch them to GND only; never
apply 5 V or 24 V.**

| Function | MCU pin | Connector pin (board map comment) | Switch | `$14` bit |
| --- | --- | --- | --- | --- |
| E-stop | PA4 | EXP2 pin 7 | **normally closed** to GND | 64, not inverted |
| Feed hold | PA5 | EXP2 pin 9 | normally open push button to GND | 2, inverted |
| Cycle start | PA6 | EXP2 pin 10 | normally open push button to GND | 4, inverted |

Baseline `$14 = 6` matches this table. An open e-stop circuit (button
pressed, wire broken or nothing connected) holds the controller in e-stop.
**For the first power-up on the bench, connect the e-stop or a wire link
from PA4 to GND**, or the board will sit in e-stop.

The controller's e-stop stops motion. It does not cut power. A machine e-stop
should also open the hot-wire supply and the motor supply, for example with a
second normally closed contact in the e-stop button driving a contactor.

PA5, PA6 and PA7 are also the SPI lines of the EXP2 connector, so a BTT
display cannot be fitted at the same time.

## Not verified

- EXP2 pin numbers above. Standard EXP2 layouts used by other boards put GND
  on pin 9, which contradicts the board map comment "Feed hold - EXP2 - pin 9".
  Find PA4, PA5, PA6 and a GND pin with a meter, using BTT's SKR 3 EZ pin
  diagram, before wiring.
- Which header pins the DIAG jumpers bridge, and whether the STOP inputs have
  any series resistor or filter that changes DIAG timing. Check that with
  the jumper fitted there is continuity between the driver's DIAG pin and the
  STOP connector's signal pin.
- Whether the SKR 3 EZ needs a jumper to select UART mode for the EZ2209
  slots. Teamgloomy notes "The SKR3EZ H723 can't be used with SPI and UART
  controlled drivers at the same time", which suggests a board-wide setting.
  Check BTT's manual. `M122` in the bench test shows whether UART works.
