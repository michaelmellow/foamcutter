"""From a job and a machine profile to G-code (job-file.md §10 to §13)."""

import math
from dataclasses import dataclass, field
from itertools import pairwise

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

# §10.7: the kerf lookup is repeated until no half kerf changes by more than
# KERF_SETTLED mm, for at most KERF_ROUNDS lookups from the compensated path.
KERF_ROUNDS = 10
KERF_SETTLED = 0.001
# §10.7: where the kerf changes at a vertex, the two offset edges are
# intersected exactly if they turn by at least this angle (degrees);
# otherwise the vertex takes the mean half kerf.
EXACT_JOIN_ANGLE = 20.0
# §10.3: two breakpoints closer than this (mm) at both faces are merged.
MERGE_GAP = 0.001


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
            check_in_block(outline, job.block, profile.table_z, problems)
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
        self.kerf_rounds: dict[str, int] = {}
        self.stretches: list[tuple[str, float, float]] = []  # (from to, root kerf, tip kerf)
        self.problems: list[str] = []

    # --- §10.7 kerf compensation and §10.3 synchronisation ----------------

    def compensate(self) -> tuple[dict[Ref, Point], dict[Ref, Point], list[str | None]]:
        """Compensated points of every path node, and the slit tag of each outer node."""
        nodes_r, nodes_t, tags = self.outer_contour()
        comp_r: dict[Ref, Point] = {("outer", i): p for i, p in enumerate(nodes_r[:-1])}
        comp_t: dict[Ref, Point] = {("outer", i): p for i, p in enumerate(nodes_t[:-1])}
        for name, f_r in self.root.features.items():
            f_t = self.tip.features[name]
            placed_r = [self.root.place(p) for p in f_r.points]
            placed_t = [self.tip.place(p) for p in f_t.points]
            if f_r.kind == "hole":
                # Feature edges are not resampled: each edge is one move with its own kerf.
                k = len(placed_r)
                edges = [[i] for i in range(k)]
                placed_r, placed_t, _, _ = self.settle(f'feature "{name}"', placed_r, placed_t, edges, edges, False)
            for i, (p, q) in enumerate(zip(placed_r, placed_t)):
                comp_r[("feature", name, i)] = p
                comp_t[("feature", name, i)] = q
        return comp_r, comp_t, tags

    def outer_contour(self) -> tuple[list[Point], list[Point], list[str | None]]:
        """Compensated, paired outer-contour nodes (closed: last = first) and their slit tags.

        Each stretch between fixed pairs has one kerf per face (§10.7). The two
        offset polygons are then synchronised stretch by stretch (§10.3).
        """
        job = self.job
        loop_r = traversal(self.root, job.direction)
        loop_t = traversal(self.tip, job.direction)
        pos_r, labels = fixed_points(self.root, job.direction)
        pos_t, _ = fixed_points(self.tip, job.direction)
        poly_r, pos_r = transition_vertices(
            [self.root.place(self.root.contour[i]) for i in loop_r[:-1]], pos_r, job.max_segment_length
        )
        poly_t, pos_t = transition_vertices(
            [self.tip.place(self.tip.contour[i]) for i in loop_t[:-1]], pos_t, job.max_segment_length
        )
        groups_r = [list(range(a, b)) for a, b in pairwise(pos_r)]
        groups_t = [list(range(a, b)) for a, b in pairwise(pos_t)]
        new_r, new_t, half_r, half_t = self.settle("outer contour", poly_r, poly_t, groups_r, groups_t, True)
        self.stretches = [
            (f"{labels[k]} to {labels[k + 1]}", 2 * hr, 2 * ht) for k, (hr, ht) in enumerate(zip(half_r, half_t))
        ]

        nodes_r, nodes_t = [new_r[0]], [new_t[0]]
        tags: list[str | None] = [slit_name(labels[0])]
        for k in range(1, len(pos_r)):
            piece_r = [new_r[i % len(new_r)] for i in range(pos_r[k - 1], pos_r[k] + 1)]
            piece_t = [new_t[i % len(new_t)] for i in range(pos_t[k - 1], pos_t[k] + 1)]
            sync_r, sync_t = g.synchronise(piece_r, piece_t, job.max_segment_length, MERGE_GAP)
            nodes_r += sync_r
            nodes_t += sync_t
            tags += [None] * (len(sync_r) - 1) + [slit_name(labels[k])]
        return nodes_r, nodes_t, tags

    def settle(
        self,
        where: str,
        poly_r: list[Point],
        poly_t: list[Point],
        groups_r: list[list[int]],
        groups_t: list[list[int]],
        outward: bool,
    ) -> tuple[list[Point], list[Point], list[float], list[float]]:
        """Offset two placed polygons, iterating the kerf of each group of edges (§10.7).

        Group j of the root and group j of the tip are cut by the same moves.
        Each group has one kerf per face, from the speed of its moves at that
        face. Returns the two offset polygons and the settled half kerfs per group.
        """

        def offsets(half_r: list[float], half_t: list[float]) -> tuple[list[Point], list[Point]]:
            return (
                self.offset_face(self.root, poly_r, edge_halves(groups_r, half_r, len(poly_r)), outward, where),
                self.offset_face(self.tip, poly_t, edge_halves(groups_t, half_t, len(poly_t)), outward, where),
            )

        # First lookup from the speeds of the uncompensated path. During the
        # iteration a speed outside the kerf table takes the nearer table end.
        half_r, half_t = self.group_halves(poly_r, poly_t, groups_r, groups_t, [])
        for rounds in range(1, KERF_ROUNDS + 1):
            new_r, new_t = offsets(half_r, half_t)
            out_of_table: list[str] = []  # only the settled lookup counts
            next_r, next_t = self.group_halves(new_r, new_t, groups_r, groups_t, out_of_table)
            change = max(abs(a - b) for a, b in zip(half_r + half_t, next_r + next_t))
            if change <= KERF_SETTLED:
                break
            half_r, half_t = next_r, next_t
        else:
            raise CamError(
                f"kerf compensation of the {where} has not settled after {KERF_ROUNDS} rounds: "
                f"a half kerf still changes by {change:.4f} mm"
            )
        self.kerf_rounds[where] = rounds
        if out_of_table:
            # The settled lookup is outside the table: reported with the other
            # checks, and the program is not written.
            self.problems.append(out_of_table[0])
        for label, half in (("root", half_r), ("tip", half_t)):
            self.kerf_used[label] += [2 * h for h in half if h > 0]
        return new_r, new_t, half_r, half_t

    def group_halves(
        self,
        poly_r: list[Point],
        poly_t: list[Point],
        groups_r: list[list[int]],
        groups_t: list[list[int]],
        out_of_table: list[str],
    ) -> tuple[list[float], list[float]]:
        """Half the kerf of each group at each face, from the speed of its moves there.

        Synchronisation moves both faces in proportion along a group, so its
        speed at a face is cut_speed x (its length there) / (the longer of its two lengths).
        A speed outside the kerf table is added to `out_of_table` and takes the
        kerf at the nearer end of the table, so that the other checks can still run.
        """
        half_r: list[float] = []
        half_t: list[float] = []
        for group_r, group_t in zip(groups_r, groups_t):
            dr = sum(g.dist(poly_r[i], poly_r[(i + 1) % len(poly_r)]) for i in group_r)
            dt = sum(g.dist(poly_t[i], poly_t[(i + 1) % len(poly_t)]) for i in group_t)
            fastest = max(dr, dt)
            for d, half in ((dr, half_r), (dt, half_t)):
                half.append(0.0 if d <= g.EPS else self.lookup(self.job.cut_speed * d / fastest, out_of_table) / 2)
        return half_r, half_t

    def lookup(self, speed: float, out_of_table: list[str]) -> float:
        try:
            return self.kerf.kerf(speed)
        except CamError as error:
            out_of_table.append(str(error))
            points = self.kerf.points
            return points[0][1] if speed < points[0][0] else points[-1][1]

    def path(self, n_outer: int, tags: list[str | None]) -> list[Ref]:
        """§10.4: the order of moves through the path nodes."""
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

    def offset_face(self, outline: Outline, placed: list[Point], half: list[float], outward: bool, where: str) -> list[Point]:
        """Offset one face's polygon (§10.7), with the guard against spikes."""
        result: list[Point] = []
        for i, (new, h) in enumerate(g.offset_polygon(placed, half, outward, EXACT_JOIN_ANGLE)):
            if g.dist(new, placed[i]) > 4 * h + 1e-9:
                raise CamError(
                    f"{outline.section.label}: kerf compensation of the {where} moves point {i} "
                    f"at ({placed[i][0]:.3f}, {placed[i][1]:.3f}) by {g.dist(new, placed[i]):.3f} mm, "
                    f"more than 4 x half the kerf; the corner is too sharp"
                )
            result.append(new)
        return result

    # --- §10.8 projection and §11 timing ----------------------------------

    def project(self, r: Point, t: Point) -> Axes:
        yr, yt, span = self.job.root.y, self.job.tip.y, self.profile.wire_span
        s0 = (0.0 - yr) / (yt - yr)
        s1 = (span - yr) / (yt - yr)
        return (r[0] + s0 * (t[0] - r[0]), r[1] + s0 * (t[1] - r[1]), r[0] + s1 * (t[0] - r[0]), r[1] + s1 * (t[1] - r[1]))

    def run(self) -> CamResult:
        job = self.job
        comp_r, comp_t, tags = self.compensate()
        refs = self.path(len(tags) - 1, tags)

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
        result.report = report(job, self.profile, result, self.kerf_used, self.stretches, self.kerf_rounds)
        result.preview_svg = preview_svg(job, self.profile, self.root, self.tip, comp_r, comp_t, refs)
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
        carried = 0.0  # minutes of G1 lines dropped since the last one written
        for index, move in enumerate(moves):
            words = " ".join(f"{letter}{axis_value(v)}" for letter, v in zip(profile.letters, move.axes))
            if move.kind == "rapid":
                program.add(f"G0 {words}", index)
            elif words == previous_words:
                # Same position after rounding (§13): drop the line, keep its time.
                carried += move.duration
            else:
                program.add(f"G1 {words} F{inverse_time(move.duration + carried)}", index)
                carried = 0.0
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
        block, span, z0 = self.job.block, self.profile.wire_span, self.profile.table_z
        y_lo, y_hi = sorted((self.job.root.y, self.job.tip.y))
        planes = 100
        for j in range(planes + 1):
            s = (y_lo + (y_hi - y_lo) * j / planes) / span
            p0 = (start[0] + s * (start[2] - start[0]), start[1] + s * (start[3] - start[1]))
            p1 = (end[0] + s * (end[2] - end[0]), end[1] + s * (end[3] - end[1]))
            if segment_hits_box(p0, p1, (block.x, z0), (block.x + block.length, z0 + block.height)):
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


