"""Synchronisation keeps every target vertex (job-file.md §10.3) and kerf matches the
actual wire speed (§10.7).

Both tests check the G-code of the three worked examples. They build the target
geometry of §6 themselves and read speeds from the G-code (§13), so they do not
use the CAM's geometry, synchronisation or timing code. Only the job loader is
shared, to read the section shapes.
"""

import math
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import yaml

from foamcam import generate, load_job, load_profile
from foamcam.job import Job, Section

ROOT = Path(__file__).resolve().parents[2]
JOBS = sorted((ROOT / "examples" / "jobs").glob("*.yaml"))
SAMPLE_PROFILE = ROOT / "examples" / "profiles" / "sample-machine.yaml"

Point = tuple[float, float]
Axes = tuple[float, float, float, float]

# Positions are written with 4 decimals (§13). A face segment shorter than this
# has a length, and so a speed, too uncertain to check a kerf against.
MIN_CHECKED_LENGTH = 0.05


# --- target geometry, written from job-file.md §6 and §8 ------------------------


def section_contour(section: Section, min_te_thickness: float) -> tuple[list[Point], int]:
    """§6.1 to §6.3: the outer contour in the untwisted section frame (mm), and its leading-edge index."""
    points: list[Point] = []
    for p in section.shape:
        if not points or math.dist(points[-1], p) >= 1e-9:
            points.append(p)

    def leading_edge() -> int:
        return min(range(len(points)), key=lambda i: (points[i][0], i))

    i_le = leading_edge()
    before = sum(v for _, v in points[:i_le]) / i_le
    after = sum(v for _, v in points[i_le + 1 :]) / (len(points) - i_le - 1)
    if before < after:
        points.reverse()
        i_le = leading_edge()
    u0, v0 = points[i_le]
    points = [(u - u0, v - v0) for u, v in points]
    extent = max(u for u, _ in points) - min(u for u, _ in points)
    points = [(u / extent, v / extent) for u, v in points]

    t_min = min_te_thickness / section.chord
    gap = points[0][1] - points[-1][1]
    if gap < t_min:
        d = t_min - gap
        points = [
            (u, v + d / 2 * u if i < i_le else v - d / 2 * u if i > i_le else v) for i, (u, v) in enumerate(points)
        ]
    return [(u * section.chord, v * section.chord) for u, v in points], i_le


def target_contour(section: Section, min_te_thickness: float) -> list[Point]:
    """§6.1 to §6.5: the placed vertices of the target outer contour."""
    return [place(section, p) for p in section_contour(section, min_te_thickness)[0]]


def slit_point(contour: list[Point], start: Point, up: bool) -> Point:
    """§8: first point where the ray from `start` along +v (or -v) meets the closed contour."""
    best: tuple[float, Point] | None = None
    for a, b in polygon_edges(contour):
        if (a[0] - start[0]) * (b[0] - start[0]) > 0 or a[0] == b[0]:
            continue
        v = a[1] + (start[0] - a[0]) * (b[1] - a[1]) / (b[0] - a[0])
        t = v - start[1] if up else start[1] - v
        if t > 0 and (best is None or t < best[0]):
            best = (t, (start[0], v))
    assert best is not None
    return best[1]


def turn_angle(polygon: list[Point], point: Point) -> float:
    """Degrees the closed polygon turns at `point`: 0 inside an edge."""
    n = len(polygon)
    for v in range(n):
        if math.dist(polygon[v], point) < 1e-9:
            a, b, c = polygon[v - 1], polygon[v], polygon[(v + 1) % n]
            d1, d2 = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
            return abs(math.degrees(math.atan2(d1[0] * d2[1] - d1[1] * d2[0], d1[0] * d2[0] + d1[1] * d2[1])))
    return 0.0


def in_line_joins(job: Job, section: Section) -> list[Point]:
    """Placed fixed points of the outer contour (§10.2) where it turns by less than 20 degrees (§10.7)."""
    contour, i_le = section_contour(section, job.min_te_thickness)
    fixed = [contour[0], contour[i_le], contour[-1]]
    fixed += [slit_point(contour, f.points[0], f.slit == "upper") for f in section.features]
    return [place(section, p) for p in fixed if turn_angle(contour, p) < 20.0]


