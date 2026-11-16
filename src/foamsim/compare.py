"""Kerf removal and comparison with the target in one section plane
(Brief 5 task 4).

Model. In a plane the wire centre moves along the trace, a polyline with one
edge per feed move. The wire melts a slot of full width k around its path, k
being the kerf-table value at the wire's speed in that plane during that move.
The cut surface left by one edge is therefore the edge moved sideways by k/2
toward the material. The simulated surface of a target element (outer contour
or hole) is the chain of those moved edges, each joined to the next at the
intersection of their lines: that is the surface the material keeps where
the wire turns toward the material, and at the other corners it is the sharp
corner the wire cannot actually cut (see `inside_corner_rounding`). An edge
whose whole moved edge lies inside the slot of the edge before or after it
leaves no surface (a move shorter than the half kerf of a wider-kerf move it
meets at a corner) and is left out of the chain before joining.

Which element an edge cuts, and which side the material is on, is decided
from the target alone:

- candidates are the outer contour, each hole, each open cut, and each slit
  (job file section 8; slits are cut but not part of the target, so they are
  not compared);
- for the outer contour the material is inside the target polygon, for a
  hole outside it; the edge is moved by k/2 to the side where the material
  is;
- the edge goes to the candidate it lies closest to (largest distance of five
  points along the moved edge, or along the edge itself for open cuts and
  slits; for a slit also the other way round, segment by segment, since one
  move cuts a whole slit segment; a channel has one segment per move).

The first and last feed moves are the lead-in and exit (job file section 13)
and are not part of any element. Channel exits (job file 10.4: rapids between
the lead-in and the exit, and feed moves the caller marks as exits) run back
along a slit already cut: they are only checked to lie on a slit.

Deviation of an element = symmetric Hausdorff distance between the simulated
surface and the target, computed on points spaced at most `step` apart on
both, so it is exact to within `step / 2`. A target with no simulated edge at
all has deviation infinity.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np

from .errors import InputError
from .geom import (
    Arr,
    point_segment_distances,
    polyline_distance,
    sample,
    segments,
    signed_distance,
)
from .profile import KerfTable, kerf_lookup
from .target import SectionTarget

JOIN_TOL = 1e-6  # mm: two trace points closer than this are the same point
STEP = 0.01  # mm: sampling step of the Hausdorff distance
_FRACTIONS = np.array([0.0, 0.25, 0.5, 0.75, 1.0])


@dataclass
class Edge:
    move: int  # index of the feed move
    line: int
    p0: Arr
    p1: Arr
    kerf: float
    side: float = 0.0  # +1: material to the left of p0 -> p1, -1: right, 0: no offset

    @property
    def normal(self) -> Arr:
        d = self.p1 - self.p0
        result: Arr = np.array([-d[1], d[0]]) / math.hypot(d[0], d[1])
        return result

    def moved(self) -> tuple[Arr, Arr]:
        shift = self.side * self.kerf / 2 * self.normal
        return self.p0 + shift, self.p1 + shift


@dataclass
class ElementResult:
    key: str
    max_deviation: float
    passed: bool
    worst: tuple[float, float] | None
    worst_kind: str
    inside_corner_rounding: float
    edges: int
    surface: list[Arr] = field(default_factory=list)  # simulated surface chains
    profile: Arr = field(default_factory=lambda: np.empty((0, 2)))  # (arc length, deviation)


@dataclass
class SectionResult:
    name: str
    plane_y: float
    trace: Arr
    passed: bool = False
    max_deviation: float = math.nan
    elements: dict[str, ElementResult] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class _Candidate:
    key: str
    shape: Arr
    closed: bool
    material: float  # +1 inside the polygon, -1 outside, 0 no offset
    compared: bool


def _candidates(target: SectionTarget) -> list[_Candidate]:
    out = [_Candidate("outer", target.outer, True, 1.0, True)]
    out += [_Candidate(f"hole {n}", s, True, -1.0, True) for n, s in target.holes.items()]
    out += [_Candidate(f"open {n}", s, False, 0.0, True) for n, s in target.opens.items()]
    out += [_Candidate(f"slit {n}", s, False, 0.0, False) for n, s in target.slits.items()]
    return out


def _assign(edge: Edge, candidates: list[_Candidate]) -> tuple[_Candidate, float, float]:
    """Best candidate for `edge`, the side to move it to, and its distance."""
    pts = edge.p0 + _FRACTIONS[:, None] * (edge.p1 - edge.p0)
    n = edge.normal
    h = edge.kerf / 2
    best: tuple[_Candidate, float, float] | None = None
    for c in candidates:
        if c.material == 0:
            side = 0.0
        else:
            mid = (edge.p0 + edge.p1) / 2
            sd = signed_distance(np.vstack([mid + h * n, mid - h * n]), c.shape)
            # Material inside (outer): the side further inside. Outside (hole):
            # the side further outside.
            side = 1.0 if c.material * (sd[1] - sd[0]) > 0 else -1.0
        if c.compared:
            dist = float(np.max(polyline_distance(pts + side * h * n, c.shape, c.closed)))
        else:
            dist = _slit_distance(edge, pts, c.shape)
        if best is None or dist < best[2]:
            best = (c, side, dist)
    assert best is not None
    return best


def _slit_distance(edge: Edge, pts: Arr, slit: Arr) -> float:
    """Distance from an edge to the nearest segment of a slit, both ways: one
    move cuts a whole slit segment, so a feature edge lying on the slit line is
    not taken for the slit."""
    seg = np.vstack([edge.p0, edge.p1])
    best = math.inf
    for j in range(len(slit) - 1):
        piece = slit[j : j + 2]
        one = float(np.max(polyline_distance(pts, piece, closed=False)))
        ends = piece[0] + _FRACTIONS[:, None] * (piece[1] - piece[0])
        back = float(np.max(polyline_distance(ends, seg, closed=False)))
        best = min(best, max(one, back))
    return best


def _joint(a: Edge, b: Edge) -> tuple[list[Arr], float]:
    """Surface points where moved edge `a` meets moved edge `b`, and the
    inside-corner rounding at that corner (0 where the wire turns toward the
    material)."""
    a0, a1 = a.moved()
    b0, b1 = b.moved()
    da, db = a1 - a0, b1 - b0
    den = float(da[0] * db[1] - da[1] * db[0])
    if abs(den) <= 1e-12 * float(np.hypot(*da) * np.hypot(*db)):
        return [(a1 + b0) / 2], 0.0
    w = b0 - a0
    t = float(w[0] * db[1] - w[1] * db[0]) / den
    u = float(w[0] * da[1] - w[1] * da[0]) / den
    point = a0 + t * da
    reach = float(np.hypot(*(point - a.p1)))
    if 0 <= t <= 1 and 0 <= u <= 1:
        # The moved edges cross: the material keeps the corner where they meet.
        return [point], 0.0
    if t > 1 and u < 0 and reach <= 10 * max(a.kerf, b.kerf):
        # The moved edges stop short of each other: the material corner is
        # beyond the wire's reach. The real surface is an arc of radius k/2
        # about the wire vertex; report how far the sharp corner lies from it.
        return [point], max(0.0, reach - (a.kerf + b.kerf) / 4)
    # The lines meet away from both edges (nearly parallel edges with
    # different kerfs): the surface steps from one moved edge to the other
    # around the end of the wider slot. The step is drawn straight.
    return [a1, b0], 0.0


def _chains(edges: list[Edge]) -> list[list[Edge]]:
    chains: list[list[Edge]] = []
    for e in edges:
        if chains and np.hypot(*(chains[-1][-1].p1 - e.p0)) < JOIN_TOL:
            chains[-1].append(e)
        else:
            chains.append([e])
    if len(chains) > 1 and np.hypot(*(chains[-1][-1].p1 - chains[0][0].p0)) < JOIN_TOL:
        chains[0] = chains.pop() + chains[0]
    return chains


def _in_slot(edge: Edge, other: Edge) -> bool:
    """True if the whole moved `edge` lies inside the slot the wire melts along
    `other` (all points within other.kerf / 2 of its path). The slot is convex,
    so testing the two ends is enough."""
    ends = np.vstack(edge.moved())
    d, _ = point_segment_distances(ends, other.p0[None, :], other.p1[None, :])
    return bool(np.all(d < other.kerf / 2 - JOIN_TOL))


def _trim(chain: list[Edge], closed: bool) -> list[Edge]:
    """Drop the edges whose surface a neighbouring edge's slot melts away
    entirely: a move shorter than the half kerf of a wider-kerf move it meets
    at a corner leaves no surface of its own. Repeated until nothing changes,
    since each removal gives the edges either side new neighbours."""
    edges = list(chain)
    changed = True
    while changed and len(edges) > 2:
        changed = False
        n = len(edges)
        for i, e in enumerate(edges):
            near = []
            if closed or i > 0:
                near.append(edges[i - 1])
            if closed or i < n - 1:
                near.append(edges[(i + 1) % n])
            if any(_in_slot(e, o) for o in near):
                del edges[i]
                changed = True
                break
    return edges


def _surface(edges: list[Edge]) -> tuple[list[tuple[Arr, bool]], float]:
    """Simulated surface as polylines (points, closed) and the largest
    inside-corner rounding."""
    out: list[tuple[Arr, bool]] = []
    rounding = 0.0
    for chain in _chains(edges):
        closed = np.hypot(*(chain[-1].p1 - chain[0].p0)) < JOIN_TOL
        chain = _trim(chain, bool(closed))
        points: list[Arr] = []
        pairs = list(itertools.pairwise(chain))
        if closed:
            pairs = [(chain[-1], chain[0]), *pairs]
        else:
            points.append(chain[0].moved()[0])
        for a, b in pairs:
            joint, r = _joint(a, b)
            points += joint
            rounding = max(rounding, r)
        if not closed:
            points.append(chain[-1].moved()[1])
        out.append((np.vstack(points), bool(closed)))
    return out, rounding


def _hausdorff(
    surface: list[tuple[Arr, bool]], shape: Arr, closed: bool
) -> tuple[float, tuple[float, float] | None, str, Arr]:
    """Symmetric Hausdorff distance between `surface` and the target `shape`.
    Also the worst point, which direction it came from, and the deviation
    profile along the target as (arc length, distance)."""
    target_pts = sample(shape, closed, STEP)
    arc = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(target_pts, axis=0).T))])
    if not surface:
        return math.inf, None, "target not cut", np.column_stack([arc, np.full(len(arc), np.inf)])
    starts, ends = zip(*(segments(p, c) for p, c in surface), strict=True)
    a, b = np.vstack(starts), np.vstack(ends)
    d_target, _ = point_segment_distances(target_pts, a, b)
    sim_pts = np.vstack([sample(p, c, STEP) for p, c in surface])
    d_sim = polyline_distance(sim_pts, shape, closed)
    i, j = int(np.argmax(d_target)), int(np.argmax(d_sim))
    keep = max(1, len(arc) // 2000)
    profile = np.column_stack([arc[::keep], d_target[::keep]])
    if d_target[i] >= d_sim[j]:
        p = target_pts[i]
        return float(d_target[i]), (float(p[0]), float(p[1])), "target not reached", profile
    p = sim_pts[j]
    return float(d_sim[j]), (float(p[0]), float(p[1])), "surface off target", profile


def compare_section(
    target: SectionTarget,
    trace: Arr,
    lines: list[int],
    durations: list[float],
    table: KerfTable,
    tolerance: float,
    exits: list[bool] | None = None,
) -> SectionResult:
    """Compare the trace of all moves of the cut in the plane of `target` with it.

    `trace[i]` and `trace[i + 1]` are the wire's start and end points of move
    i in this plane; `lines` and `durations` (minutes) are per move. Moves
    marked in `exits` (channel exits, rapids among them) only have to lie on a
    slit."""
    result = SectionResult(target.name, target.y, trace)
    candidates = _candidates(target)
    slits = [c for c in candidates if not c.compared]
    per_key: dict[str, list[Edge]] = {c.key: [] for c in candidates}

    for i in range(1, len(durations) - 1):
        p0, p1 = trace[i], trace[i + 1]
        length = float(np.hypot(*(p1 - p0)))
        if length < 1e-9:
            continue
        if exits is not None and exits[i]:
            exit_edge = Edge(i, lines[i], p0, p1, 0.0)
            dist = min((_assign(exit_edge, [c])[2] for c in slits), default=math.inf)
            if dist > 1.5 * table.max_kerf:
                result.problems.append(
                    f"line {lines[i]}: channel exit {dist:.3f} mm away from the nearest slit"
                )
            continue
        try:
            kerf = kerf_lookup(table, length / durations[i])
        except InputError as exc:
            result.error = f"line {lines[i]}: {exc}"
            return result
        edge = Edge(i, lines[i], p0, p1, kerf)
        cand, side, dist = _assign(edge, candidates)
        edge.side = side
        per_key[cand.key].append(edge)
        if not cand.compared and dist > 1.5 * kerf:
            result.problems.append(
                f"line {lines[i]}: cut {dist:.3f} mm away from the nearest slit, outside every "
                "target shape"
            )

    for c in candidates:
        if not c.compared:
            continue
        edges = per_key[c.key]
        if c.material == 0:
            surface = [(np.vstack([e.p0, e.p1]), False) for e in edges]
            rounding = 0.0
        else:
            surface, rounding = _surface(edges)
        dev, worst, kind, profile = _hausdorff(surface, c.shape, c.closed)
        result.elements[c.key] = ElementResult(
            c.key, dev, dev <= tolerance, worst, kind, rounding, len(edges),
            [p if not closed else np.vstack([p, p[:1]]) for p, closed in surface], profile,
        )
    result.max_deviation = max(e.max_deviation for e in result.elements.values())
    result.passed = not result.problems and all(e.passed for e in result.elements.values())
    return result
