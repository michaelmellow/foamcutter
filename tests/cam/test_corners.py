"""Matched corners of the outer contour (job-file.md §10.2): a notch reaches the wire at
root and tip at the same moment, even where it sits at a different place along the chord."""

import math
from pathlib import Path
from typing import Any

from foamcam import generate, load_job, load_profile
from foamcam.cam import CORNER_ANGLE, corners, matched_fixed_points
from foamcam.sections import build_outline

from .test_acceptance import (
    RECTANGLE,
    constant_kerf_profile,
    face_point,
    motions,
    rectangle_job,
    write_yaml,
)

SPAN = 900.0  # wire_span of constant_kerf_profile
ROOT_Y, TIP_Y = 300.0, 600.0  # section planes of rectangle_job


def notched(a: float, b: float, depth: float) -> list[list[float]]:
    """RECTANGLE with a notch `depth` deep in its upper side from chord fraction a to b."""
    (te_u, top), (le_u, _), lower_le, lower_te = RECTANGLE
    return [[te_u, top], [b, top], [b, top - depth], [a, top - depth], [a, top], [le_u, top], lower_le, lower_te]


def job_with(root_points: list[list[float]], tip_points: list[list[float]]) -> dict[str, Any]:
    job = rectangle_job()
    job["root"]["shape"] = {"points": root_points}
    job["tip"]["shape"] = {"points": tip_points}
    return job


def outlines(tmp_path: Path, job: dict[str, Any]):  # type: ignore[no-untyped-def]
    loaded = load_job(write_yaml(tmp_path / "job.yaml", job))
    problems: list[str] = []
    root = build_outline(loaded.root, loaded.min_te_thickness, problems)
    tip = build_outline(loaded.tip, loaded.min_te_thickness, problems)
    assert not problems and root is not None and tip is not None
    return loaded, root, tip


def test_corners_are_the_sharp_turns_only(tmp_path: Path) -> None:
    job, root, _ = outlines(tmp_path, job_with(notched(0.6, 0.7, 0.05), RECTANGLE))
    found = corners(root, job.direction)
    # upper TE, the four notch corners, LE, lower LE corner, lower TE: all 90 degrees
    assert len(found) == 8
    assert CORNER_ANGLE == 60.0


def test_notch_corners_are_matched_in_order(tmp_path: Path) -> None:
    # the notch is at 60-70 % of the chord at the root and at 45-55 % at the tip
    job, root, tip = outlines(tmp_path, job_with(notched(0.6, 0.7, 0.05), notched(0.45, 0.55, 0.1)))
    pos_r, pos_t, labels, unmatched = matched_fixed_points(root, tip, job.direction)
    assert labels == ["start", "corner 1", "corner 2", "corner 3", "corner 4", "leading edge", "corner 5",
                      "far trailing edge", "start"]
    assert unmatched == []
    assert pos_r == pos_t == [0, 1, 2, 3, 4, 5, 6, 7, 8]  # the same vertex numbers: the same shape order


def test_wire_reaches_each_notch_corner_at_both_faces_at_once(tmp_path: Path) -> None:
    job = job_with(notched(0.6, 0.7, 0.05), notched(0.45, 0.55, 0.1))
    result = generate(load_job(write_yaml(tmp_path / "job.yaml", job)),
                      load_profile(write_yaml(tmp_path / "profile.yaml", constant_kerf_profile())))
    faces = [(face_point(axes, ROOT_Y, SPAN), face_point(axes, TIP_Y, SPAN))
             for command, axes, _ in motions(result.gcode) if command == "G1"]
    # placed notch corners (§6.5): root chord 100, LE at (100, 50); tip chord 50, LE at (150, 60);
    # the top edge is at z = LE z, the notch floor depth x chord below it
    root_corners = [(170.0, 50.0), (170.0, 45.0), (160.0, 45.0), (160.0, 50.0)]
    tip_corners = [(177.5, 60.0), (177.5, 55.0), (172.5, 55.0), (172.5, 60.0)]
    for r_corner, t_corner in zip(root_corners, tip_corners):
        # the offset corner is within half a kerf diagonally (0.1 x sqrt 2 mm) of the drawn one
        lines = [i for i, (r, _) in enumerate(faces) if math.dist(r, r_corner) < 0.15]
        assert len(lines) == 1, (r_corner, lines)
        assert math.dist(faces[lines[0]][1], t_corner) < 0.15, (r_corner, faces[lines[0]])
    assert "Matched corners of the outer contour: 5" in result.report


def test_different_corner_counts_fall_back_and_are_reported(tmp_path: Path) -> None:
    job = job_with(notched(0.6, 0.7, 0.05), RECTANGLE)  # no notch at the tip
    _, root, tip = outlines(tmp_path, job)
    _, _, labels, unmatched = matched_fixed_points(root, tip, "upper_first")
    assert labels == ["start", "leading edge", "corner 1", "far trailing edge", "start"]  # lower LE corner only
    assert unmatched == ["start to leading edge: 4 corners at the root, 0 at the tip, not matched"]
    result = generate(load_job(write_yaml(tmp_path / "job.yaml", job)),
                      load_profile(write_yaml(tmp_path / "profile.yaml", constant_kerf_profile())))
    assert "warning: start to leading edge: 4 corners at the root, 0 at the tip, not matched" in result.report


def test_rectangle_corner_is_one_line_not_two(tmp_path: Path) -> None:
    """Before matched corners the lower LE corner of the tapered rectangle took two lines,
    one where each face reached it (test_acceptance's hand calculation)."""
    result = generate(load_job(write_yaml(tmp_path / "job.yaml", rectangle_job())),
                      load_profile(write_yaml(tmp_path / "profile.yaml", constant_kerf_profile())))
    faces = [(face_point(axes, ROOT_Y, SPAN), face_point(axes, TIP_Y, SPAN))
             for command, axes, _ in motions(result.gcode) if command == "G1"]
    at_corner = [(r, t) for r, t in faces if math.dist(r, (99.9, 29.9)) < 0.01 or math.dist(t, (149.9, 49.9)) < 0.01]
    assert len(at_corner) == 1
    r, t = at_corner[0]
    assert math.dist(r, (99.9, 29.9)) < 0.01 and math.dist(t, (149.9, 49.9)) < 0.01