def place(section: Section, p: Point) -> Point:
    """§6.4 twist and §6.5 placement."""
    c_t = section.twist_axis * section.chord
    theta = math.radians(section.twist)
    du, v = p[0] - c_t, p[1]
    u_t = c_t + du * math.cos(theta) + v * math.sin(theta)
    v_t = -du * math.sin(theta) + v * math.cos(theta)
    return section.x + (u_t - c_t), section.z + v_t


def target_polygons(job: Job, section: Section) -> list[list[Point]]:
    """The outer contour and every hole of one section, each a closed polygon."""
    polygons = [target_contour(section, job.min_te_thickness)]
    for feature in section.features:
        if feature.kind == "hole":
            polygons.append([place(section, p) for p in feature.points])
    return polygons


# --- reading the G-code (§13) ---------------------------------------------------


def feed_moves(gcode: str, letters: tuple[str, ...]) -> list[tuple[Axes, Axes, float]]:
    """Every G1 move as (start axes, end axes, F)."""
    moves: list[tuple[Axes, Axes, float]] = []
    previous: Axes | None = None
    lines = [line for line in gcode.splitlines() if line.startswith(("G0 ", "G1 "))]
    # The first motion line is the opening lift (§10.6): only the two vertical axes.
    assert [w[0] for w in lines[0].split()[1:]] == [letters[1], letters[3]], lines[0]
    for line in lines[1:]:
        words = line.split()
        values = {w[0]: float(w[1:]) for w in words[1:]}
        axes = (values[letters[0]], values[letters[1]], values[letters[2]], values[letters[3]])
        if words[0] == "G1":
            assert previous is not None
            moves.append((previous, axes, values["F"]))
        previous = axes
    return moves


def face_point(axes: Axes, y: float, span: float) -> Point:
    """Where the wire crosses the plane at y (machine-profile.md §6)."""
    a1, a2, a3, a4 = axes
    s = y / span
    return a1 + s * (a3 - a1), a2 + s * (a4 - a2)


