"""Command-line tool: python -m foamcam <command> ...

  cut JOB --profile PROFILE [--out DIR]   write G-code (.nc), report (.txt) and preview (.svg)
  check JOB --profile PROFILE             validate and print the report; write nothing
  profile PROFILE                         validate a machine profile
  kerf-test --profile PROFILE ...         write a kerf test-cut program
"""

import argparse
import sys
from pathlib import Path

from .cam import generate
from .errors import FoamcamError
from .job import load_job
from .kerftest import KerfTest, kerf_test_gcode
from .profile import load_profile


def numbers(text: str, count: int | None = None) -> tuple[float, ...]:
    try:
        values = tuple(float(v) for v in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a comma-separated list of numbers: {text}") from None
    if count is not None and len(values) != count:
        raise argparse.ArgumentTypeError(f"expected {count} numbers, got {len(values)}")
    return values


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m foamcam", description="CAM for a 4-axis hot-wire foam cutter.")
    sub = p.add_subparsers(dest="command", required=True)

    cut = sub.add_parser("cut", help="write G-code, report and preview for a job")
    cut.add_argument("job", type=Path)
    cut.add_argument("--profile", type=Path, required=True)
    cut.add_argument("--out", type=Path, default=Path("output"), help="folder for the files (default: output)")

    check = sub.add_parser("check", help="validate a job and print its report")
    check.add_argument("job", type=Path)
    check.add_argument("--profile", type=Path, required=True)

    prof = sub.add_parser("profile", help="validate a machine profile")
    prof.add_argument("profile", type=Path)

    kerf = sub.add_parser("kerf-test", help="write a kerf test-cut program")
    kerf.add_argument("--profile", type=Path, required=True)
    kerf.add_argument("--block", type=lambda t: numbers(t, 3), required=True, metavar="X,LENGTH,HEIGHT",
                      help="block back-face x, length along x, height (mm)")
    kerf.add_argument("--speeds", type=numbers, required=True, metavar="S1,S2,...", help="test speeds, mm/min")
    kerf.add_argument("--depth", type=float, default=40.0, help="slot depth, mm (default 40)")
    kerf.add_argument("--spacing", type=float, default=8.0, help="distance between slots, mm (default 8)")
    kerf.add_argument("--margin", type=float, default=8.0, help="margin at bottom and top, mm (default 8)")
    kerf.add_argument("--lead-in", type=float, default=10.0, help="clearance in front of the block, mm (default 10)")
    kerf.add_argument("--safe", type=lambda t: numbers(t, 4), required=True, metavar="A1,A2,A3,A4",
                      help="safe position, axes 1 to 4")
    kerf.add_argument("--out", type=Path, default=Path("output/kerf-test.nc"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "profile":
            profile = load_profile(args.profile)
            print(f'{args.profile}: valid. "{profile.name}", axes {" ".join(profile.letters)}, '
                  f"wire span {profile.wire_span:g} mm, kerf tables: {', '.join(profile.kerf_tables)}")
            return 0
        if args.command == "kerf-test":
            profile = load_profile(args.profile)
            x, length, height = args.block
            s = args.safe
            test = KerfTest(x, length, height, args.depth, args.speeds, args.spacing, args.margin,
                            args.lead_in, (s[0], s[1], s[2], s[3]))
            write(args.out, kerf_test_gcode(test, profile))
            return 0
        profile = load_profile(args.profile)
        result = generate(load_job(args.job), profile)
        if args.command == "check":
            print(result.report)
            return 0
        stem = args.job.stem
        write(args.out / f"{stem}.nc", result.gcode)
        write(args.out / f"{stem}.txt", result.report)
        write(args.out / f"{stem}.svg", result.preview_svg)
        print(f"total cut time {result.total_time:.2f} min")
        return 0
    except FoamcamError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {path}")
