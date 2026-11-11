"""Run lint, type check and tests. Exits non-zero if any of them fails.

Usage: python check.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# pytest exit code 5 means "no tests collected"; accepted so the empty
# skeleton passes. Any other non-zero code is a failure.
PYTEST_NO_TESTS = 5

STEPS: list[tuple[str, list[str], set[int]]] = [
    ("ruff", [sys.executable, "-m", "ruff", "check", "."], {0}),
    ("mypy", [sys.executable, "-m", "mypy"], {0}),
    ("pytest", [sys.executable, "-m", "pytest"], {0, PYTEST_NO_TESTS}),
]


def main() -> int:
    failed: list[str] = []
    for name, cmd, ok_codes in STEPS:
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
