"""Read one closed section outline from a DXF file and put it in Selig order.

The outline is drawn in the DXF x-y plane, in mm (or in the units named by the
file's $INSUNITS header): x along the chord with the leading edge on the left
(smaller x), y up. Lines, arcs, circles, ellipses, polylines and splines are
read, also inside block references, and joined end to end.
"""

import math
from dataclasses import dataclass
from pathlib import Path

from ezdxf import path as ezpath
from ezdxf.entities.dxfgfx import DXFGraphic
from ezdxf.filemanagement import readfile
from ezdxf.lldxf.const import DXFError

from .errors import DxfError

Point = tuple[float, float]

# $INSUNITS code -> mm per drawing unit. 0 (unitless) is taken as mm.
UNIT_SCALE = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0}
CURVES = {"LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE", "POLYLINE", "SPLINE"}
TE_MERGE_RADIUS = 0.3  # mm; a trailing edge smaller than this is made sharp (see sharpen_trailing_edge)


@dataclass(frozen=True)
class DxfOutline:
    """A closed outline in mm, in Selig order (job-file.md §7.1).

    `points[0]` is the upper trailing edge, the list runs over the upper side to
    the leading edge and back along the lower side to the lower trailing edge.
    The closing edge from the last point back to the first is the trailing-edge
    face (zero length for a sharp trailing edge, which is then not repeated).
    """

    source: str
    points: tuple[Point, ...]
    warnings: tuple[str, ...]

    @property
    def le_index(self) -> int:
        """Index of the leading edge: the point with the smallest x (first if tied), as in job-file.md §6.1."""
        xs = [p[0] for p in self.points]
        return xs.index(min(xs))

    @property
    def chord(self) -> float:
        xs = [p[0] for p in self.points]
        return max(xs) - min(xs)


def read_outline(
    path: Path, join_tol: float = 0.1, flat_tol: float = 0.01, simplify_tol: float = 0.01
) -> DxfOutline:
    """Read `path` and return its outline. `join_tol` is the largest gap (mm)
    joined between pieces; `flat_tol` the largest deviation (mm) allowed when
    curves are turned into straight segments; `simplify_tol` the largest
    deviation (mm) allowed when nearly collinear points are then dropped (0 keeps
    every point). Dense points make very short moves, which a USB-streamed
    controller may not keep up with."""
    try:
        doc = readfile(str(path))
    except (OSError, DXFError) as error:
        raise DxfError(f"{path.name}: cannot read DXF: {error}") from None
    insunits = doc.header.get("$INSUNITS", 0)
    if insunits not in UNIT_SCALE:
        raise DxfError(f"{path.name}: drawing units code $INSUNITS={insunits} is not supported; use mm")
    scale = UNIT_SCALE[insunits]

    pieces: list[list[Point]] = []
    for entity in doc.modelspace():
        pieces.extend(_polylines(entity, flat_tol / scale))
    pieces = [[(x * scale, y * scale) for x, y in piece] for piece in pieces]
    pieces = [p for p in (_dedup(piece) for piece in pieces) if len(p) >= 2]
    if not pieces:
        raise DxfError(f"{path.name}: no lines, arcs, polylines or splines found")

    warnings: list[str] = []
    loops, opens = _chain(pieces, join_tol)
    if not loops:
        loops = _close_small_gap(path.name, opens, warnings)
    loops.sort(key=lambda lp: abs(signed_area(lp)), reverse=True)
    if len(loops) > 1:
        warnings.append(
            f"{path.name}: {len(loops)} closed outlines found; the largest is used, the "
            "others (for example spar slots) are ignored. Add them as job features by hand."
        )
    if opens and loops[0] is not opens[0]:
        warnings.append(f"{path.name}: {len(opens)} loose open piece(s) ignored")

    loop = loops[0]
    if signed_area(loop) < 0:
        loop = loop[::-1]
    points = selig_order(loop)
    points, merged = sharpen_trailing_edge(points, TE_MERGE_RADIUS)
    if merged:
        warnings.append(
            f"{path.name}: {merged + 1} points of a rounded trailing edge (within {TE_MERGE_RADIUS} mm) "
            "merged into one sharp point"
        )
    if simplify_tol > 0:
        xs = [p[0] for p in points]
        le = xs.index(min(xs))  # kept, like both trailing-edge points
        points = simplify(points[: le + 1], simplify_tol) + simplify(points[le:], simplify_tol)[1:]
    return DxfOutline(path.name, tuple(points), tuple(warnings))


def sharpen_trailing_edge(points: list[Point], radius: float) -> tuple[list[Point], int]:
    """Merge a tiny rounded or blunt trailing edge into one point.

    CAD airfoils often end in an arc or face a few hundredths of a mm across.
    That leaves foamcam a trailing-edge stretch far shorter than the kerf, whose
    kerf iteration (job-file.md §10.7) never settles. Only when the trailing
    edge (the distance between the first and last points in Selig order) is
    smaller than `radius`: the points next to the trailing edge (the point with
    the largest x) that lie within `radius` of it are removed, and the list then
    starts at that point (a sharp trailing edge in Selig order). A larger,
    deliberate trailing-edge face is left alone. The job's min_te_thickness can
    still open a sharp edge to a face. Returns the new list and the number of
    points removed.
    """
    n = len(points)
    if n < 6 or math.dist(points[0], points[-1]) >= radius:
        return points, 0
    xs = [p[0] for p in points]
    te = xs.index(max(xs))
    near = {te}
    for step in (1, -1):
        i = (te + step) % n
        while i not in near and math.dist(points[i], points[te]) < radius:
            near.add(i)
            i = (i + step) % n
    if len(near) == 1 or len(near) > n - 3:
        return points, 0
    rotated = points[te:] + points[:te]
    kept = [rotated[0]] + [p for j, p in enumerate(rotated[1:], start=1) if (te + j) % n not in near]
    return kept, len(near) - 1


