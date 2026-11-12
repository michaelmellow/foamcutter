"""Run lint, type check and tests. Exits non-zero if any of them fails.

Usage:
    python check.py          check everything
    python check.py AREA     check one area only: cam, step or sim
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

AREAS = {
    "cam": ("foamcam", "tests/cam"),
    "step": ("foamstep", "tests/step"),
    "sim": ("foamsim", "tests/sim"),
}


def steps(area: str | None) -> list[tuple[str, list[str], set[int]]]:
    py = sys.executable
    if area is None:
        ruff_paths = ["."]
        mypy_args: list[str] = []  # files listed in pyproject.toml
        pytest_paths: list[str] = []  # testpaths in pyproject.toml
    else:
        package, tests = AREAS[area]
        ruff_paths = [f"src/{package}", tests]
        mypy_args = [f"src/{package}", tests]
        pytest_paths = [tests]
    return [
        ("ruff", [py, "-m", "ruff", "check", *ruff_paths], {0}),
        ("mypy", [py, "-m", "mypy", *mypy_args], {0}),
        ("pytest", [py, "-m", "pytest", *pytest_paths], {0}),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("area", nargs="?", choices=sorted(AREAS))
    args = parser.parse_args()

    failed: list[str] = []
    for name, cmd, ok_codes in steps(args.area):
        print(f"=== {name} ===", flush=True)
        code = subprocess.call(cmd, cwd=ROOT)
        if code not in ok_codes:
            failed.append(f"{name} (exit {code})")
    print("=== summary ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
