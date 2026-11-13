# Foam cutter firmware (grblHAL for the BTT SKR 3 EZ)

| File | What it is |
| --- | --- |
| `release/*.bin` | The firmware binaries, one per MCU variant. Names carry the grblHAL core and driver commits. |
| `release/SHA256SUMS.txt` | Checksums of the binaries. |
| `build.py` | Rebuilds the binaries from a clean checkout and checks them. |
| `foamcutter.ini` | PlatformIO environments for this build (copied into the driver checkout by `build.py`). |
| `wiring.md` | One-page wiring sheet: driver slots, DIAG jumpers, power, e-stop. |
| `baseline-settings.txt` | Baseline `$` settings with comments. Send it with `settings.py`. |
| `bench-test.md` | Bench-test script: commands and expected responses. |
| `bench/` | G-code excerpts used by the bench test, and the script that makes them. |
| `commissioning.md` | Manual commissioning procedure for ioSender. |

Tests: `python -m pytest firmware`. The firmware has no `check.py` area.

## Which binary

The project brief says STM32H723. BTT's SKR 3 EZ wiki page lists only the
STM32H743VI, and there are SKR 3 EZ boards with either chip. Both are built.
**Read the marking on the large square chip** and flash the matching file:

| Chip marking | File |
| --- | --- |
| `STM32H723VG…` | `grblhal-foamcutter-skr3ez-h723-bl128-core-26d44fe73329-drv-bfe5d4f6ec27.bin` |
| `STM32H743VI…` | `grblhal-foamcutter-skr3ez-h743-bl128-core-26d44fe73329-drv-bfe5d4f6ec27.bin` |

Both are linked to start at `0x08020000`, after BTT's 128 KB bootloader, and
use the 25 MHz crystal of this board. The wrong variant will not run.

## Pinned sources

| Source | Repository | Commit |
| --- | --- | --- |
| STM32H7xx driver | https://github.com/dresco/STM32H7xx | `bfe5d4f6ec271cc9929336244c3b1c8e55d1d040` |
| grblHAL core (`grbl/`) | submodule of the driver | `26d44fe733292b0c1f453237f3c9c2bdbac2ac67` |
| Trinamic plugin (`motors/`) | submodule | `7567c340cf97c7d2e1c212d1b1f94b023fd51cbe` |
| Trinamic library (`trinamic/`) | submodule | `9b304175318d93818c8967faadc89dd101b17e92` |

All 15 submodule commits are listed in `build.py` and checked after checkout.
The toolchain is pinned in `foamcutter.ini`: `ststm32@20.0.0`,
`framework-stm32cubeh7 @ 1.12.1`, `toolchain-gccarmnoneeabi @ 1.70201.0`.

## Build options

Set in `foamcutter.ini` on top of the upstream `[common]` and `[usb_h7xx]`
sections:

| Option | Why |
| --- | --- |
| `BOARD_BTT_SKR_30` | Board map `boards/btt_skr_v3.0_map.h`, "BTT SKR 3 / SKR 3 EZ". |
| `HSE_VALUE=25000000` | 25 MHz crystal. The board map stops the build for any other value. |
| `N_AXIS=4` | X, Y, Z on driver slots X, Y, Z; the 4th axis (A) on slot E0 (motor M3 in the map). |
| `TRINAMIC_ENABLE=2209` | TMC2209 over UART (the core turns on `TRINAMIC_UART_ENABLE`). |
| `DEFAULT_AXIS_ROTATIONAL_MASK=0` | All 4 axes linear. Without it the A axis defaults to rotary (`$376`). |
| `USB_SERIAL_CDC=1` | From `[usb_h7xx]`: the host connects over the board's USB port. |

Settings are stored in the MCU flash (the driver uses flash when no EEPROM is
enabled; `grbl/driver_opts2.h`, `FLASH_ENABLE`).

## Build from a clean checkout

Needs Python 3.11+, git and PlatformIO Core (`pip install platformio`, or the
PlatformIO installer). Works on Windows, Linux and macOS.

```text
python firmware/build.py
```

This clones the driver into `output/firmware-build/`, checks out the pinned
commits, checks every submodule commit, builds both variants, copies them to
`firmware/release/` and rewrites `SHA256SUMS.txt`. It then checks each binary:
size fits the application area, stack pointer in RAM, reset vector inside the
image at `0x08020000`.

To only check the committed binaries: `python firmware/build.py --verify`.

## Flash

The SKR 3 EZ has BTT's SD-card bootloader. This method is from BTT and
third-party guides; it was not carried out in this session.

1. Format a microSD card as FAT32. Small cards (a few GB or less) are reported
   to work best.
2. Copy the matching binary to the card's root and rename it `firmware.bin`.
3. With the board powered off, insert the card. Power on and wait about 30 s.
4. Remove the card and look at it on a PC. If the file is now named
   `FIRMWARE.CUR`, the bootloader took it. That only shows the file was
   copied, not that the firmware runs: carry on with the bench test.
5. Connect ioSender to the board's USB serial port (a virtual COM port; no
   baud rate setting matters for USB CDC). Send `$I`. Expected:
   `[BOARD:BTT SKR-3]` and `[PLUGIN:Trinamic v0.33]` among the lines.
6. Send `$RST=*` once, then apply the baseline settings (`settings.py`).

If `$I` shows nothing, the wrong MCU variant is the first suspect.
