"""Command-line tool: python -m foamdxf ROOT.dxf TIP.dxf --span MM --stock LENGTH,HEIGHT --profile PROFILE

Reads the root and tip outlines, writes a job file (docs/formats/job-file.md)
and, unless --job-only is given, runs the CAM on it to write G-code (.nc),
report (.txt) and preview (.svg), exactly as `python -m foamcam cut` would.

Instead of two DXFs, a STEP model with flat root and tip faces:

  python -m foamdxf --step WING.step --list-faces
  python -m foamdxf --step WING.step [--faces ROOT,TIP] --stock LENGTH,HEIGHT --profile PROFILE

The span is then measured between the faces; --faces defaults to the pair
`step.StepModel.suggest` picks (the parallel flat faces farthest apart).

`python -m foamdxf --gui` opens the same thing in a window.
"""

import argparse
import sys
from pathlib import Path

from foamcam.errors import FoamcamError

from .errors import DxfError
from .jobgen import CutSettings, Placement
from .pipeline import Request, make_job, run_cam, write_files
from .step import read_step


def pair(text: str) -> tuple[float, float]:
    try:
        a, b = (float(v) for v in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected two comma-separated numbers, got {text}") from None
    return a, b


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m foamdxf",
        description="Turn root and tip DXF outlines (or two faces of a STEP model) into a foamcutter job, then G-code.",
    )
    p.add_argument("root", type=Path, nargs="?", help="DXF of the root outline")
    p.add_argument("tip", type=Path, nargs="?", help="DXF of the tip outline")
    p.add_argument("--span", type=float, help="distance between the root and tip faces, mm (DXFs only)")
    p.add_argument("--step", type=Path, help="STEP model to use instead of the two DXFs")
    p.add_argument("--faces", type=pair, metavar="ROOT,TIP",
                   help="STEP face numbers of the root and tip (default: suggested; see --list-faces)")
    p.add_argument("--list-faces", action="store_true", help="list the STEP model's flat faces and stop")
    p.add_argument("--stock", type=pair, metavar="LENGTH,HEIGHT",
                   help="foam stock size: length along the chord and height, mm")
    p.add_argument("--profile", type=Path, help="machine profile YAML")
    p.add_argument("--out", type=Path, default=Path("output"), help="folder for the files (default: output)")
    p.add_argument("--name", help="job name (default: from the file names)")
    p.add_argument("--speed", type=float, default=150.0, help="cut speed at the faster face, mm/min (default 150)")
    p.add_argument("--kerf-table", help="kerf table name (default: the profile's first)")
    p.add_argument("--direction", choices=("upper_first", "lower_first"), default="upper_first")
    p.add_argument("--lead-in", type=float, default=20.0, help="clearance in front of the stock, mm (default 20)")
    p.add_argument("--block-x", type=float, help="G54 x of the stock's back face (default: centred in travel)")
    p.add_argument("--safe-clearance", type=float, default=20.0,
                   help="safe position height above the stock, mm (default 20)")
    p.add_argument("--min-te", type=float, default=0.6,
                   help="minimum trailing-edge thickness, mm (default 0.6; 0 = as drawn, sharp edges may fail)")
    p.add_argument("--segment", type=float, default=1.0, help="max segment length, mm (default 1)")
    p.add_argument("--simplify", type=float, default=0.01,
                   help="drop DXF points within this distance of the outline, mm (default 0.01; 0 keeps all)")
    p.add_argument("--job-only", action="store_true", help="write the job file only, do not run the CAM")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--gui"]:
        from .gui import main as gui_main

        return gui_main(argv[1:])
    p = parser()
    args = p.parse_args(argv)
    faces: tuple[int, int] | None = None
    root: Path = args.step
    tip: Path = args.step
    try:
        if args.step is not None:
            if args.root is not None:
                p.error("give either --step or the two DXFs, not both")
            model = read_step(args.step)
            if args.list_faces:
                print(f"{args.step.name}: flat faces, largest first")
                for face in model.faces:
                    print(f"  {face.label()}")
                print(f"suggested root,tip: {model.suggest()}")
                return 0
            if args.faces is not None:
                faces = (int(args.faces[0]), int(args.faces[1]))
            else:
                faces = model.suggest()
                if faces is None:
                    raise DxfError(f"{args.step.name}: no two parallel flat faces; pick them with --faces")
                print(f"using faces {faces[0]} (root) and {faces[1]} (tip)")
        else:
            if args.root is None or args.tip is None or args.span is None:
                p.error("give ROOT and TIP DXFs and --span, or --step")
            root, tip = args.root, args.tip
    except DxfError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.stock is None or args.profile is None:
        p.error("--stock and --profile are required")
    request = Request(
        root=root,
        tip=tip,
        profile=args.profile,
        placement=Placement(args.span or 0.0, args.stock[0], args.stock[1], args.block_x, args.lead_in,
                            args.safe_clearance),
        settings=CutSettings(args.speed, args.kerf_table, args.direction, args.segment, args.min_te),
        name=args.name,
        simplify=args.simplify,
        faces=faces,
    )
    try:
        plan = make_job(request)
        for warning in plan.warnings:
            print(f"warning: {warning}")
        for line in plan.generated.summary:
            print(f"  {line}")
        if args.job_only:
            for path in write_files(plan, None, args.out):
                print(f"wrote {path}")
            return 0
        try:
            result = run_cam(plan)
        except FoamcamError:
            write_files(plan, None, args.out)  # keep the job for inspection
            raise
        for path in write_files(plan, result, args.out):
            print(f"wrote {path}")
        print(f"total cut time {result.total_time:.2f} min")
        nc = args.out / f"{request.stem}.nc"
        job = args.out / f"{request.stem}.yaml"
        print(f"check it: python -m foamsim {nc} --job {job} --profile {args.profile}")
        return 0
    except (DxfError, FoamcamError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
