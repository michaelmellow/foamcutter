"""Command line: python -m foamsim PROGRAM --job JOB --profile PROFILE.

Exit code 0: pass. 1: fail. 2: the inputs could not be used.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import InputError
from .job import load_job
from .profile import load_profile
from .sim import DEFAULT_TOLERANCE, simulate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="foamsim",
        description="Check a hot-wire G-code program against its job before cutting.",
    )
    parser.add_argument("program", type=Path, help="G-code file")
    parser.add_argument("--job", type=Path, required=True, help="job file (.yaml)")
    parser.add_argument("--profile", type=Path, required=True, help="machine profile (.yaml)")
    parser.add_argument("--plane", type=float, action="append", default=[],
                        help="extra plane y (mm) to trace; may be repeated")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help="largest allowed deviation per section, mm (default 0.05)")
    parser.add_argument("--out", type=Path, default=Path("output/sim"),
                        help="folder for the report and plots (default output/sim)")
    parser.add_argument("--no-plots", action="store_true", help="write the report only")
    parser.add_argument("--frames", type=int, default=150, help="animation frames (default 150)")
    args = parser.parse_args(argv)

    try:
        profile = load_profile(args.profile)
        job = load_job(args.job)
        gcode = args.program.read_text(encoding="utf-8")
        result = simulate(gcode, profile, job, planes=tuple(args.plane),
                          tolerance=args.tolerance)
    except (InputError, OSError, UnicodeDecodeError) as exc:
        print(f"foamsim: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.program.stem
    report = args.out / f"{stem}-report.json"
    report.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(result.summary())
    print(f"report: {report}")
    if not args.no_plots and result.program is not None:
        from .plots import write_animation, write_overlay

        overlay = args.out / f"{stem}-overlay.png"
        write_overlay(result, overlay)
        animation = args.out / f"{stem}-cut.gif"
        write_animation(result, animation, max_frames=args.frames)
        print(f"overlay: {overlay}\nanimation: {animation}")
    return 0 if result.passed else 1
