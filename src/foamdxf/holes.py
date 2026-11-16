"""Inner contours (spar holes, lightening holes) as job features (job-file.md §8).

The CAM reaches each hole through a straight channel (a "slit"): from the outer
contour straight up or down to the hole's first point, once round the hole and
back out the same way. This module turns the inner loops found in the root and
tip drawings into matching `kind: "hole"` features:

- **Pairing.** Root and tip must have the same number of holes. Each root hole
  is paired with the nearest tip hole (centres in the shared drawing frame).
- **Same vertex count.** §8 needs equal point lists at root and tip, and each
  vertex pair is cut by one move. Both loops are re-sampled by arc length into
  the same number of points, piece by piece between matching corners, so a
  rectangular slot keeps its corners (each straight side one move) and a
  circle becomes a polygon with sides of about `max_segment_length`.
- **Channel side.** The first point is the hole's top point (channel from the
  upper side) or bottom point (lower side). "shortest" picks, per hole, the
  side with the shorter channel at root and tip together.
- **Drawn channels.** A `ChannelSpec` per hole replaces that with a §8
  `channel`: a straight line at any angle, offset sideways from the hole
  centre, or a curve that leaves the hole along its edge and bends round to
  that angle. Root and tip get the same spec, each about its own hole centre,
  so the two channels match point for point.
"""

import json
import math
from dataclasses import asdict, dataclass, field

from foamcam.geometry import inside
from foamcam.job import Feature
from foamcam.sections import channel_problem

from .errors import DxfError
from .outline import Point, signed_area

CORNER_DEGREES = 25.0  # a vertex turning more than this is a corner kept in both loops
MIN_CIRCLE_POINTS = 16  # a hole without corners gets at least this many points
CHANNELS = ("shortest", "upper", "lower")
SHAPES = ("auto", "straight", "curved")  # auto: straight up or down, from the CHANNELS setting
BEYOND_MM = 2.0  # a drawn channel's last point lies this far past the outer contour (§8)


@dataclass(frozen=True)
class ChannelSpec:
    """How one hole's channel is drawn. Angles in degrees: 0 = +x (toward the
    trailing edge), 90 = +z (up). Root and tip use the same spec."""

    shape: str = "auto"  # one of SHAPES
    angle: float = 90.0  # direction the channel runs out toward
    offset: float = 0.0  # straight: sideways distance of its line from the hole centre, + to the left
    entry: float = 90.0  # curved: where it leaves the hole, seen from the hole centre
    radius: float = 5.0  # curved: radius of the bend, mm


@dataclass(frozen=True)
class HolePair:
    name: str
    slit: str  # "upper" or "lower"
    root: tuple[Point, ...]  # same length as tip, first point where the channel arrives
    tip: tuple[Point, ...]
    channel_lengths: tuple[float, float]  # root, tip, mm
    root_channel: tuple[Point, ...] = ()  # §8 channel; () for a straight up or down slit
    tip_channel: tuple[Point, ...] = ()
    root_drawn: tuple[Point, ...] = ()  # the hole as drawn (counter-clockwise), for the editor
    tip_drawn: tuple[Point, ...] = ()
    spec: ChannelSpec = field(default_factory=ChannelSpec)


def specs_from_text(text: str) -> dict[str, ChannelSpec]:
    """Channel specs by hole name from their JSON form (as kept in the GUI settings)."""
    if not text.strip():
        return {}
    try:
        raw = json.loads(text)
        specs = {str(name): ChannelSpec(**fields) for name, fields in raw.items()}
    except (ValueError, TypeError, AttributeError) as error:
        raise DxfError(f"channel settings cannot be read: {error}") from None
    for name, spec in specs.items():
        if spec.shape not in SHAPES:
            raise DxfError(f'{name}: channel shape must be one of {", ".join(SHAPES)}')
        if spec.shape == "curved" and spec.radius <= 0:
            raise DxfError(f"{name}: the bend radius must be more than 0")
    return specs


def specs_to_text(specs: dict[str, ChannelSpec]) -> str:
    return json.dumps({name: asdict(spec) for name, spec in sorted(specs.items()) if spec.shape != "auto"})


