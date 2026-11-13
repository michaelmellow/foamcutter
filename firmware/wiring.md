# Wiring sheet: BTT SKR 3 EZ, 4 axes, sensorless homing

> **WARNING: check before the first power-up.**
> - **Motor supply: 24 V maximum for this build.** The EZ2209 is rated
>   4.75–28 V DC. The board's 48 V rating is for other drivers.
> - **Motor-voltage jumpers.** Each driver slot has a jumper that picks where
>   the driver's motor supply comes from. For one 24 V supply on Power In,
>   all four used slots must select motherboard power. BTT's manual shows the
>   two jumper positions only in photographs, with no pin labels in the text.
>   Before fitting drivers, confirm each jumper with the power-off continuity
>   check under "Power" below.

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

Sources:
- BTT SKR 3 EZ user manual, official copy in github.com/bigtreetech/SKR-3,
  commit `b5d842e9`: §1.2 parameters, §1.1 features 19–20, §3.1, §3.2, §3.4.
- The SKR 3 EZ V1.0 schematic from the same repository.
- BTT's EZ2209 wiki page.

**Maximum motor supply: 28 V, set by the EZ2209.** BTT's EZ2209 page gives
"Power voltage (VM) 4.75V---28V DC". The board accepts up to 48 V on its
separate Motor Power terminal (manual §1.2: "Motor Voltage: DC12V-DC48V").
That 48 V is for other drivers: the manual names the TMC5160 and EZ5160
("The motor power supply supports up to 48V, and for the larger voltage when
using TMC5160 and EZ5160…", §1.1 item 20). **It must not be used with the
EZ2209.** This build uses one 24 V supply, which is inside both the board's
input range (DC 12–24 V) and the EZ2209's range.

| Supply | Where | Notes |
| --- | --- | --- |
| Board and motors | **Power In** (DCIN) terminal | One 24 V supply. |
| — | **Motor Power** terminal | **Leave unconnected.** |
| PC | USB port | Serial link to ioSender (USB CDC). |
| Hot wire | its own external supply | Not connected to the board in any way. The controller does not switch it. |

**Motor-voltage selection jumpers** (manual §3.2; "Each motor drive module
can select the corresponding motor voltage through the jumper cap", §1.1
item 19). Each driver slot has a 3-pin header next to its large capacitor.
In the schematic it is labelled, for example, "X-Motor Power selection",
with pins `X-Motor_Power` (the driver's supply), `VBB` and `Motor_Power`
(the separate terminal). The manual shows two positions:
- §3.2.1 "Motherboard Power Voltage for Motor Voltage Selection" (top
  picture on page 13): the driver takes its supply from Power In.
- §3.2.2 "Motor Supply Voltage for Motor Voltage Selection" (bottom
  picture): the driver takes its supply from the Motor Power terminal.

For a single 24 V supply on Power In, set **all four used slots (X, Y, Z,
E0) to the §3.2.1 position**. This setting is inferred from the manual's
headings and photographs; the text names no pins. Confirm it, power off,
with a continuity meter:
1. On each header, the pin the jumper joins to the driver pin must beep to
   the **+** screw of **Power In**. That is VBB; the path may run through the
   board fuse.
2. The pin left free must beep to the **+** screw of **Motor Power**.

If either check fails, stop and do not power up.

Other board settings from the manual:
- **USB/CAN switch** (§3.4): it must be in the popped-up (USB) position, or
  the PC will not see the board.
- **VUSB jumper** (§3.1): leave it off. It is only for running the board
  from USB power; the board takes its power from the 24 V supply.

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
For the bench test, EXP2 stays unconnected and a temporary `$14=70` makes
the open e-stop input read inactive (`bench-test.md`). The last bench step
restores `$14=6`.

**Required: the e-stop must also open the hot-wire supply.** The
controller's e-stop stops motion only; it does not cut any power, and the
controller never switches the wire heat. The e-stop button must therefore
have a second normally closed contact that opens the hot-wire supply
directly, or through a contactor whose coil it switches, so that pressing
e-stop removes wire heat. Do not run the machine with the wire heated until
this is wired and tested (`commissioning.md`, step 0). Opening the motor
supply as well is optional.

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
- Whether a UART mode jumper is needed. BTT's manual (§3.3.2) mentions none
  for EZ drivers ("The EZ series drivers support the simultaneous use of both
  UART and SPI drivers"). `M122` in the bench test shows whether UART works.
- The motor-voltage jumper positions: see the warning at the top and the
  continuity check under "Power".
