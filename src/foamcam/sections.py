"""Target geometry of a section (job-file.md §6) and its slits (§8)."""

import math
from dataclasses import dataclass

from . import geometry as g
from .geometry import Point
from .job import Block, Feature, Section


@dataclass(frozen=True)
class Outline:
    """A section after §6.1 to §6.3, in the untwisted section frame (mm).

    `contour` runs in Selig order with the slit points of §8 inserted.
    """

    section: Section
    contour: tuple[Point, ...]
    le_index: int
    slit_index: dict[str, int]
    features: dict[str, Feature]

    def place(self, p: Point) -> Point:
        """Twist (§6.4) and place (§6.5): section frame (u, v) -> work frame (x, z)."""
        s = self.section
        c_t = s.twist_axis * s.chord
        theta = math.radians(s.twist)
        du, v = p[0] - c_t, p[1]
        u_t = c_t + du * math.cos(theta) + v * math.sin(theta)
        v_t = -du * math.sin(theta) + v * math.cos(theta)
        return s.x + (u_t - c_t), s.z + v_t


def clean_contour(raw: list[Point], label: str, problems: list[str]) -> tuple[list[Point], int]:
    """§6.1: remove duplicates, find the leading edge, orient, normalise."""
    points: list[Point] = []
    for p in raw:
        if not points or g.dist(points[-1], p) >= 1e-9:
            points.append(p)
    if len(points) < 3:
        problems.append(f"{label}.shape: needs at least 3 distinct points")
        return [], 0

    def leading_edge(pts: list[Point]) -> int:
        return min(range(len(pts)), key=lambda i: (pts[i][0], i))

    i_le = leading_edge(points)
    if 0 < i_le < len(points) - 1:
        before = sum(p[1] for p in points[:i_le]) / i_le
        after = sum(p[1] for p in points[i_le + 1 :]) / (len(points) - i_le - 1)
        if before < after:
            points.reverse()
            i_le = leading_edge(points)
    if not 0 < i_le < len(points) - 1:
        problems.append(f"{label}.shape: the leading edge (smallest u) must not be the first or last point")
        return [], 0

    u0, v0 = points[i_le]
    moved = [(u - u0, v - v0) for u, v in points]
    extent = max(u for u, _ in moved) - min(u for u, _ in moved)
    return [(u / extent, v / extent) for u, v in moved], i_le


def thicken_trailing_edge(points: list[Point], i_le: int, t_min: float) -> list[Point]:
    """§6.2: open the trailing edge to at least t_min (chord fraction)."""
    gap = points[0][1] - points[-1][1]
    if gap >= t_min:
        return points
    d = t_min - gap
    result = []
    for i, (u, v) in enumerate(points):
        if i < i_le:
            v += d / 2 * u
        elif i > i_le:
            v -= d / 2 * u
        result.append((u, v))
    return result


def build_outline(section: Section, min_te_thickness: float, problems: list[str]) -> Outline | None:
    label = section.label
    points, i_le = clean_contour(list(section.shape), label, problems)
    if not points:
        return None
    points = thicken_trailing_edge(points, i_le, min_te_thickness / section.chord)
    contour = [(u * section.chord, v * section.chord) for u, v in points]

    ok = True
    if g.self_intersects(contour):
        problems.append(f"{label}.shape: the outer contour crosses itself")
        ok = False

    outer_edges = g.edges(contour)
    features = {f.name: f for f in section.features}
    feature_edges = {
        f.name: g.edges(list(f.points), closed=f.kind == "hole") for f in section.features
    }
    for i, f in enumerate(section.features):
        path = f"{label}.features[{i}]"
        if not all(g.inside(p, contour) for p in f.points) or g.any_crossing(feature_edges[f.name], outer_edges):
            problems.append(f'{path}: feature "{f.name}" must lie strictly inside the outer contour')
            ok = False
        for other in section.features[i + 1 :]:
            touching = g.any_crossing(feature_edges[f.name], feature_edges[other.name])
            if f.kind == "hole":
                touching |= any(g.inside(p, list(f.points)) for p in other.points)
            if other.kind == "hole":
                touching |= any(g.inside(p, list(other.points)) for p in f.points)
            if touching:
                problems.append(f'{path}: feature "{f.name}" touches feature "{other.name}"')
                ok = False
    if not ok:
        return None

    # §8 slits: from the first feature point along +v or -v to the outer contour.
    n = len(contour)
    hits: dict[int, list[tuple[float, str]]] = {}
    for i, f in enumerate(section.features):
        path = f"{label}.features[{i}]"
        direction = (0.0, 1.0) if f.slit == "upper" else (0.0, -1.0)
        hit = g.ray_hit(f.points[0], direction, contour)
        if hit is None:
            problems.append(f'{path}: the slit of "{f.name}" does not reach the outer contour')
            ok = False
            continue
        _, edge, s = hit
        upper_side = edge < i_le
        lower_side = i_le <= edge < n - 1
        if (f.slit == "upper" and not upper_side) or (f.slit == "lower" and not lower_side):
            problems.append(f'{path}: the slit of "{f.name}" meets the outer contour on the wrong side')
            ok = False
            continue
        hits.setdefault(edge, []).append((s, f.name))
    if not ok:
        return None

    new_contour: list[Point] = []
    slit_index: dict[str, int] = {}
    new_le = 0
    pending_end: list[str] = []
    for i in range(n):
        new_contour.append(contour[i])
        for name in pending_end:
            slit_index[name] = len(new_contour) - 1
        pending_end = []
        if i == i_le:
            new_le = len(new_contour) - 1
        a, b = contour[i], contour[(i + 1) % n]
        for s, name in sorted(hits.get(i, [])):
            if s <= 1e-9:
                slit_index[name] = len(new_contour) - 1
            elif s >= 1 - 1e-9:
                pending_end.append(name)
            else:
                new_contour.append((a[0] + s * (b[0] - a[0]), a[1] + s * (b[1] - a[1])))
                slit_index[name] = len(new_contour) - 1
    return Outline(section, tuple(new_contour), new_le, slit_index, features)


def check_in_block(outline: Outline, block: Block, problems: list[str]) -> None:
    """§6.7: every target point inside the block's x and z range."""
    worst = 0.0
    for p in outline.contour:
        x, z = outline.place(p)
        worst = max(worst, block.x - x, x - (block.x + block.length), -z, z - block.height)
    if worst > 1e-9:
        problems.append(f"{outline.section.label}: the section lies outside the block by up to {worst:.3f} mm")
