"""Plane geometry on (x, z) points stored as numpy arrays of shape (n, 2)."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

Arr = NDArray[np.float64]

_CHUNK = 4096


def as_array(points: object) -> Arr:
    return np.asarray(points, dtype=np.float64).reshape(-1, 2)


def segments(points: Arr, closed: bool) -> tuple[Arr, Arr]:
    """Start and end points of the edges of a polyline (closed: last to first too)."""
    if closed:
        return points, np.roll(points, -1, axis=0)
    return points[:-1], points[1:]


def point_segment_distances(p: Arr, a: Arr, b: Arr) -> tuple[Arr, NDArray[np.intp]]:
    """For each point of `p`, the distance to the nearest of the segments a[i]-b[i]
    and the index of that segment."""
    if len(a) == 0:
        return np.full(len(p), np.inf), np.zeros(len(p), dtype=np.intp)
    d = b - a
    length2 = np.einsum("ij,ij->i", d, d)
    safe = np.where(length2 > 0, length2, 1.0)
    out = np.empty(len(p))
    idx = np.empty(len(p), dtype=np.intp)
    for start in range(0, len(p), _CHUNK):
        q = p[start : start + _CHUNK]
        ap = q[:, None, :] - a[None, :, :]
        t = np.clip(np.einsum("nmk,mk->nm", ap, d) / safe, 0.0, 1.0)
        diff = ap - t[:, :, None] * d[None, :, :]
        dist2 = np.einsum("nmk,nmk->nm", diff, diff)
        best = np.argmin(dist2, axis=1)
        idx[start : start + _CHUNK] = best
        out[start : start + _CHUNK] = np.sqrt(dist2[np.arange(len(q)), best])
    return out, idx


def polyline_distance(p: Arr, points: Arr, closed: bool) -> Arr:
    a, b = segments(points, closed)
    return point_segment_distances(p, a, b)[0]


def inside(p: Arr, polygon: Arr) -> NDArray[np.bool_]:
    """Even-odd point-in-polygon test for each point of `p`."""
    x, z = p[:, 0][:, None], p[:, 1][:, None]
    a, b = segments(polygon, closed=True)
    ax, az, bx, bz = a[:, 0], a[:, 1], b[:, 0], b[:, 1]
    crosses = (az > z) != (bz > z)
    with np.errstate(divide="ignore", invalid="ignore"):
        xint = ax + (z - az) * (bx - ax) / (bz - az)
    hits = crosses & (x < xint)
    result: NDArray[np.bool_] = (np.count_nonzero(hits, axis=1) % 2) == 1
    return result


def signed_distance(p: Arr, polygon: Arr) -> Arr:
    """Distance to the polygon boundary, negative inside."""
    d = polyline_distance(p, polygon, closed=True)
    return np.where(inside(p, polygon), -d, d)


def sample(points: Arr, closed: bool, step: float) -> Arr:
    """All vertices plus evenly spaced points so that no gap exceeds `step`."""
    a, b = segments(points, closed)
    if len(a) == 0:
        return points.copy()
    parts = []
    for p0, p1 in zip(a, b, strict=True):
        n = max(1, math.ceil(float(np.hypot(*(p1 - p0))) / step))
        t = np.arange(n)[:, None] / n
        parts.append(p0 + t * (p1 - p0))
    if not closed:
        parts.append(points[-1:])
    return np.vstack(parts)


def sample_segments(a: Arr, b: Arr, step: float) -> Arr:
    parts = []
    for p0, p1 in zip(a, b, strict=True):
        n = max(1, math.ceil(float(np.hypot(*(p1 - p0))) / step))
        t = np.arange(n + 1)[:, None] / n
        parts.append(p0 + t * (p1 - p0))
    return np.vstack(parts) if parts else np.empty((0, 2))


def cross(u: Arr, v: Arr) -> float:
    return float(u[0] * v[1] - u[1] * v[0])


def segments_intersect(p1: Arr, p2: Arr, q1: Arr, q2: Arr) -> bool:
    """True if the closed segments p1-p2 and q1-q2 share a point."""
    d1, d2 = p2 - p1, q2 - q1
    o1 = cross(d1, q1 - p1)
    o2 = cross(d1, q2 - p1)
    o3 = cross(d2, p1 - q1)
    o4 = cross(d2, p2 - q1)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True

    def on(a: Arr, b: Arr, c: Arr, o: float) -> bool:
        return o == 0 and min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) \
            and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])

    return on(p1, p2, q1, o1) or on(p1, p2, q2, o2) or on(q1, q2, p1, o3) or on(q1, q2, p2, o4)


def segment_hits_rectangle(
    p0: Arr, p1: Arr, x0: float, x1: float, z0: float, z1: float
) -> bool:
    """True if the segment p0-p1 has a point inside the closed rectangle."""
    t0, t1 = 0.0, 1.0
    d = p1 - p0
    for lo, hi, start, delta in ((x0, x1, p0[0], d[0]), (z0, z1, p0[1], d[1])):
        if delta == 0:
            if start < lo or start > hi:
                return False
            continue
        ta, tb = (lo - start) / delta, (hi - start) / delta
        if ta > tb:
            ta, tb = tb, ta
        t0, t1 = max(t0, ta), min(t1, tb)
        if t0 > t1:
            return False
    return True