def pair_holes(
    root_outer: tuple[Point, ...],
    root_holes: tuple[tuple[Point, ...], ...],
    tip_outer: tuple[Point, ...],
    tip_holes: tuple[tuple[Point, ...], ...],
    channel: str = "shortest",
    max_segment: float = 1.0,
    specs: dict[str, ChannelSpec] | None = None,
) -> list[HolePair]:
    """Matching features for the holes of both sections. Raises DxfError."""
    if channel not in CHANNELS:
        raise DxfError(f'hole channel must be one of {", ".join(CHANNELS)}, not "{channel}"')
    if len(root_holes) != len(tip_holes):
        raise DxfError(
            f"the root has {len(root_holes)} inner contour(s) and the tip {len(tip_holes)}; "
            "every hole must be drawn at both ends"
        )
    todo = list(tip_holes)
    pairs = []
    for k, r in enumerate(sorted(root_holes, key=lambda h: _centre(h)[0])):
        t = min(todo, key=lambda h: math.dist(_centre(h), _centre(r)))
        todo.remove(t)
        name = f"hole{k + 1}"
        spec = (specs or {}).get(name, ChannelSpec())
        if spec.shape != "auto":
            pairs.append(drawn_channel(name, spec, root_outer, r, tip_outer, t, max_segment))
            continue
        sides = ("upper", "lower") if channel == "shortest" else (channel,)
        options = []
        for side in sides:
            rs, ts = _start(r, side), _start(t, side)
            lr, lt = _channel(root_outer, rs[0], side), _channel(tip_outer, ts[0], side)
            if lr is not None and lt is not None:
                options.append((lr + lt, side, rs, ts, lr, lt))
        if not options:
            raise DxfError(f"hole {k + 1}: a straight {' or '.join(sides)} channel does not reach the outer contour")
        _, side, rs, ts, lr, lt = min(options)
        r_pts, t_pts = _matched(rs, ts, max_segment)
        pairs.append(HolePair(name, side, r_pts, t_pts, (lr, lt), root_drawn=_ccw(r), tip_drawn=_ccw(t)))
    _check_channels(root_outer, [(p.name, p.slit, p.root, p.root_channel) for p in pairs], "root")
    _check_channels(tip_outer, [(p.name, p.slit, p.tip, p.tip_channel) for p in pairs], "tip")
    return pairs


# --- drawn channels ---------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelPath:
    """One section's drawn channel: where it leaves the hole, its §8 points, its side."""

    entry: Point
    edge: int  # edge of the counter-clockwise hole the entry lies on
    points: tuple[Point, ...]  # from the hole outward; the last lies BEYOND_MM past the outer contour
    slit: str  # side of the outer contour it meets: "upper" or "lower"
    length: float  # from the entry to the outer contour, mm
    clockwise: bool  # the hole is cut clockwise (a curved channel that turns right)


def drawn_channel(
    name: str,
    spec: ChannelSpec,
    root_outer: tuple[Point, ...],
    root_hole: tuple[Point, ...],
    tip_outer: tuple[Point, ...],
    tip_hole: tuple[Point, ...],
    max_segment: float,
) -> HolePair:
    """The feature pair for a hole with a drawn channel. Raises DxfError."""
    rc = channel_path(spec, root_outer, root_hole, max_segment, where=f"{name} (root)")
    tc = channel_path(spec, tip_outer, tip_hole, max_segment, bends=_bends(rc, spec),
                      clockwise=rc.clockwise, where=f"{name} (tip)")
    if len(rc.points) != len(tc.points):  # the bend steps follow the root: always equal
        raise DxfError(f"{name}: the root and tip channels have different point counts")
    if rc.slit != tc.slit:
        raise DxfError(f"{name}: the channel meets the {rc.slit} side at the root but the {tc.slit} side at the tip")
    rs = _start_at(root_hole, rc.entry, rc.edge, rc.clockwise)
    ts = _start_at(tip_hole, tc.entry, tc.edge, tc.clockwise)
    r_pts, t_pts = _matched(rs, ts, max_segment)
    return HolePair(name, rc.slit, r_pts, t_pts, (rc.length, tc.length), rc.points, tc.points,
                    _ccw(root_hole), _ccw(tip_hole), spec)


