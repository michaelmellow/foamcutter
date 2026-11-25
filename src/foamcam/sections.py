"""Target geometry of a section (job-file.md §6) and its slits (§8)."""

import itertools
import math
from dataclasses import dataclass, field, replace

from . import geometry as g
from .geometry import Point
from .job import Block, Feature, Section


@dataclass(frozen=True)
class Outline:
    """A section after §6.1 to §6.3, in the untwisted section frame (mm).

    `contour` runs in Selig order with the slit points of §8 inserted.
    `branch_index` maps a feature whose channel ends in another hole to that
    parent's name and the index of the slit point inserted into its points.
    """

    section: Section
    contour: tuple[Point, ...]
    le_index: int
    slit_index: dict[str, int]
    features: dict[str, Feature]
    branch_index: dict[str, tuple[str, int]] = field(default_factory=dict)

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

    # §8 slits: from the first feature point along +v or -v (or along the
    # channel) to the outer contour.
    n = len(contour)
    hits: dict[int, list[tuple[float, str]]] = {}
    branch_hits: dict[str, list[tuple[int, float, str]]] = {}  # parent -> (edge, fraction, child)
    parents: dict[str, str] = {}
    for i, f in enumerate(section.features):
        path = f"{label}.features[{i}]"
        if f.channel:
            why = channel_problem(f, contour, outer_edges, section.features, feature_edges)
            if why:
                problems.append(f'{path}.channel: the channel of "{f.name}" {why}')
                ok = False
                continue
            start = f.channel[-2] if len(f.channel) > 1 else f.points[0]
            end = f.channel[-1]
            length = g.dist(start, end)
            direction = ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
            parent = channel_parent(f, section.features)
            if parent is not None:
                branch = g.ray_hit(start, direction, list(parent.points))
                if branch is None:  # cannot happen: the last point lies inside the parent
                    problems.append(f'{path}.channel: the channel of "{f.name}" does not reach "{parent.name}"')
                    ok = False
                    continue
                parents[f.name] = parent.name
                branch_hits.setdefault(parent.name, []).append((branch[1], branch[2], f.name))
                continue
            hit = g.ray_hit(start, direction, contour)
        else:
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
    for name in parents:  # §8: following the parents must reach the outer contour
        seen = [name]
        while seen[-1] in parents:
            nxt = parents[seen[-1]]
            if nxt in seen:
                loop = seen[seen.index(nxt):]
                problems.append(
                    f"{label}.features: the channels of " + ", ".join(f'"{x}"' for x in loop)
                    + " only lead into each other and never reach the outer contour"
                )
                ok = False
                break
            seen.append(nxt)
        if not ok:
            break
    if not ok:
        return None

    branch_index: dict[str, tuple[str, int]] = {}
    for parent_name, found in branch_hits.items():
        parent = features[parent_name]
        points, index = _insert_points(list(parent.points), found)
        features[parent_name] = replace(parent, points=tuple(points))
        branch_index.update({child: (parent_name, k) for child, k in index.items()})

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
    return Outline(section, tuple(new_contour), new_le, slit_index, features, branch_index)


def _insert_points(points: list[Point], found: list[tuple[int, float, str]]) -> tuple[list[Point], dict[str, int]]:
    """A closed polygon with the slit points (edge, fraction, name) inserted, and their indices."""
    n = len(points)
    by_edge: dict[int, list[tuple[float, str]]] = {}
    for edge, s, name in found:
        if s >= 1 - 1e-9:  # at the end of the edge: the next edge's first vertex
            edge, s = (edge + 1) % n, 0.0
        by_edge.setdefault(edge, []).append((s, name))
    out: list[Point] = []
    index: dict[str, int] = {}
    for i in range(n):
        out.append(points[i])
        a, b = points[i], points[(i + 1) % n]
        for s, name in sorted(by_edge.get(i, [])):
            if s <= 1e-9:
                index[name] = len(out) - 1
            else:
                out.append((a[0] + s * (b[0] - a[0]), a[1] + s * (b[1] - a[1])))
                index[name] = len(out) - 1
    return out, index


def channel_parent(f: Feature, features: tuple[Feature, ...]) -> Feature | None:
    """§8: the hole the channel of `f` ends in, or None if it ends outside the outer contour."""
    if not f.channel:
        return None
    for other in features:
        if other.name != f.name and other.kind == "hole" and g.inside(f.channel[-1], list(other.points)):
            return other
    return None


def channel_problem(
    f: Feature,
    contour: list[Point],
    outer_edges: list[tuple[Point, Point]],
    features: tuple[Feature, ...],
    feature_edges: dict[str, list[tuple[Point, Point]]],
) -> str:
    """§8 Channels: what is wrong with the channel of `f`, or "" if nothing. The
    channel ends outside the outer contour, or inside another hole (its parent)."""
    pts = [f.points[0], *f.channel]
    if any(g.dist(a, b) < 1e-9 for a, b in itertools.pairwise(pts)):
        return "has two equal points in a row"
    if not all(g.inside(p, contour) for p in f.channel[:-1]):
        return "leaves the outer contour before its last point"
    parent = channel_parent(f, features)
    path_edges = g.edges(pts, closed=False)
    if parent is None:
        if g.inside(f.channel[-1], contour):
            return "must end outside the outer contour or inside another hole (its last point is in the foam)"
        if g.any_crossing(path_edges[:-1], outer_edges):
            return "crosses the outer contour before its last segment"
    else:
        if g.any_crossing(path_edges, outer_edges):
            return "crosses the outer contour"
        if any(g.inside(p, list(parent.points)) for p in f.channel[:-1]) or g.any_crossing(
            path_edges[:-1], feature_edges[parent.name]
        ):
            return f'enters hole "{parent.name}" before its last segment'
    for a in range(len(path_edges)):
        for b in range(a + 2, len(path_edges)):
            if g.segments_cross(*path_edges[a], *path_edges[b]):
                return "crosses itself"
    if g.any_crossing(path_edges, feature_edges[f.name]) or (
        f.kind == "hole" and any(g.inside(p, list(f.points)) for p in f.channel)
    ):
        return "runs into its own feature"
    for other in features:
        if other.name == f.name or (parent is not None and other.name == parent.name):
            continue
        if g.any_crossing(path_edges, feature_edges[other.name]) or (
            other.kind == "hole" and any(g.inside(p, list(other.points)) for p in f.channel)
        ):
            return f'crosses feature "{other.name}"'
    return ""


def check_in_block(outline: Outline, block: Block, z0: float, problems: list[str]) -> None:
    """§6.7: every target point inside the block's x and z range (§5), bottom face at `z0`."""
    worst = 0.0
    for p in outline.contour:
        x, z = outline.place(p)
        worst = max(worst, block.x - x, x - (block.x + block.length), z0 - z, z - (z0 + block.height))
    if worst > 1e-9:
        problems.append(f"{outline.section.label}: the section lies outside the block by up to {worst:.3f} mm")
