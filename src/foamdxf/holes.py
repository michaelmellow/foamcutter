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
"""

import math
from dataclasses import dataclass

from .errors import DxfError
from .outline import Point, signed_area

CORNER_DEGREES = 25.0  # a vertex turning more than this is a corner kept in both loops
MIN_CIRCLE_POINTS = 16  # a hole without corners gets at least this many points
CHANNELS = ("shortest", "upper", "lower")


@dataclass(frozen=True)
class HolePair:
    name: str
    slit: str  # "upper" or "lower"
    root: tuple[Point, ...]  # same length as tip, first point where the channel arrives
    tip: tuple[Point, ...]
    channel_lengths: tuple[float, float]  # root, tip, mm


def pair_holes(
    root_outer: tuple[Point, ...],
    root_holes: tuple[tuple[Point, ...], ...],
    tip_outer: tuple[Point, ...],
    tip_holes: tuple[tuple[Point, ...], ...],
    channel: str = "shortest",
    max_segment: float = 1.0,
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
        pairs.append(HolePair(f"hole{k + 1}", side, r_pts, t_pts, (lr, lt)))
    return pairs


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
