"""The wire as a straight line between the two wire points (machine profile 3.3)."""

from __future__ import annotations

import math

import numpy as np

from .geom import Arr, segment_hits_rectangle

Axes = tuple[float, float, float, float]


def trace_point(a: Axes, span: float, y: float) -> tuple[float, float]:
    """Where the wire meets the plane y: P(y) = L + (y / span)(R - L), as (x, z)."""
    s = y / span
    return (a[0] + s * (a[2] - a[0]), a[1] + s * (a[3] - a[1]))


def trace(positions: Arr, span: float, y: float) -> Arr:
    """`trace_point` for every row (a1, a2, a3, a4) of `positions`."""
    s = y / span
    return np.column_stack(
        [positions[:, 0] + s * (positions[:, 2] - positions[:, 0]),
         positions[:, 1] + s * (positions[:, 3] - positions[:, 1])]
    )


def wire_angle(a: Axes, span: float) -> float:
    """Angle between the wire and the y axis, degrees (machine profile 6)."""
    return math.degrees(math.atan(math.hypot(a[2] - a[0], a[3] - a[1]) / span))


def wire_in_box(
    a: Axes, span: float, y0: float, y1: float, x0: float, x1: float, z0: float, z1: float
) -> bool:
    """True if the wire has a point inside the closed box x0..x1, y0..y1, z0..z1.

    Along y the wire is linear, so clip the y range to where its x and z are
    inside the box (slab method)."""
    lo, hi = y0, y1
    for start, end, b0, b1 in ((a[0], a[2], x0, x1), (a[1], a[3], z0, z1)):
        slope = (end - start) / span  # coordinate = start + slope * y
        if slope == 0:
            if not b0 <= start <= b1:
                return False
            continue
        ya, yb = (b0 - start) / slope, (b1 - start) / slope
        if ya > yb:
            ya, yb = yb, ya
        lo, hi = max(lo, ya), min(hi, yb)
        if lo > hi:
            return False
    return True


def sweep_hits_block(
    start: Axes,
    end: Axes,
    span: float,
    y0: float,
    y1: float,
    rect: tuple[float, float, float, float],
    planes: int = 201,
) -> float | None:
    """For a move that is linear in axis space, test `planes` evenly spaced
    planes from y0 to y1: return the first y where the wire's path in that
    plane enters the rectangle (x0, x1, z0, z1), or None. Between the tested
    planes nothing is checked."""
    s = np.array(start, dtype=np.float64)
    e = np.array(end, dtype=np.float64)
    for y in np.linspace(y0, y1, planes):
        p0 = trace(s[None, :], span, float(y))[0]
        p1 = trace(e[None, :], span, float(y))[0]
        if segment_hits_rectangle(p0, p1, *rect):
            return float(y)
    return None
