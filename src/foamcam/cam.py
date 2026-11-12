"""From a job and a machine profile to G-code (job-file.md §10 to §13)."""

import math
from dataclasses import dataclass, field

from . import __version__
from . import geometry as g
from .errors import CamError, ValidationError
from .geometry import Point
from .job import Job
from .profile import Profile
from .sections import Outline, build_outline, check_in_block

Axes = tuple[float, float, float, float]

# A path node is a reference into the geometry: ("outer", i) or ("feature", name, i).
Ref = tuple[str, int] | tuple[str, str, int]


@dataclass
class Move:
    kind: str  # "rapid", "lead_in", "cut", "exit"
    axes: Axes
    duration: float = 0.0  # minutes, feed moves only
    root_length: float = 0.0  # mm moved at the root foam face
    tip_length: float = 0.0


@dataclass
class KerfStats:
    values: list[float] = field(default_factory=list)


@dataclass
class CamResult:
    gcode: str
    report: str
    preview_svg: str
    total_time: float  # minutes, sum of feed move durations
    moves: list[Move]


def generate(job: Job, profile: Profile) -> CamResult:
    root, tip = validate(job, profile)
    return Cam(job, profile, root, tip).run()


def validate(job: Job, profile: Profile) -> tuple[Outline, Outline]:
    """Rules of job-file.md §3 to §9 that need geometry or the profile."""
    problems: list[str] = []
    if job.kerf_table not in profile.kerf_tables:
        problems.append(f'kerf_table: "{job.kerf_table}" is not a kerf table in the machine profile')
    for section in (job.root, job.tip):
        if not 0 < section.y < profile.wire_span:
            problems.append(
                f"{section.label}.position.y: must lie between the carriage planes (0 and {profile.wire_span:g})"
            )
    root = build_outline(job.root, job.min_te_thickness, problems)
    tip = build_outline(job.tip, job.min_te_thickness, problems)
    for outline in (root, tip):
        if outline is not None:
            check_in_block(outline, job.block, problems)
    if (
        root is not None
        and tip is not None
        and fixed_points(root, job.direction)[1] != fixed_points(tip, job.direction)[1]
    ):
        problems.append("features: the slit points occur in a different order at the root and the tip")
    if problems or root is None or tip is None:
        raise ValidationError("job file", problems)
    return root, tip


def traversal(outline: Outline, direction: str) -> list[int]:
    """§10.1: contour indices in cutting order, ending back at the start."""
    n = len(outline.contour)
    order = list(range(n)) if direction == "upper_first" else list(range(n - 1, -1, -1))
    return order + [order[0]]


def fixed_points(outline: Outline, direction: str) -> tuple[list[int], list[str]]:
    """§10.2: positions in the traversal of the fixed points, with their labels."""
    loop = traversal(outline, direction)
    n = len(outline.contour)
    marks = [(0, 0, "start"), (loop.index(outline.le_index), 1, "leading edge"), (n - 1, 2, "far trailing edge")]
    for name, index in outline.slit_index.items():
        marks.append((loop.index(index), 3, f'slit "{name}"'))
    marks.append((n, 4, "start"))
    marks.sort()
    return [m[0] for m in marks], [m[2] for m in marks]


