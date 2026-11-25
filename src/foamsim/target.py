"""Target geometry of the root and tip sections (job file format sections 6 to 8)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from .errors import InputError
from .geom import Arr, as_array, inside, segments, segments_intersect
from .job import Job, Section
from .profile import Profile

Point = tuple[float, float]


@dataclass
class SectionTarget:
    """Target shapes of one section, placed in its plane y = `y` (work frame x, z)."""

    name: str
    y: float
    outer: Arr  # closed polygon
    holes: dict[str, Arr] = field(default_factory=dict)  # closed polygons
    opens: dict[str, Arr] = field(default_factory=dict)  # polylines
    # [first point, channel points..., slit point]: the slit or channel as cut (section 8)
    slits: dict[str, Arr] = field(default_factory=dict)


def contour_uv(raw: Sequence[Point], chord: float, min_te_thickness: float) -> list[Point]:
    """Sections 6.1 to 6.3: the contour in mm in the section frame."""
    return _contour(raw, chord, min_te_thickness)[0]


def _contour(raw: Sequence[Point], chord: float, min_te: float) -> tuple[list[Point], int]:
    pts: list[Point] = []
    for p in raw:
        if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) >= 1e-9:
            pts.append((float(p[0]), float(p[1])))
    if len(pts) < 3:
        raise InputError("the contour has fewer than 3 distinct points")

    def leading_edge(points: list[Point]) -> int:
        u_min = min(u for u, _ in points)
        return next(i for i, (u, _) in enumerate(points) if u == u_min)

    i_le = leading_edge(pts)
    if i_le == 0 or i_le == len(pts) - 1:
        raise InputError("the leading edge (smallest u) is the first or last point")
    mean_before = sum(v for _, v in pts[:i_le]) / i_le
    mean_after = sum(v for _, v in pts[i_le + 1 :]) / (len(pts) - i_le - 1)
    if mean_before < mean_after:
        pts.reverse()
        i_le = leading_edge(pts)

    u0, v0 = pts[i_le]
    pts = [(u - u0, v - v0) for u, v in pts]
    extent = max(u for u, _ in pts) - min(u for u, _ in pts)
    pts = [(u / extent, v / extent) for u, v in pts]

    t_min = min_te / chord
    gap = pts[0][1] - pts[-1][1]
    if gap < t_min:
        d = t_min - gap
        pts = [
            (u, v + d / 2 * u) if i < i_le else (u, v - d / 2 * u) if i > i_le else (u, v)
            for i, (u, v) in enumerate(pts)
        ]
    return [(u * chord, v * chord) for u, v in pts], i_le


def place(
    points: Sequence[Point], chord: float, twist: float, twist_axis: float, x: float, z: float
) -> list[Point]:
    """Sections 6.4 and 6.5: twist about the twist axis, then place at (x, z)."""
    c_t = twist_axis * chord
    th = math.radians(twist)
    cos, sin = math.cos(th), math.sin(th)
    out = []
    for u, v in points:
        up = c_t + (u - c_t) * cos + v * sin
        vp = -(u - c_t) * sin + v * cos
        out.append((x + (up - c_t), z + vp))
    return out


def _place_section(points: Sequence[Point], s: Section) -> Arr:
    return as_array(place(points, s.chord, s.twist, s.twist_axis, s.x, s.z))


def _slit_point(
    contour: list[Point], i_le: int, first: Point, side: str
) -> tuple[Point, float] | None:
    """Section 8: the slit point and its position along the contour (edge index
    plus fraction), or None if the slit does not meet the matching side."""
    u0, v0 = first
    sign = 1.0 if side == "upper" else -1.0
    n = len(contour)
    best: tuple[float, int, float] | None = None  # (distance, edge, fraction)
    for j in range(n):
        (ua, va), (ub, vb) = contour[j], contour[(j + 1) % n]
        if ua == ub:
            continue
        s = (u0 - ua) / (ub - ua)
        if not 0.0 <= s <= 1.0:
            continue
        dist = (va + s * (vb - va) - v0) * sign
        if dist > 0 and (best is None or dist < best[0]):
            best = (dist, j, s)
    if best is None:
        return None
    dist, j, s = best
    pos = j + s
    on_upper = pos <= i_le
    on_lower = i_le <= pos <= n - 1
    if (side == "upper" and not on_upper) or (side == "lower" and not on_lower):
        return None
    return (u0, v0 + sign * dist), pos


def _channel_slit_point(
    contour: list[Point], i_le: int, first: Point, channel: Sequence[Point], side: str
) -> tuple[Point, float] | str:
    """Section 8 channels: the slit point and its position along the contour, or
    what is wrong with the channel."""
    poly = as_array(contour)
    if len(channel) > 1 and not bool(np.all(inside(as_array(channel[:-1]), poly))):
        return "leaves the outer contour before its last point"
    if bool(inside(as_array(channel[-1:]), poly)[0]):
        return "must end outside the outer contour"
    pts = [first, *channel]
    n = len(contour)
    for k in range(len(pts) - 2):  # every segment but the last stays inside
        for j in range(n):
            if segments_intersect(np.array(pts[k], dtype=float), np.array(pts[k + 1], dtype=float),
                                  poly[j], poly[(j + 1) % n]):
                return "crosses the outer contour before its last segment"
    (ua, va), (ub, vb) = pts[-2], pts[-1]
    best: tuple[float, int, float] | None = None  # (fraction along the last segment, edge, fraction)
    for j in range(n):
        (pa, qa), (pb, qb) = contour[j], contour[(j + 1) % n]
        du, dv, eu, ev = ub - ua, vb - va, pb - pa, qb - qa
        den = du * ev - dv * eu
        if den == 0:
            continue
        t = ((pa - ua) * ev - (qa - va) * eu) / den
        s = ((pa - ua) * dv - (qa - va) * du) / den
        if 0 <= t <= 1 and 0 <= s <= 1 and (best is None or t < best[0]):
            best = (t, j, s)
    if best is None:
        return "does not meet the outer contour"
    t, j, s = best
    pos = j + s
    if (side == "upper" and not pos <= i_le) or (side == "lower" and not i_le <= pos <= n - 1):
        return f"does not meet the {side} side"
    return (ua + t * (ub - ua), va + t * (vb - va)), pos


def _parent(channel: Sequence[Point], name: str, holes: dict[str, list[Point]]) -> str | None:
    """Section 8: the hole a channel ends in (its parent), or None."""
    end = as_array(channel[-1:])
    for other, pts in holes.items():
        if other != name and bool(inside(end, as_array(pts))[0]):
            return other
    return None


def _branch_point(
    contour: list[Point], first: Point, channel: Sequence[Point], parent: list[Point]
) -> Point | str:
    """Section 8 channels into another hole: the slit point on the parent's edge,
    or what is wrong with the channel."""
    poly, hole = as_array(contour), as_array(parent)
    if len(channel) > 1 and not bool(np.all(inside(as_array(channel[:-1]), poly))):
        return "leaves the outer contour"
    if len(channel) > 1 and bool(np.any(inside(as_array(channel[:-1]), hole))):
        return "enters its parent hole before its last segment"
    pts = [first, *channel]
    for k in range(len(pts) - 1):
        a, b = np.array(pts[k], dtype=float), np.array(pts[k + 1], dtype=float)
        for j in range(len(contour)):
            if segments_intersect(a, b, poly[j], poly[(j + 1) % len(contour)]):
                return "crosses the outer contour"
        if k < len(pts) - 2:
            for j in range(len(parent)):
                if segments_intersect(a, b, hole[j], hole[(j + 1) % len(parent)]):
                    return "enters its parent hole before its last segment"
    (ua, va), (ub, vb) = pts[-2], pts[-1]
    best: float | None = None
    m = len(parent)
    for j in range(m):
        (pa, qa), (pb, qb) = parent[j], parent[(j + 1) % m]
        du, dv, eu, ev = ub - ua, vb - va, pb - pa, qb - qa
        den = du * ev - dv * eu
        if den == 0:
            continue
        t = ((pa - ua) * ev - (qa - va) * eu) / den
        s = ((pa - ua) * dv - (qa - va) * du) / den
        if 0 <= t <= 1 and 0 <= s <= 1 and (best is None or t < best):
            best = t
    if best is None:
        return "does not meet its parent hole"
    return ua + best * (ub - ua), va + best * (vb - va)


def _self_crossing(polygon: Arr) -> bool:
    a, b = segments(polygon, closed=True)
    keep = [i for i in range(len(a)) if not np.array_equal(a[i], b[i])]
    m = len(keep)
    for ii in range(m):
        for jj in range(ii + 1, m):
            if jj == ii + 1 or (ii == 0 and jj == m - 1):
                continue
            i, j = keep[ii], keep[jj]
            if segments_intersect(a[i], b[i], a[j], b[j]):
                return True
    return False


def _crosses(p: Arr, closed_p: bool, q: Arr, closed_q: bool) -> bool:
    pa, pb = segments(p, closed_p)
    qa, qb = segments(q, closed_q)
    return any(
        segments_intersect(pa[i], pb[i], qa[j], qb[j])
        for i in range(len(pa))
        for j in range(len(qa))
    )


def build_targets(job: Job, profile: Profile) -> dict[str, SectionTarget]:
    """Target shapes of both sections, keyed "root" and "tip". With
    `job.mirror` the planes are reflected in y = wire_span / 2 (job section 12:
    the mirrored program cuts the reflected panel)."""
    errors: list[str] = []
    if job.kerf_table not in profile.kerf_tables:
        errors.append(f'kerf_table: "{job.kerf_table}" is not a table of the machine profile')
    span = profile.wire_span
    targets: dict[str, SectionTarget] = {}
    order: dict[str, list[str]] = {}
    all_parents: dict[str, dict[str, str]] = {}
    for s in job.sections:
        if not 0 < s.y < span:
            errors.append(f"{s.label}.position.y: must lie between 0 and wire_span {span:g}")
        try:
            contour, i_le = _contour(s.raw, s.chord, job.min_te_thickness)
        except InputError as exc:
            errors.extend(f"{s.label}.shape: {m}" for m in exc.messages)
            continue
        outer = _place_section(contour, s)
        target = SectionTarget(s.label, span - s.y if job.mirror else s.y, outer)
        if _self_crossing(outer):
            errors.append(f"{s.label}: the target outer contour crosses itself")

        b = job.block
        x, z = outer[:, 0], outer[:, 1]
        outside = max(
            float(np.max(b.x - x)), float(np.max(x - (b.x + b.length))),
            float(np.max(b.z - z)), float(np.max(z - (b.z + b.height))), 0.0,
        )
        if outside > 0:
            errors.append(
                f"{s.label}: the target contour lies outside the block by up to {outside:.3f} mm"
            )

        positions: list[tuple[float, str]] = []
        raw_holes = {f.name: list(f.points) for f in s.features if f.kind == "hole"}
        parents: dict[str, str] = {}
        for i, f in enumerate(s.features):
            p = f"{s.label}.features[{i}]"
            placed = _place_section(f.points, s)
            closed = f.kind == "hole"
            if not bool(np.all(inside(placed, outer))) or _crosses(placed, closed, outer, True):
                errors.append(f"{p}: must lie strictly inside the target outer contour")
            for other_name, other in [*target.holes.items(), *target.opens.items()]:
                if _crosses(placed, closed, other, other_name in target.holes) or (
                    closed and bool(np.any(inside(other, placed)))
                ) or (other_name in target.holes and bool(np.any(inside(placed, other)))):
                    errors.append(f'{p}: touches the feature "{other_name}"')
            parent = _parent(f.channel, f.name, raw_holes) if f.channel else None
            if parent is not None:
                branch = _branch_point(contour, f.points[0], f.channel, raw_holes[parent])
                if isinstance(branch, str):
                    errors.append(f"{p}.channel: the channel {branch}")
                    continue
                parents[f.name] = parent
                (target.holes if closed else target.opens)[f.name] = placed
                target.slits[f.name] = _place_section([f.points[0], *f.channel[:-1], branch], s)
                continue
            if f.channel:
                found = _channel_slit_point(contour, i_le, f.points[0], f.channel, f.slit)
                if isinstance(found, str):
                    errors.append(f"{p}.channel: the channel {found}")
                    continue
                hit: tuple[Point, float] | None = found
            else:
                hit = _slit_point(contour, i_le, f.points[0], f.slit)
            if hit is None:
                errors.append(
                    f"{p}.slit: the slit from the first point does not meet the {f.slit} side"
                )
                continue
            slit_point, pos = hit
            positions.append((pos if job.direction == "upper_first" else -pos, f.name))
            (target.holes if closed else target.opens)[f.name] = placed
            target.slits[f.name] = _place_section(
                [f.points[0], *f.channel[:-1], slit_point], s
            )
        for name, slit in target.slits.items():  # a channel must not cut into another feature
            for other_name, other in [*target.holes.items(), *target.opens.items()]:
                if other_name in (name, parents.get(name)):
                    continue
                if _crosses(slit, False, other, other_name in target.holes):
                    errors.append(f'{s.label}: the slit of "{name}" crosses the feature "{other_name}"')
        for name in parents:  # following the parents must reach the outer contour
            seen = [name]
            while seen[-1] in parents and parents[seen[-1]] not in seen:
                seen.append(parents[seen[-1]])
            if seen[-1] in parents:
                errors.append(f'{s.label}: the channel of "{name}" never leads to the outer contour')
                break
        all_parents[s.label] = parents
        order[s.label] = [name for _, name in sorted(positions)]
        targets[s.label] = target

    if "root" in order and "tip" in order and sorted(order["root"]) == sorted(order["tip"]) \
            and order["root"] != order["tip"]:
        errors.append(
            "features: the slit points of root and tip are not in the same order along "
            "the cutting direction"
        )
    if "root" in all_parents and "tip" in all_parents and all_parents["root"] != all_parents["tip"]:
        errors.append("features: the channels end in different holes at the root and the tip")
    if errors:
        raise InputError(errors)
    return targets