def channel_path(
    spec: ChannelSpec,
    outer: tuple[Point, ...],
    hole: tuple[Point, ...],
    max_segment: float = 1.0,
    bends: int | None = None,
    clockwise: bool | None = None,
    where: str = "hole",
) -> ChannelPath:
    """One section's channel for `spec`. `bends` and `clockwise` force the bend's point
    count and turning direction (the tip follows the root). Raises DxfError."""
    loop = list(_ccw(hole))
    c = area_centre(loop)
    d = _unit(spec.angle)
    if spec.shape == "straight":
        o = (c[0] - spec.offset * d[1], c[1] + spec.offset * d[0])  # offset to the left of d
        hits = _line_hits(loop, o, d)
        if not hits:
            raise DxfError(f"{where}: a channel offset {spec.offset:g} mm from the centre misses the hole")
        t_e, edge = max(hits)
        entry = (o[0] + t_e * d[0], o[1] + t_e * d[1])
        start, bend, cw = entry, [], False
    else:
        u = _unit(spec.entry)
        hits = [(t, i) for t, i in _line_hits(loop, c, u) if t > 0]
        if not hits:
            raise DxfError(f"{where}: no hole edge in the entry direction {spec.entry:g} degrees")
        t_e, edge = max(hits)
        entry = (c[0] + t_e * u[0], c[1] + t_e * u[1])
        a, b = loop[edge], loop[(edge + 1) % len(loop)]
        tangent = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))  # counter-clockwise along the hole
        left = (spec.angle - (tangent + 180)) % 360  # leave backwards along the edge, turn left (hole cut ccw)
        right = (tangent - spec.angle) % 360  # leave forwards along the edge, turn right (hole cut cw)
        cw = right < left if clockwise is None else clockwise
        heading, turn = (tangent, -right) if cw else (tangent + 180, left)
        if bends is not None:
            steps = bends
        elif abs(turn) < 0.01:  # already heading the right way: no bend
            steps = 0
        else:
            steps = max(2, math.ceil(math.radians(abs(turn)) * spec.radius / max_segment))
        bend = _arc(entry, heading, turn, spec.radius, steps)
        start = bend[-1] if bend else entry
        if not all(inside(q, list(outer)) for q in bend):
            raise DxfError(f"{where}: the bend runs out of the outer contour; make the bend radius smaller")
    out = [(t, i) for t, i in _line_hits(list(outer), start, d) if t > 1e-9]
    if not out:
        raise DxfError(f"{where}: the channel never reaches the outer contour")
    out.sort()
    t_q, q_edge = out[0]
    t_x = t_q + (min(BEYOND_MM, (out[1][0] - t_q) / 2) if len(out) > 1 else BEYOND_MM)
    last = (start[0] + t_x * d[0], start[1] + t_x * d[1])
    length = sum(math.dist(p, q) for p, q in zip([entry, *bend], bend)) + t_q
    return ChannelPath(entry, edge, (*bend, last), _side(outer, q_edge), length, cw)


def channel_problems(
    outer: tuple[Point, ...], holes: list[tuple[str, str, tuple[Point, ...], tuple[Point, ...]]]
) -> list[str]:
    """What the CAM would refuse about the drawn channels (§8 Channels), per hole:
    (name, slit, hole points starting at the entry, channel)."""
    features = tuple(Feature(n, "hole", s, pts, ch) for n, s, pts, ch in holes)
    contour = list(outer)
    outer_edges = _edges(contour)
    feature_edges = {f.name: _edges(list(f.points)) for f in features}
    found = []
    for f in features:
        if f.channel:
            why = channel_problem(f, contour, outer_edges, features, feature_edges)
            if why:
                found.append(f"{f.name}: the channel {why}")
    return found


@dataclass(frozen=True)
class ChannelPreview:
    """A drawn channel at both sections, for the editor; `problem` is "" if it can be cut."""

    root: ChannelPath | None
    tip: ChannelPath | None
    problem: str


def preview_channels(
    pairs: tuple[HolePair, ...],
    specs: dict[str, ChannelSpec],
    outers: tuple[tuple[Point, ...], tuple[Point, ...]],
    max_segment: float = 1.0,
) -> dict[str, ChannelPreview]:
    """The drawn channels `specs` give the holes of a built job (without running the
    CAM), with what the CAM would refuse about each. Holes without a spec are left out."""
    out: dict[str, ChannelPreview] = {}
    features: tuple[list[tuple[str, str, tuple[Point, ...], tuple[Point, ...]]], ...] = ([], [])
    for p in pairs:
        spec = specs.get(p.name, ChannelSpec())
        rc = tc = None
        problem = ""
        if spec.shape != "auto":
            try:
                rc = channel_path(spec, outers[0], p.root_drawn, max_segment, where="root")
                tc = channel_path(spec, outers[1], p.tip_drawn, max_segment, bends=_bends(rc, spec),
                                  clockwise=rc.clockwise, where="tip")
            except DxfError as error:
                problem = str(error)
            out[p.name] = ChannelPreview(rc, tc, problem)
        for k, (path, drawn, loop) in enumerate(((rc, p.root_drawn, p.root), (tc, p.tip_drawn, p.tip))):
            if path is None:
                features[k].append((p.name, p.slit, loop, ()))
            else:
                start = tuple(_start_at(drawn, path.entry, path.edge, path.clockwise))
                features[k].append((p.name, path.slit, start, path.points))
    for label, outer, found in (("root", outers[0], features[0]), ("tip", outers[1], features[1])):
        for problem in channel_problems(outer, found):
            name = problem.split(":", 1)[0]
            if name in out and not out[name].problem:
                out[name] = ChannelPreview(out[name].root, out[name].tip, f"{label}: {problem.split(': ', 1)[1]}")
    return out