def nearest_on_segment(p: Point, a: Point, b: Point) -> tuple[float, float]:
    """(distance from p to segment ab, parameter of the nearest point along ab)."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dz * dz
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / length2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz), t


def nearest_point(p: Point, edges: list[tuple[Point, Point]]) -> Point:
    _, t, (a, b) = min((*nearest_on_segment(p, a, b), (a, b)) for a, b in edges)
    return a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])


def polygon_edges(polygon: list[Point]) -> list[tuple[Point, Point]]:
    return [(polygon[i], polygon[(i + 1) % len(polygon)]) for i in range(len(polygon))]


def letters_and_span(profile_data: dict[str, Any]) -> tuple[tuple[str, ...], float]:
    return tuple(axis["letter"] for axis in profile_data["axes"]), float(profile_data["wire_span"])


def kerf_lookup(table: list[dict[str, float]], speed: float) -> float:
    """machine-profile.md §7: linear interpolation in the kerf table."""
    for p0, p1 in pairwise(table):
        if p0["speed"] <= speed <= p1["speed"]:
            return p0["kerf"] + (p1["kerf"] - p0["kerf"]) * (speed - p0["speed"]) / (p1["speed"] - p0["speed"])
    raise AssertionError(f"speed {speed} outside the kerf table")


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


# --- a. the uncompensated path passes through every target vertex ---------------


@pytest.mark.parametrize("job_path", JOBS, ids=[p.stem for p in JOBS])
def test_uncompensated_path_passes_through_every_target_vertex(job_path: Path, tmp_path: Path) -> None:
    """§10.3: every vertex of both target contours lies on the path within 0.001 mm.

    A constant kerf of 1e-6 mm makes the G-code path the uncompensated path
    to well within the 4-decimal resolution of the program.
    """
    profile_data: dict[str, Any] = yaml.safe_load(SAMPLE_PROFILE.read_text(encoding="utf-8"))
    profile_data["kerf_tables"] = [
        {"name": "eps-20-sample", "description": "negligible kerf",
         "points": [{"speed": 1.0, "kerf": 1e-6}, {"speed": 100000.0, "kerf": 1e-6}]}
    ]
    profile = load_profile(write_yaml(tmp_path / "profile.yaml", profile_data))
    job = load_job(job_path)
    letters, span = letters_and_span(profile_data)
    moves = feed_moves(generate(job, profile).gcode, letters)[1:-1]  # without lead-in and exit

    for section in (job.root, job.tip):
        path = [(face_point(a, section.y, span), face_point(b, section.y, span)) for a, b, _ in moves]
        for polygon in target_polygons(job, section):
            worst = max(min(nearest_on_segment(v, a, b)[0] for a, b in path) for v in polygon)
            assert worst <= 0.001, f"{section.label}: a target vertex is {worst:.4f} mm from the path"


# --- b. kerf matches the speed each move actually runs at ------------------------


@pytest.mark.parametrize("job_path", JOBS, ids=[p.stem for p in JOBS])
def test_kerf_applied_matches_the_actual_speed(job_path: Path) -> None:
    """§10.7: on every contour move, the kerf applied is the table kerf at the move's
    speed at that face, within 0.005 mm.

    Speed at a face = face segment length x F, both from the G-code. The kerf
    applied is twice the distance from the segment's midpoint to the target. A
    move is a contour move at a face if that nearest target point lies inside an
    edge of the outer contour or of a hole and the move runs within 20 degrees of
    that edge. This excludes the lead-in, the exit and the slits.

    At a join between stretches where the contour turns by less than 20 degrees,
    the kerf changes over one max_segment_length either side (§10.7). A move
    whose two ends lie that close to the join (measured at their nearest target
    points) must instead have a kerf between the table kerfs of the moves around
    the join, within 0.005 mm.
    """
    profile_data: dict[str, Any] = yaml.safe_load(SAMPLE_PROFILE.read_text(encoding="utf-8"))
    job = load_job(job_path)
    table = next(t for t in profile_data["kerf_tables"] if t["name"] == job.kerf_table)["points"]
    letters, span = letters_and_span(profile_data)
    moves = feed_moves(generate(job, load_profile(SAMPLE_PROFILE)).gcode, letters)[1:-1]

    checked = 0
    failures: list[str] = []
    for section in (job.root, job.tip):
        edges = [e for polygon in target_polygons(job, section) for e in polygon_edges(polygon)]
        joins = in_line_joins(job, section)
        # (move number, kerf applied, table kerf at its speed, joins it touches)
        found: list[tuple[int, float, float, list[int]]] = []
        for number, (start, end, feed) in enumerate(moves, start=2):
            p0, p1 = face_point(start, section.y, span), face_point(end, section.y, span)
            length = math.dist(p0, p1)
            if length < MIN_CHECKED_LENGTH:
                continue
            mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
            distance, t, (a, b) = min((*nearest_on_segment(mid, a, b), (a, b)) for a, b in edges)
            if not 0.0 < t < 1.0:
                continue
            cosine = abs((p1[0] - p0[0]) * (b[0] - a[0]) + (p1[1] - p0[1]) * (b[1] - a[1])) / (
                length * math.dist(a, b)
            )
            if cosine < math.cos(math.radians(20.0)):
                continue
            checked += 1
            feet = [nearest_point(p, edges) for p in (p0, p1)]
            touched = [
                j for j, join in enumerate(joins)
                if all(math.dist(foot, join) <= job.max_segment_length + 0.001 for foot in feet)
            ]
            found.append((number, 2 * distance, kerf_lookup(table, length * feed), touched))
        for number, applied, expected, touched in found:
            if touched:
                around = [k for _, _, k, t in found if set(t) & set(touched)]
                low, high = min(around), max(around)
            else:
                low = high = expected
            if not low - 0.005 <= applied <= high + 0.005:
                failures.append(
                    f"{section.label}, G1 move {number}: kerf applied {applied:.4f}, "
                    f"table {low:.4f}" + (f" to {high:.4f} (join)" if touched else "")
                )
    assert checked > 0.9 * 2 * len(moves), f"only {checked} face segments checked of {2 * len(moves)}"
    assert not failures, f"{len(failures)} of {checked} face segments wrong, first: " + "; ".join(failures[:5])

