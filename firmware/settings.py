"""Check the baseline settings file and write it without comments, ready to send.

Usage:
    python firmware/settings.py     write output/baseline-settings.nc

grblHAL keeps a comment that follows a $ setting on the same line and passes it
to the setting parser (protocol.c, the '$' and ';' cases), so only bare
"$n=value" lines may be sent. firmware/baseline-settings.txt keeps its
comments on lines of their own, starting with ';'.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASELINE = HERE / "baseline-settings.txt"
OUTPUT = ROOT / "output" / "baseline-settings.nc"

SETTING = re.compile(r"\$(\d+)=(-?\d+(?:\.\d+)?)")


def parse(text: str) -> list[tuple[int, str]]:
    """Return (setting number, value) pairs. Raises ValueError on a bad line."""
    settings = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        match = SETTING.fullmatch(line)
        if not match:
            raise ValueError(f"line {number}: expected '$n=value' alone on the line: {raw!r}")
        settings.append((int(match.group(1)), match.group(2)))
    return settings


def strip(text: str) -> str:
    return "".join(f"${n}={value}\n" for n, value in parse(text))


def steps_per_mm(full_steps: int, microsteps: int, lead: float) -> float:
    """Steps per mm for a leadscrew axis (machine-profile.md §5, field `lead`)."""
    return full_steps * microsteps / lead


def main() -> int:
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(strip(BASELINE.read_text()))
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
