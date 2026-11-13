"""Plane geometry on (horizontal, vertical) point pairs."""

import math
from bisect import bisect_left
from collections.abc import Sequence
from itertools import pairwise

Point = tuple[float, float]

EPS = 1e-9


def dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def length(points: Sequence[Point]) -> float:
    return sum(dist(a, b) for a, b in pairwise(points))


def cumulative_lengths(points: Sequence[Point]) -> list[float]:
    cumulative = [0.0]
    for a, b in pairwise(points):
        cumulative.append(cumulative[-1] + dist(a, b))
    return cumulative


def point_at(points: Sequence[Point], cumulative: Sequence[float], fraction: float) -> Point:
    """Point at an arc-length fraction (0 … 1) along a polyline."""
    total = cumulative[-1]
    if fraction >= 1.0 or total <= 0:
        return points[-1] if fraction >= 1.0 else points[0]
    target = total * fraction
    i = max(1, bisect_left(cumulative, target))
    a, b = points[i - 1], points[i]
    seg = cumulative[i] - cumulative[i - 1]
    f = 0.0 if seg <= 0 else (target - cumulative[i - 1]) / seg
    return a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])


def synchronise(
    piece_r: Sequence[Point], piece_t: Sequence[Point], max_segment: float, merge_gap: float
) -> tuple[list[Point], list[Point]]:
    """Paired points along two polylines between fixed pairs (job-file.md §10.3).

    The breakpoints are the arc-length fractions of every vertex of both pieces,
    with extra equal steps between them so that no move is longer than
    `max_segment` on either piece. Both pieces are evaluated at every breakpoint,
    so each piece's own vertices are kept exactly, except that a breakpoint less
    than `merge_gap` from the previous one at both faces is dropped (the last
    one, fraction 1, replaces its predecessor instead). Returns the points at
    fractions above 0, ending with the last point of each piece.
    """
    cum_r, cum_t = cumulative_lengths(piece_r), cumulative_lengths(piece_t)
    fractions = {1.0}
    for cumulative in (cum_r, cum_t):
        if cumulative[-1] > 0:
            fractions.update(c / cumulative[-1] for c in cumulative[1:-1])
    longest = max(cum_r[-1], cum_t[-1])
    # Between two breakpoints neither piece has a vertex, so a fraction step w
    # moves w x length at each face: both are below merge_gap when the longer is.
    breaks = [0.0]
    for f in sorted(fractions):
        if (f - breaks[-1]) * longest >= merge_gap:
            breaks.append(f)
        elif f == 1.0 and len(breaks) > 1:
            breaks[-1] = 1.0
    if breaks[-1] != 1.0:
        breaks.append(1.0)
    nodes_r: list[Point] = []
    nodes_t: list[Point] = []
    for a, b in pairwise(breaks):
        steps = max(1, math.ceil((b - a) * longest / max_segment - 1e-9))
        for j in range(1, steps + 1):
            f = b if j == steps else a + (b - a) * j / steps
            nodes_r.append(point_at(piece_r, cum_r, f))
            nodes_t.append(point_at(piece_t, cum_t, f))
    return nodes_r, nodes_t


def signed_area(polygon: Sequence[Point]) -> float:
    n = len(polygon)
    return 0.5 * sum(
        polygon[i][0] * polygon[(i + 1) % n][1] - polygon[(i + 1) % n][0] * polygon[i][1] for i in range(n)
    )


def segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    """True if segments ab and cd cross at a point interior to both."""
    d1, d2 = cross(c, d, a), cross(c, d, b)
    d3, d4 = cross(a, b, c), cross(a, b, d)
    return ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and (
        (d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)
    )


def edges(polygon: Sequence[Point], closed: bool = True) -> list[tuple[Point, Point]]:
    n = len(polygon)
    count = n if closed else n - 1
    return [(polygon[i], polygon[(i + 1) % n]) for i in range(count)]


def self_intersects(polygon: Sequence[Point]) -> bool:
    es = edges(polygon)
    n = len(es)
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # neighbours across the closing vertex
            if segments_cross(es[i][0], es[i][1], es[j][0], es[j][1]):
                return True
    return False


def any_crossing(a: Sequence[tuple[Point, Point]], b: Sequence[tuple[Point, Point]]) -> bool:
    return any(segments_cross(p, q, r, s) for p, q in a for r, s in b)


def inside(point: Point, polygon: Sequence[Point]) -> bool:
    """Ray-casting point-in-polygon test."""
    x, y = point
    result = False
    n = len(polygon)
    for i in range(n):
        (x1, y1), (x2, y2) = polygon[i], polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            result = not result
    return result