def simplify(points: list[Point], tol: float) -> list[Point]:
    """Ramer-Douglas-Peucker: drop points while every dropped point stays within
    `tol` of the kept polyline. The first and last points are always kept."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        far, far_d = -1, tol
        for i in range(a + 1, b):
            d = _segment_distance(points[i], points[a], points[b])
            if d > far_d:
                far, far_d = i, d
        if far >= 0:
            keep[far] = True
            stack += [(a, far), (far, b)]
    return [p for p, k in zip(points, keep, strict=True) if k]


def _segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = b[0] - a[0], b[1] - a[1]
    length2 = ax * ax + ay * ay
    if length2 == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / length2))
    return math.dist(p, (a[0] + t * ax, a[1] + t * ay))


def _polylines(entity: DXFGraphic, flat_tol: float) -> list[list[Point]]:
    kind = entity.dxftype()
    if kind == "INSERT":
        out: list[list[Point]] = []
        for sub in entity.virtual_entities():  # type: ignore[attr-defined]
            out.extend(_polylines(sub, flat_tol))
        return out
    if kind not in CURVES:
        return []
    try:
        p = ezpath.make_path(entity)
    except (TypeError, ValueError):
        return []
    result = []
    for sub in p.sub_paths():
        pts = [(v.x, v.y) for v in sub.flattening(distance=flat_tol)]
        if len(pts) >= 2:
            result.append(pts)
    return result


def _dedup(points: list[Point], eps: float = 1e-6) -> list[Point]:
    out: list[Point] = []
    for p in points:
        if not out or math.dist(out[-1], p) > eps:
            out.append(p)
    return out


def _closed(points: list[Point], tol: float) -> bool:
    return len(points) > 2 and math.dist(points[0], points[-1]) <= tol


def _chain(pieces: list[list[Point]], tol: float) -> tuple[list[list[Point]], list[list[Point]]]:
    """Join pieces end to end. Returns (closed loops without the repeated end point, open chains)."""
    todo = list(pieces)
    loops: list[list[Point]] = []
    opens: list[list[Point]] = []
    while todo:
        cur = todo.pop(0)
        flipped = False
        while not _closed(cur, tol):
            best, best_d, best_rev = -1, tol, False
            for i, s in enumerate(todo):
                for rev, end in ((False, s[0]), (True, s[-1])):
                    d = math.dist(cur[-1], end)
                    if d <= best_d:
                        best, best_d, best_rev = i, d, rev
            if best < 0:
                if flipped:
                    break
                cur, flipped = cur[::-1], True
                continue
            s = todo.pop(best)
            cur = cur + (s[::-1] if best_rev else s)[1:]
        if _closed(cur, tol):
            loops.append(_dedup(cur[:-1]))
        else:
            opens.append(cur)
    return loops, opens


def _close_small_gap(name: str, opens: list[list[Point]], warnings: list[str]) -> list[list[Point]]:
    """Many airfoil DXFs leave the trailing edge open. Close one small gap."""
    if len(opens) == 1:
        chain = opens[0]
        gap = math.dist(chain[0], chain[-1])
        width = max(p[0] for p in chain) - min(p[0] for p in chain)
        if gap <= 0.05 * width:
            warnings.append(f"{name}: outline was open; closed a {gap:.2f} mm gap with a straight line")
            return [chain]
    ends = ", ".join(f"({c[0][0]:.2f}, {c[0][1]:.2f}) to ({c[-1][0]:.2f}, {c[-1][1]:.2f})" for c in opens[:4])
    raise DxfError(f"{name}: the outline is not closed. Open pieces: {ends}")


def signed_area(loop: list[Point]) -> float:
    n = len(loop)
    return 0.5 * sum(loop[i][0] * loop[(i + 1) % n][1] - loop[(i + 1) % n][0] * loop[i][1] for i in range(n))


def selig_order(loop: list[Point]) -> list[Point]:
    """Rotate a counter-clockwise loop so it starts at the upper trailing edge.

    The trailing edge is the point with the largest x. Counter-clockwise from
    there runs over the upper side first. If that point is the lower corner of
    a blunt trailing edge (a short, steep edge to the next point), start at the
    upper corner instead, so that the trailing-edge face is the closing edge.
    """
    n = len(loop)
    xs = [p[0] for p in loop]
    i = xs.index(max(xs))
    chord = max(xs) - min(xs)
    nxt = loop[(i + 1) % n]
    dx, dy = nxt[0] - loop[i][0], nxt[1] - loop[i][1]
    te_face = (
        abs(dx) < abs(dy)
        and dy > 0
        and math.hypot(dx, dy) <= 0.1 * chord
        and nxt[0] >= max(xs) - 0.02 * chord
    )
    start = (i + 1) % n if te_face else i
    return loop[start:] + loop[:start]
