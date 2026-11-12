"""Build the foam cutter grblHAL firmware from a clean checkout.

Usage:
    python firmware/build.py            fetch pinned sources, build, verify, copy to firmware/release/
    python firmware/build.py --verify   only verify the binaries already in firmware/release/

Needs git and PlatformIO Core (pio) on the PATH, or PlatformIO installed in its
default location (~/.platformio/penv). Works on Windows, Linux and macOS.

The source checkout goes to output/firmware-build/ (not committed).
"""

import argparse
import hashlib
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware"
RELEASE = FIRMWARE / "release"
WORK = ROOT / "output" / "firmware-build"
CHECKOUT = WORK / "STM32H7xx"

DRIVER_URL = "https://github.com/dresco/STM32H7xx.git"
DRIVER_COMMIT = "bfe5d4f6ec271cc9929336244c3b1c8e55d1d040"

# Submodule commits recorded by DRIVER_COMMIT. Checked after checkout so a
# changed or tampered submodule cannot slip into the build unnoticed.
SUBMODULES = {
    "bluetooth": "a68b47471ea5d67bcd36eef43209a08bda4f4993",
    "eeprom": "99fabd2297b4b78850478f28e0ca19d8536ed651",
    "fans": "c5c3613c8d311b43427ec32d551a1e62a5a68531",
    "grbl": "26d44fe733292b0c1f453237f3c9c2bdbac2ac67",  # grblHAL core
    "keypad": "826e749b59b65d23dc60bb07506244a2067f52cd",
    "laser": "3110715da1eb34b5448c5be3de00b4d65b7e22f5",
    "motors": "7567c340cf97c7d2e1c212d1b1f94b023fd51cbe",  # Trinamic plugin
    "networking": "4a861cea57f67da399d85876b39d0b8b84d4086c",
    "openpnp": "76dbbc1420dd9773b1008a21f9d97b266f564bc9",
    "plasma": "a5e94494542f881f57a2833c75426461d7480a8d",
    "plugins": "3af21190621b2af892526b29cb084d2d9966f162",
    "sdcard": "60997d15ed81ecb129e1b6d79e169c5cb1169c09",
    "spindle": "9311b34894ed7ad6ca8ccbdaa6d2d2e961d10505",
    "trinamic": "9b304175318d93818c8967faadc89dd101b17e92",  # Trinamic library
    "webui": "7474c46de587cfde140d7a8507dda3fdc2ded479",
}

# BTT bootloader: 128 KB, application starts at 0x08020000.
APP_BASE = 0x08020000

# (PlatformIO environment, MCU tag, flash size in bytes)
TARGETS = [
    ("foamcutter_skr3ez_h743_bl128", "h743", 2048 * 1024),
    ("foamcutter_skr3ez_h723_bl128", "h723", 1024 * 1024),
]


def release_name(mcu: str) -> str:
    core = SUBMODULES["grbl"][:12]
    driver = DRIVER_COMMIT[:12]
    return f"grblhal-foamcutter-skr3ez-{mcu}-bl128-core-{core}-drv-{driver}.bin"


def run(cmd: list[str], cwd: Path | None = None) -> str:
    print("+ " + " ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout


def find_pio() -> str:
    found = shutil.which("pio") or shutil.which("platformio")
    if found:
        return found
    penv = Path.home() / ".platformio" / "penv"
    for candidate in (penv / "bin" / "pio", penv / "Scripts" / "pio.exe"):
        if candidate.exists():
            return str(candidate)
    sys.exit("PlatformIO Core not found. Install it: https://docs.platformio.org/en/latest/core/installation/")


def checkout() -> None:
    if not (CHECKOUT / ".git").exists():
        WORK.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", DRIVER_URL, str(CHECKOUT)])
    else:
        run(["git", "fetch", "origin"], cwd=CHECKOUT)
    run(["git", "checkout", "--force", DRIVER_COMMIT], cwd=CHECKOUT)
    run(["git", "clean", "-fdx", "--exclude=.pio"], cwd=CHECKOUT)
    run(["git", "submodule", "sync", "--recursive"], cwd=CHECKOUT)
    run(["git", "submodule", "update", "--init", "--recursive", "--force"], cwd=CHECKOUT)

    head = run(["git", "rev-parse", "HEAD"], cwd=CHECKOUT).strip()
    if head != DRIVER_COMMIT:
        sys.exit(f"Driver checkout is at {head}, expected {DRIVER_COMMIT}")
    for name, expected in SUBMODULES.items():
        actual = run(["git", "rev-parse", "HEAD"], cwd=CHECKOUT / name).strip()
        if actual != expected:
            sys.exit(f"Submodule {name} is at {actual}, expected {expected}")
    print(f"Sources verified: driver {DRIVER_COMMIT[:12]}, {len(SUBMODULES)} submodules.")


def verify_binary(data: bytes, flash_size: int) -> list[str]:
    """Return a list of problems with a firmware image linked for APP_BASE."""
    problems = []
    app_size = flash_size - 128 * 1024 - 128 * 1024  # bootloader + settings sector
    if len(data) < 8:
        return ["image is shorter than a vector table"]
    if len(data) > app_size:
        problems.append(f"image is {len(data)} bytes, application area is {app_size}")
    stack, reset = struct.unpack_from("<II", data, 0)
    # Initial stack pointer must be in RAM (DTCM 0x20000000 or AXI SRAM 0x24000000).
    if not (0x20000000 <= stack <= 0x24100000):
        problems.append(f"initial stack pointer {stack:#010x} is not in RAM")
    # Reset handler must be a Thumb address inside this image at APP_BASE.
    if not (reset & 1):
        problems.append(f"reset vector {reset:#010x} is not a Thumb address")
    if not (APP_BASE <= (reset & ~1) < APP_BASE + len(data)):
        problems.append(
            f"reset vector {reset:#010x} is outside the image at {APP_BASE:#010x} "
            "(wrong linker script for the BTT bootloader?)"
        )
    return problems


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_release() -> bool:
    ok = True
    sums = {}
    sums_file = RELEASE / "SHA256SUMS.txt"
    if sums_file.exists():
        for line in sums_file.read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            sums[name.strip()] = digest
    for _, mcu, flash_size in TARGETS:
        path = RELEASE / release_name(mcu)
        if not path.exists():
            print(f"MISSING  {path.name}")
            ok = False
            continue
        problems = verify_binary(path.read_bytes(), flash_size)
        if sums.get(path.name) != sha256(path):
            problems.append("SHA-256 does not match SHA256SUMS.txt")
        for problem in problems:
            print(f"FAIL     {path.name}: {problem}")
        if not problems:
            print(f"OK       {path.name}")
        ok = ok and not problems
    return ok


def build() -> None:
    pio = find_pio()
    checkout()
    shutil.copyfile(FIRMWARE / "foamcutter.ini", CHECKOUT / "foamcutter.ini")
    RELEASE.mkdir(exist_ok=True)
    lines = []
    for env, mcu, _ in TARGETS:
        run([pio, "run", "-c", "foamcutter.ini", "-e", env], cwd=CHECKOUT)
        built = CHECKOUT / ".pio" / "build" / env / "firmware.bin"
        target = RELEASE / release_name(mcu)
        shutil.copyfile(built, target)
        lines.append(f"{sha256(target)}  {target.name}")
    (RELEASE / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true", help="only verify firmware/release/")
    args = parser.parse_args()
    if not args.verify:
        build()
    return 0 if verify_release() else 1


if __name__ == "__main__":
    sys.exit(main())