class Cam:
    def __init__(self, job: Job, profile: Profile, root: Outline, tip: Outline) -> None:
        self.job = job
        self.profile = profile
        self.root = root
        self.tip = tip
        self.kerf = profile.kerf_tables[job.kerf_table]
        self.kerf_used: dict[str, list[float]] = {"root": [], "tip": []}
        self.problems: list[str] = []

    # --- §10.3 synchronisation and §10.4 order of moves -------------------

    def synchronise(self) -> tuple[list[Point], list[Point], list[str | None]]:
        """Paired outer-contour nodes (closed: last = first) and the slit tag of each node."""
        loop_r = traversal(self.root, self.job.direction)
        loop_t = traversal(self.tip, self.job.direction)
        pos_r, labels = fixed_points(self.root, self.job.direction)
        pos_t, _ = fixed_points(self.tip, self.job.direction)
        cr, ct = self.root.contour, self.tip.contour
        nodes_r, nodes_t = [cr[loop_r[0]]], [ct[loop_t[0]]]
        tags: list[str | None] = [slit_name(labels[0])]
        for k in range(1, len(pos_r)):
            piece_r = [cr[i] for i in loop_r[pos_r[k - 1] : pos_r[k] + 1]]
            piece_t = [ct[i] for i in loop_t[pos_t[k - 1] : pos_t[k] + 1]]
            longest = max(g.length(piece_r), g.length(piece_t))
            steps = max(1, math.ceil(longest / self.job.max_segment_length - 1e-9))
            nodes_r += g.resample(piece_r, steps)
            nodes_t += g.resample(piece_t, steps)
            tags += [None] * (steps - 1) + [slit_name(labels[k])]
        return nodes_r, nodes_t, tags

    def path(self, n_outer: int, tags: list[str | None]) -> list[Ref]:
        refs: list[Ref] = []
        for i in range(n_outer + 1):
            refs.append(("outer", i % n_outer))
            name = tags[i] if i < n_outer else None
            if name is None:
                continue
            feature = self.root.features[name]
            k = len(feature.points)
            if feature.kind == "hole":
                order = list(range(k)) + [0]
            else:
                order = list(range(k)) + list(range(k - 2, -1, -1))
            refs += [("feature", name, v) for v in order]
            refs.append(("outer", i % n_outer))
        return refs

    # --- §10.7 kerf compensation ------------------------------------------

    def compensate(
        self, outer_r: list[Point], outer_t: list[Point]
    ) -> tuple[dict[Ref, Point], dict[Ref, Point]]:
        comp_r: dict[Ref, Point] = {}
        comp_t: dict[Ref, Point] = {}
        n = len(outer_r)
        self.offset("outer", None, outer_r, outer_t, [("outer", i) for i in range(n)], True, comp_r, comp_t)
        for name, f_r in self.root.features.items():
            f_t = self.tip.features[name]
            refs: list[Ref] = [("feature", name, i) for i in range(len(f_r.points))]
            if f_r.kind == "hole":
                self.offset(name, name, list(f_r.points), list(f_t.points), refs, False, comp_r, comp_t)
            else:
                for ref, p, q in zip(refs, f_r.points, f_t.points):
                    comp_r[ref] = self.root.place(p)
                    comp_t[ref] = self.tip.place(q)
        return comp_r, comp_t

    def offset(
        self,
        what: str,
        feature: str | None,
        poly_r: list[Point],
        poly_t: list[Point],
        refs: list[Ref],
        outward: bool,
        comp_r: dict[Ref, Point],
        comp_t: dict[Ref, Point],
    ) -> None:
        n = len(poly_r)
        half_r: list[float] = []
        half_t: list[float] = []
        for i in range(n):
            # Speeds of this edge's move before compensation (§10.7 step 1).
            dr = g.dist(poly_r[i], poly_r[(i + 1) % n])
            dt = g.dist(poly_t[i], poly_t[(i + 1) % n])
            fastest = max(dr, dt)
            for d, half, label in ((dr, half_r, "root"), (dt, half_t, "tip")):
                if d <= g.EPS:
                    half.append(0.0)
                    continue
                k = self.kerf.kerf(self.job.cut_speed * d / fastest)
                self.kerf_used[label].append(k)
                half.append(k / 2)
        for outline, poly, half, comp in ((self.root, poly_r, half_r, comp_r), (self.tip, poly_t, half_t, comp_t)):
            placed = [outline.place(p) for p in poly]
            for i, (new, h) in enumerate(g.offset_polygon(placed, half, outward)):
                if g.dist(new, placed[i]) > 4 * h + 1e-9:
                    where = "outer contour" if feature is None else f'feature "{feature}"'
                    raise CamError(
                        f"{outline.section.label}: kerf compensation of the {where} moves point {i} "
                        f"at ({placed[i][0]:.3f}, {placed[i][1]:.3f}) by {g.dist(new, placed[i]):.3f} mm, "
                        f"more than 4 x half the kerf; the corner is too sharp"
                    )
                comp[refs[i]] = new

    # --- §10.8 projection and §11 timing ----------------------------------

    def project(self, r: Point, t: Point) -> Axes:
        yr, yt, span = self.job.root.y, self.job.tip.y, self.profile.wire_span
        s0 = (0.0 - yr) / (yt - yr)
        s1 = (span - yr) / (yt - yr)
        return (r[0] + s0 * (t[0] - r[0]), r[1] + s0 * (t[1] - r[1]), r[0] + s1 * (t[0] - r[0]), r[1] + s1 * (t[1] - r[1]))

    def run(self) -> CamResult:
        job = self.job
        nodes_r, nodes_t, tags = self.synchronise()
        outer_r, outer_t = nodes_r[:-1], nodes_t[:-1]
        refs = self.path(len(outer_r), tags)
        comp_r, comp_t = self.compensate(outer_r, outer_t)

        x_entry = job.block.x + job.block.length + job.lead_in
        start = refs[0]
        entry_r = (x_entry, comp_r[start][1])
        entry_t = (x_entry, comp_t[start][1])
        points = [(entry_r, entry_t)] + [(comp_r[ref], comp_t[ref]) for ref in refs] + [(entry_r, entry_t)]

        moves = [Move("rapid", job.safe_position), Move("rapid", self.project(entry_r, entry_t))]
        last = len(points) - 1
        prev_r, prev_t = points[0]
        for k in range(1, len(points)):
            r, t = points[k]
            dr, dt = g.dist(prev_r, r), g.dist(prev_t, t)
            if max(dr, dt) < 1e-6:
                continue
            kind = "lead_in" if k == 1 else "exit" if k == last else "cut"
            moves.append(Move(kind, self.project(r, t), max(dr, dt) / job.cut_speed, dr, dt))
            prev_r, prev_t = r, t
        moves.append(Move("rapid", job.safe_position))

        self.check_rapids(moves)
        if job.mirror:
            for move in moves:
                a1, a2, a3, a4 = move.axes
                move.axes = (a3, a4, a1, a2)
        lines = self.gcode_lines(moves)
        self.check(moves, lines)
        if self.problems:
            raise CamError(self.problems)

        total = sum(m.duration for m in moves)
        result = CamResult("", "", "", total, moves)
        from .output import preview_svg, report

        result.gcode = "\n".join(lines.text) + "\n"
        result.report = report(job, self.profile, result, self.kerf_used)
        result.preview_svg = preview_svg(job, self.root, self.tip, comp_r, comp_t, refs)
        return result

    # --- §13 G-code ---------------------------------------------------------

    def gcode_lines(self, moves: list[Move]) -> "Program":
        job, profile = self.job, self.profile
        program = Program()
        for text in (
            f"foamcam {__version__}",
            f"job: {job.name}",
            f"profile: {profile.name}",
            f"kerf table: {job.kerf_table}",
            f"cut speed: {job.cut_speed:g} mm/min at the faster foam face",
            f"mirror: {'yes' if job.mirror else 'no'}",
            f"total cut time: {sum(m.duration for m in moves):.2f} min",
            "wire heat is not controlled by this program",
        ):
            program.add(comment(text))
        for code in ("G21", "G54", "G90", "G93"):
            program.add(code)
        previous_words = ""
        for index, move in enumerate(moves):
            words = " ".join(f"{letter}{axis_value(v)}" for letter, v in zip(profile.letters, move.axes))
            if move.kind == "rapid":
                program.add(f"G0 {words}", index)
            elif words != previous_words:
                program.add(f"G1 {words} F{inverse_time(move.duration)}", index)
            previous_words = words
        program.add("G94")
        return program

    # --- §11.1 checks -------------------------------------------------------

    def check(self, moves: list[Move], lines: "Program") -> None:
        profile = self.profile
        worst: dict[int, tuple[float, float, str, float, int]] = {}
        for line_number, index in lines.motion_lines():
            for axis, value in zip(profile.axes, moves[index].axes):
                for excess, side, limit in ((axis.min - value, "min", axis.min), (value - axis.max, "max", axis.max)):
                    if excess > 1e-9 and excess > worst.get(axis.number, (0.0,))[0]:
                        worst[axis.number] = (excess, value, side, limit, line_number)
        for number in sorted(worst):
            excess, value, side, limit, line_number = worst[number]
            axis = profile.axes[number - 1]
            self.problems.append(
                f"{axis.label} goes to {value:.4f}, beyond its {side} {limit:.4f} by {excess:.4f} mm "
                f"(program line {line_number})"
            )

        # Wire angle, speed ratio and axis rate: report the first offending line of each.
        found: dict[str, str] = {}
        previous: Move | None = None
        for line_number, index in lines.motion_lines():
            move = moves[index]
            a1, a2, a3, a4 = move.axes
            angle = math.degrees(math.atan(math.hypot(a3 - a1, a4 - a2) / profile.wire_span))
            if angle > profile.max_wire_angle + 1e-9:
                found.setdefault(
                    "angle",
                    f"wire angle {angle:.2f} degrees exceeds max_wire_angle {profile.max_wire_angle:g} "
                    f"(program line {line_number})",
                )
            if previous is not None and move.kind == "cut":
                ratio = speed_ratio(move)
                if ratio > profile.max_speed_ratio + 1e-9:
                    found.setdefault(
                        "ratio",
                        f"speed ratio {ratio:.2f} between the foam faces exceeds max_speed_ratio "
                        f"{profile.max_speed_ratio:g} (program line {line_number})",
                    )
            if previous is not None and move.kind != "rapid":
                for axis, before, after in zip(profile.axes, previous.axes, move.axes):
                    rate = abs(after - before) / move.duration
                    if rate > axis.max_rate + 1e-6:
                        found.setdefault(
                            f"rate{axis.number}",
                            f"{axis.label} would move at {rate:.1f} mm/min, above its max_rate "
                            f"{axis.max_rate:g} (program line {line_number})",
                        )
            previous = move
        self.problems += found.values()

    def check_rapids(self, moves: list[Move]) -> None:
        """§10.6: the rapids between the safe position and the entry point stay clear of the block."""
        for a, b, what in ((moves[0], moves[1], "to the entry point"), (moves[-2], moves[-1], "back to the safe position")):
            if self.rapid_hits_block(a.axes, b.axes):
                self.problems.append(f"the rapid move {what} passes through the foam block")

    def rapid_hits_block(self, start: Axes, end: Axes) -> bool:
        block, span = self.job.block, self.profile.wire_span
        y_lo, y_hi = sorted((self.job.root.y, self.job.tip.y))
        planes = 100
        for j in range(planes + 1):
            s = (y_lo + (y_hi - y_lo) * j / planes) / span
            p0 = (start[0] + s * (start[2] - start[0]), start[1] + s * (start[3] - start[1]))
            p1 = (end[0] + s * (end[2] - end[0]), end[1] + s * (end[3] - end[1]))
            if segment_hits_box(p0, p1, (block.x, 0.0), (block.x + block.length, block.height)):
                return True
        return False