def _check_channels(
    outer: tuple[Point, ...], holes: list[tuple[str, str, tuple[Point, ...], tuple[Point, ...]]], label: str
) -> None:
    problems = channel_problems(outer, holes)
    if problems:
        raise DxfError(f"{label}: " + "; ".join(problems))


def _edges(loop: list[Point]) -> list[tuple[Point, Point]]:
    return [(loop[i], loop[(i + 1) % len(loop)]) for i in range(len(loop))]


def _bends(path: ChannelPath, spec: ChannelSpec) -> int | None:
    return len(path.points) - 1 if spec.shape == "curved" else None


def area_centre(loop: list[Point]) -> Point:
    """Centre of area of a closed loop (its vertex mean if it has no area)."""
    a = signed_area(loop)
    if abs(a) < 1e-12:
        return _centre(tuple(loop))
    n = len(loop)
    cx = cz = 0.0
    for i in range(n):
        (x0, z0), (x1, z1) = loop[i], loop[(i + 1) % n]
        w = x0 * z1 - x1 * z0
        cx += (x0 + x1) * w
        cz += (z0 + z1) * w
    return cx / (6 * a), cz / (6 * a)


def _unit(degrees: float) -> Point:
    r = math.radians(degrees)
    return math.cos(r), math.sin(r)


def _line_hits(loop: list[Point], origin: Point, d: Point) -> list[tuple[float, int]]:
    """(t, edge) where the line origin + t * d crosses an edge of the closed loop."""
    out = []
    n = len(loop)
    for i in range(n):
        (xa, za), (xb, zb) = loop[i], loop[(i + 1) % n]
        ex, ez = xb - xa, zb - za
        den = d[0] * ez - d[1] * ex
        if abs(den) < 1e-12:
            continue
        wx, wz = xa - origin[0], za - origin[1]
        t = (wx * ez - wz * ex) / den
        s = (wx * d[1] - wz * d[0]) / den
        if -1e-9 <= s < 1 - 1e-9:  # each vertex belongs to one edge only
            out.append((t, i))
    return out


def _arc(start: Point, heading: float, turn: float, radius: float, steps: int) -> list[Point]:
    """`steps` points along an arc from `start` (not included) leaving at `heading`
    degrees and turning by `turn` degrees (+ left), radius `radius`."""
    h = math.radians(heading)
    side = 1.0 if turn >= 0 else -1.0
    cx = start[0] - side * radius * math.sin(h)
    cz = start[1] + side * radius * math.cos(h)
    a0 = math.atan2(start[1] - cz, start[0] - cx)
    return [
        (cx + radius * math.cos(a0 + math.radians(turn) * k / steps),
         cz + radius * math.sin(a0 + math.radians(turn) * k / steps))
        for k in range(1, steps + 1)
    ]


def _side(outer: tuple[Point, ...], edge: int) -> str:
    """Side of the outer contour (Selig order, as the CAM reads it, §6.1) an edge is on."""
    i_le = min(range(len(outer)), key=lambda i: (outer[i][0], i))
    return "upper" if edge < i_le else "lower"


def _ccw(loop: tuple[Point, ...]) -> tuple[Point, ...]:
    return tuple(loop) if signed_area(list(loop)) > 0 else tuple(loop)[::-1]


def _start_at(hole: tuple[Point, ...], entry: Point, edge: int, clockwise: bool) -> list[Point]:
    """The hole (counter-clockwise, or clockwise if asked) starting at `entry`, which lies
    on counter-clockwise edge `edge`; it becomes a vertex unless it is one already."""
    pts = list(_ccw(hole))
    n = len(pts)
    if math.dist(entry, pts[edge]) < 1e-6:
        loop = pts[edge:] + pts[:edge]
    elif math.dist(entry, pts[(edge + 1) % n]) < 1e-6:
        k = (edge + 1) % n
        loop = pts[k:] + pts[:k]
    else:
        loop = [entry] + pts[edge + 1 :] + pts[: edge + 1]
    return [loop[0], *loop[:0:-1]] if clockwise else loop


