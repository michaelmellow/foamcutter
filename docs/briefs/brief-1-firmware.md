## Brief 1: Firmware build

**Goal.** Produce a reproducible grblHAL build for the SKR 3 EZ (STM32H723) that drives 4 independent axes through EZ2209 drivers and homes all 4 without switches.

**Background to verify, not assume.** The [grblHAL controller list](https://github.com/grblhal/controllers) names the Bigtreetech SKR 3 under the STM32H7xx driver. The [Trinamic motor plugin](https://github.com/grblHAL/Plugins_motor/blob/master/README.md) supports the TMC2209 and provides sensorless homing. A developer in [grblHAL core issue 152](https://github.com/grblHAL/core/issues/152) reported working SKR 3 builds with TMC2209 support.

**Tasks**

1. Read the STM32H7xx driver source. Identify the board define and pin map for the SKR 3 EZ, and confirm the H723 build target, crystal frequency and flash offset for the BTT bootloader.
2. Configure 4 axes, USB serial, settings storage, and the Trinamic plugin for TMC2209 over UART on driver slots X, Y, Z and E0.
3. Confirm from the pin map and BTT schematic how each driver's DIAG output reaches a limit input, and which jumpers must be fitted.
4. Enable sensorless homing. Document the settings involved: `$338` (driver enable), `$339` (sensorless homing enable), `$14x` (current), `$15x` (microsteps), `$20x` and `$22x` (stall sensitivity, 0 to 255 for the TMC2209).
5. List which Trinamic parameters are hard-coded at compile time and expose the stall-detection ones as runtime settings.
6. Confirm that inverse-time feed (G93) works on 4-axis moves. Report how G94 feed is interpreted on 4-axis moves.
7. Report whether the third and fourth axes can be named U and V, and how.
8. Define an input for an emergency stop and a feed hold.
9. Write build instructions (PlatformIO preferred) and flashing instructions for this board.
10. Write a bench-test script: a list of commands and the expected response for each.

**Deliverables**

- Configuration files and build instructions that produce the binary from a clean checkout, pinned to specific grblHAL commits.
- The firmware binary.
- A one-page wiring sheet: driver slots, DIAG jumpers, power, e-stop.
- A baseline settings file (`$` values) with comments.
- The bench-test script.
- A manual commissioning procedure for ioSender: steps/mm, direction, motor current, stall sensitivity, homing, travel and soft limits, speed and acceleration.

**Acceptance**

- `$I` reports the board and the Trinamic plugin.
- `M122` reports four TMC2209 drivers with live values.
- Each axis jogs alone, in the right direction, by the commanded distance.
- A 4-axis G93 move completes in the commanded time.

**Out of scope.** Carrying out the commissioning on the machine, any CAM work.

**Known difficulty.** The plugin's own documentation says sensorless tuning is not trivial and that switches are likely the easier option for one-off machines. Keep the limit inputs usable for mechanical switches without a rebuild.