class Program:
    """G-code lines, remembering which move each motion line came from."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.move_of_line: dict[int, int] = {}

    def add(self, line: str, move_index: int | None = None) -> None:
        self.text.append(line)
        if move_index is not None:
            self.move_of_line[len(self.text)] = move_index

    def motion_lines(self) -> list[tuple[int, int]]:
        return sorted(self.move_of_line.items())


def slit_name(label: str) -> str | None:
    return label[len('slit "') : -1] if label.startswith('slit "') else None


def speed_ratio(move: Move) -> float:
    slow, fast = sorted((move.root_length, move.tip_length))
    return math.inf if slow <= 1e-12 else fast / slow


def comment(text: str) -> str:
    return "(" + text.replace("(", "[").replace(")", "]") + ")"


def axis_value(value: float) -> str:
    text = f"{value:.4f}"
    return "0.0000" if text == "-0.0000" else text


def inverse_time(minutes: float) -> str:
    """Feed for G93: 1/minutes, plain decimal, at least 6 significant digits."""
    f = 1.0 / minutes
    decimals = max(1, 5 - math.floor(math.log10(f)))
    return f"{f:.{decimals}f}"


def segment_hits_box(p0: Point, p1: Point, low: Point, high: Point) -> bool:
    """Liang-Barsky: does segment p0-p1 enter the open box low-high?"""
    t0, t1 = 0.0, 1.0
    d = (p1[0] - p0[0], p1[1] - p0[1])
    for axis in (0, 1):
        for p, q in ((-d[axis], p0[axis] - low[axis]), (d[axis], high[axis] - p0[axis])):
            if abs(p) < 1e-12:
                if q <= 0:
                    return False
                continue
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    return t1 - t0 > 1e-9