def _centre(loop: tuple[Point, ...]) -> Point:
    return sum(p[0] for p in loop) / len(loop), sum(p[1] for p in loop) / len(loop)


def _start(loop: tuple[Point, ...], side: str) -> list[Point]:
    """The loop counter-clockwise, starting at its top (upper) or bottom (lower) point."""
    pts = list(loop) if signed_area(list(loop)) > 0 else list(loop)[::-1]
    sign = 1.0 if side == "upper" else -1.0
    i = max(range(len(pts)), key=lambda j: (sign * pts[j][1], -pts[j][0]))
    return pts[i:] + pts[:i]


def _channel(outer: tuple[Point, ...], start: Point, side: str) -> float | None:
    """Length of the straight channel from `start` up or down to the outer contour."""
    sign = 1.0 if side == "upper" else -1.0
    x0, z0 = start
    best: float | None = None
    n = len(outer)
    for i in range(n):
        (xa, za), (xb, zb) = outer[i], outer[(i + 1) % n]
        if (xa - x0) * (xb - x0) > 0 or xa == xb:
            continue
        z = za + (zb - za) * (x0 - xa) / (xb - xa)
        d = sign * (z - z0)
        if d > 0 and (best is None or d < best):
            best = d
    return best


def _corners(loop: list[Point]) -> list[int]:
    """Index 0 (the start) and every vertex that turns more than CORNER_DEGREES."""
    out = [0]
    n = len(loop)
    for i in range(1, n):
        a, b, c = loop[i - 1], loop[i], loop[(i + 1) % n]
        h1 = math.atan2(b[1] - a[1], b[0] - a[0])
        h2 = math.atan2(c[1] - b[1], c[0] - b[0])
        turn = abs((h2 - h1 + math.pi) % (2 * math.pi) - math.pi)
        if math.degrees(turn) > CORNER_DEGREES:
            out.append(i)
    return out


def _pieces(loop: list[Point], breaks: list[int]) -> list[list[Point]]:
    """The closed loop cut at `breaks` into open polylines, each with both ends."""
    closed = loop + loop[:1]
    ends = breaks + [len(loop)]
    return [closed[ends[k] : ends[k + 1] + 1] for k in range(len(breaks))]


def _straight(line: list[Point], tol: float = 0.01) -> bool:
    (xa, za), (xb, zb) = line[0], line[-1]
    length = math.hypot(xb - xa, zb - za)
    if length == 0:
        return False
    return all(abs((xb - xa) * (z - za) - (zb - za) * (x - xa)) / length <= tol for x, z in line)


def _length(line: list[Point]) -> float:
    return sum(math.dist(line[i], line[i + 1]) for i in range(len(line) - 1))


def _resample(line: list[Point], n: int) -> list[Point]:
    """n + 1 points at equal arc length along `line`, both ends included."""
    total = _length(line)
    out = [line[0]]
    i, done = 0, 0.0
    for k in range(1, n):
        target = total * k / n
        while i < len(line) - 2 and done + math.dist(line[i], line[i + 1]) < target:
            done += math.dist(line[i], line[i + 1])
            i += 1
        seg = math.dist(line[i], line[i + 1])
        f = 0.0 if seg == 0 else (target - done) / seg
        a, b = line[i], line[i + 1]
        out.append((a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])))
    out.append(line[-1])
    return out


def _matched(root: list[Point], tip: list[Point], max_segment: float) -> tuple[tuple[Point, ...], tuple[Point, ...]]:
    cr, ct = _corners(root), _corners(tip)
    if len(cr) != len(ct):  # different shapes: match as smooth loops
        cr, ct = [0], [0]
    out_r: list[Point] = []
    out_t: list[Point] = []
    pieces = list(zip(_pieces(root, cr), _pieces(tip, ct), strict=True))
    for pr, pt in pieces:
        if len(pieces) > 1 and _straight(pr) and _straight(pt):
            n = 1  # a straight side is one move: short pieces at corners upset the kerf (§10.7)
        else:
            n = max(1, math.ceil(max(_length(pr), _length(pt)) / max(max_segment, 1e-3)))
        if len(pieces) == 1:
            n = max(n, MIN_CIRCLE_POINTS)
        out_r += _resample(pr, n)[:-1]
        out_t += _resample(pt, n)[:-1]
    return tuple(out_r), tuple(out_t)