def ray_hit(origin: Point, direction: Point, polygon: Sequence[Point]) -> tuple[float, int, float] | None:
    """Nearest hit of a ray on a closed polygon: (ray distance, edge index, fraction along edge)."""
    best: tuple[float, int, float] | None = None
    n = len(polygon)
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        ex, ey = b[0] - a[0], b[1] - a[1]
        denom = direction[0] * ey - direction[1] * ex
        if abs(denom) < EPS:
            continue
        wx, wy = a[0] - origin[0], a[1] - origin[1]
        t = (wx * ey - wy * ex) / denom
        s = (wx * direction[1] - wy * direction[0]) / denom
        if t > EPS and -EPS <= s <= 1 + EPS and (best is None or t < best[0]):
            best = (t, i, min(1.0, max(0.0, s)))
    return best


def turn_angle(polygon: Sequence[Point], v: int) -> float:
    """Angle in degrees by which a closed polygon turns at vertex v, skipping zero-length edges."""
    n = len(polygon)
    i = (v - 1) % n
    while dist(polygon[i], polygon[v]) <= EPS and i != v:
        i = (i - 1) % n
    j = (v + 1) % n
    while dist(polygon[v], polygon[j]) <= EPS and j != v:
        j = (j + 1) % n
    d1 = (polygon[v][0] - polygon[i][0], polygon[v][1] - polygon[i][1])
    d2 = (polygon[j][0] - polygon[v][0], polygon[j][1] - polygon[v][1])
    return abs(math.degrees(math.atan2(d1[0] * d2[1] - d1[1] * d2[0], d1[0] * d2[0] + d1[1] * d2[1])))


def offset_polygon(
    polygon: Sequence[Point], half_kerf: Sequence[float], outward: bool, exact_angle: float
) -> list[tuple[Point, float]]:
    """Mitred offset of a closed polygon (job-file.md §10.7).

    Edge i runs from vertex i to vertex i+1 and moves by half_kerf[i]. With
    `outward` the edges move away from the inside of the polygon, otherwise
    into it. Zero-length edges are skipped: their vertices take the offset of
    the nearest real edges. Returns each new vertex with the larger half kerf
    of the two edges that define it.

    Each new vertex is v + a·n1 + b·n2, where n1 and n2 are the unit normals
    of the two edges. If both half kerfs are equal, or the edges turn by at
    least `exact_angle` degrees, it is the intersection of the two offset
    edges. Otherwise (nearly collinear edges with different kerfs, whose
    intersection runs far away) it uses the mean half kerf of the two edges.
    """
    n = len(polygon)
    lengths = [dist(polygon[i], polygon[(i + 1) % n]) for i in range(n)]
    if not any(length > EPS for length in lengths):
        raise ValueError("polygon has no length")
    ccw = signed_area(polygon) > 0
    # For a counter-clockwise polygon the outside is to the right of each edge.
    right = ccw == outward

    def normal(i: int) -> Point:
        a, b = polygon[i], polygon[(i + 1) % n]
        dx, dy = (b[0] - a[0]) / lengths[i], (b[1] - a[1]) / lengths[i]
        return (dy, -dx) if right else (-dy, dx)

    def real_edge(i: int, step: int) -> int:
        while lengths[i % n] <= EPS:
            i += step
        return i % n

    result: list[tuple[Point, float]] = []
    for v in range(n):
        e1 = real_edge(v - 1, -1)  # edge arriving at v
        e2 = real_edge(v, +1)  # edge leaving v
        h1, h2 = half_kerf[e1], half_kerf[e2]
        n1, n2 = normal(e1), normal(e2)
        px, py = polygon[v]
        dot = n1[0] * n2[0] + n1[1] * n2[1]
        if dot <= -1 + EPS:
            # The contour doubles back on itself: no finite mitre.
            result.append(((math.inf, math.inf), max(h1, h2)))
            continue
        if h1 != h2 and dot <= math.cos(math.radians(exact_angle)):
            # Intersection of the offset lines: q·n1 = v·n1 + h1 and q·n2 = v·n2 + h2.
            a = (h1 - dot * h2) / (1 - dot * dot)
            b = (h2 - dot * h1) / (1 - dot * dot)
        else:
            a = b = (h1 + h2) / 2 / (1 + dot)
        result.append(((px + a * n1[0] + b * n2[0], py + a * n1[1] + b * n2[1]), max(h1, h2)))
    return result
