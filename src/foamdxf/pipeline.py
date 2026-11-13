"""DXF files -> job -> CAM result: the steps shared by the CLI and the GUI."""

from dataclasses import dataclass
from pathlib import Path

from foamcam.cam import CamResult, generate
from foamcam.job import Job, load_job_text
from foamcam.profile import Profile, load_profile
from foamcam.sections import build_outline

from .errors import DxfError
from .jobgen import CutSettings, GeneratedJob, Placement, build_job
from .outline import Point, read_outline


@dataclass(frozen=True)
class Request:
    root: Path
    tip: Path
    profile: Path
    placement: Placement
    settings: CutSettings
    name: str | None = None
    simplify: float = 0.01

    @property
    def stem(self) -> str:
        return f"{self.root.stem}-{self.tip.stem}"


@dataclass(frozen=True)
class Plan:
    request: Request
    profile: Profile
    generated: GeneratedJob
    job: Job
    warnings: tuple[str, ...]


def make_job(request: Request) -> Plan:
    """Read the profile and both DXFs and build the job. Raises DxfError or FoamcamError."""
    profile = load_profile(request.profile)
    root = read_outline(request.root, simplify_tol=request.simplify)
    tip = read_outline(request.tip, simplify_tol=request.simplify)
    generated = build_job(root, tip, profile, request.placement, request.settings, request.name)
    job = load_job_text(generated.text, request.root.parent)
    return Plan(request, profile, generated, job, root.warnings + tip.warnings)


def run_cam(plan: Plan) -> CamResult:
    """Run foamcam on the job. Raises FoamcamError when the job cannot be cut."""
    return generate(plan.job, plan.profile)


def write_files(plan: Plan, result: CamResult | None, out_dir: Path) -> list[Path]:
    """Write the job and, if given, the G-code, report and preview. Returns the paths."""
    files = [(".yaml", plan.generated.text)]
    if result is not None:
        files += [(".nc", result.gcode), (".txt", result.report), (".svg", result.preview_svg)]
    written = []
    for suffix, text in files:
        path = out_dir / f"{plan.request.stem}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


@dataclass(frozen=True)
class PreviewGeometry:
    """Everything the GUI draws, in the section view (x, z), mm."""

    stock: tuple[float, float, float, float]  # x0, z0, x1, z1
    travel: tuple[float, float, float, float]  # x0, z0, x1, z1 shared by both towers
    root_target: list[Point]
    tip_target: list[Point]
    root_cut: list[Point]  # wire at the root face, compensated, from the entry point round to it
    tip_cut: list[Point]
    tower_12: list[Point]  # axes 1 and 2
    tower_34: list[Point]  # axes 3 and 4
    wire_span: float  # y of the axes 3-4 tower plane (axes 1-2 are at y = 0)
    root_y: float  # y of the root foam face
    tip_y: float
    times: list[float]  # minutes from the entry point to each path point
    letters: tuple[str, str, str, str]  # G-code letters of axes 1 to 4
    phases: list[str]  # per path point: "entry", then the move kind: "lead_in", "cut" or "exit"
    safe_position: tuple[float, float, float, float]  # axes 1 to 4


def preview_geometry(plan: Plan, result: CamResult) -> PreviewGeometry:
    job, profile = plan.job, plan.profile
    targets = []
    for section in (job.root, job.tip):
        problems: list[str] = []
        outline = build_outline(section, job.min_te_thickness, problems)
        if outline is None:
            raise DxfError("; ".join(problems))
        targets.append([outline.place(p) for p in outline.contour])

    # the wire path from the entry point (end of the last rapid before the cut) to the exit
    first = next(i for i, m in enumerate(result.moves) if m.kind != "rapid")
    feed_moves = [m for m in result.moves if m.kind != "rapid"]
    feeds = [result.moves[first - 1].axes] + [m.axes for m in feed_moves]
    times = [0.0]
    for m in feed_moves:
        times.append(times[-1] + m.duration)

    def at_plane(y: float) -> list[Point]:
        s = y / profile.wire_span
        return [(a1 + s * (a3 - a1), a2 + s * (a4 - a2)) for a1, a2, a3, a4 in feeds]

    a = profile.axes
    b = job.block
    return PreviewGeometry(
        stock=(b.x, profile.table_z, b.x + b.length, profile.table_z + b.height),
        travel=(max(a[0].min, a[2].min), max(a[1].min, a[3].min), min(a[0].max, a[2].max), min(a[1].max, a[3].max)),
        root_target=targets[0],
        tip_target=targets[1],
        root_cut=at_plane(job.root.y),
        tip_cut=at_plane(job.tip.y),
        tower_12=[(m[0], m[1]) for m in feeds],
        tower_34=[(m[2], m[3]) for m in feeds],
        wire_span=profile.wire_span,
        root_y=job.root.y,
        tip_y=job.tip.y,
        times=times,
        letters=profile.letters,
        phases=["entry"] + [m.kind for m in feed_moves],
        safe_position=job.safe_position,
    )


@dataclass(frozen=True)
class AlignEdge:
    """A long stock edge (parallel to the wire) and how to put the wire on it."""

    name: str
    x: float  # axes 1 and 3
    z: float  # axes 2 and 4
    gcode: tuple[str, str, str]  # lift, across, down: no diagonal rapid


def alignment_edges(g: PreviewGeometry) -> list[AlignEdge]:
    """The four stock edges that run along the wire. With both towers at the same
    (x, z), the wire is parallel to y and lies exactly on that edge, so the stock
    can be pushed against the wire to place and square it before cutting."""
    x0, z0, x1, z1 = g.stock
    l1, l2, l3, l4 = g.letters
    safe = g.safe_position
    edges = []
    for name, x, z in (
        ("low-x top edge", x0, z1),
        ("high-x top edge", x1, z1),
        ("low-x bottom edge (on the table)", x0, z0),
        ("high-x bottom edge (on the table)", x1, z0),
    ):
        gcode = (
            f"G90 G0 {l2}{safe[1]:.3f} {l4}{safe[3]:.3f}",
            f"G0 {l1}{x:.3f} {l3}{x:.3f}",
            f"G0 {l2}{z:.3f} {l4}{z:.3f}",
        )
        edges.append(AlignEdge(name, x, z, gcode))
    return edges