def transition_vertices(polygon: list[Point], fixed: list[int], distance: float) -> tuple[list[Point], list[int]]:
    """§10.7: confine the kerf change at each nearly-in-line join.

    `polygon` is a closed contour in traversal order and `fixed` the positions
    of its fixed points (the last is len(polygon), the start again). Where the
    contour turns by less than EXACT_JOIN_ANGLE at a fixed point, a vertex is
    added on each of its two edges `distance` from it, if that edge is longer.
    Returns the new polygon and the new positions of the fixed points.
    """
    n = len(polygon)
    joins = {p % n for p in fixed}
    before: set[int] = set()  # add a vertex on the edge ending at this vertex
    after: set[int] = set()  # add a vertex on the edge starting at this vertex
    for j in joins:
        if g.turn_angle(polygon, j) < EXACT_JOIN_ANGLE:
            before.add(j)
            after.add(j)
    result: list[Point] = []
    new_index: dict[int, int] = {}
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        new_index[i] = len(result)
        result.append(a)
        length = g.dist(a, b)
        cuts = []
        if i in after and length > distance + g.EPS:
            cuts.append(distance / length)
        if (i + 1) % n in before and length > distance + g.EPS:
            cuts.append(1 - distance / length)
        for f in sorted(set(cuts)):
            if 0 < f < 1:
                result.append((a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])))
    return result, [new_index[p] if p < n else len(result) for p in fixed]


def edge_halves(groups: list[list[int]], half: list[float], n: int) -> list[float]:
    """Half kerf of every polygon edge, from the half kerf of its group."""
    result = [0.0] * n
    for group, h in zip(groups, half):
        for i in group:
            result[i] = h
    return result


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
