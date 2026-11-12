"""Inner features: a hole reached by a slit from the upper side, and an open
cut reached by a slit from the lower side. Straight wire, so root and tip see
the same path.

Section frame (mm, LE at origin, v up): the rectangle runs u 0..100, v -20..0.

Hole "spar": u 40..60, v -15..-5, placed at x 140..160, z 25..35.
  Slit "upper" from the first point (40, -15) along +v meets the upper side
  v = 0 at u = 40: slit point (140, 40). Upper side is between points 0 and
  i_le = 1, as required.
  Wire: hole edges offset 0.5 into the hole -> (140.5, 25.5) (159.5, 25.5)
  (159.5, 34.5) (140.5, 34.5). Offset slit point (140, 40.5).

Open "hinge": u 70, v -15..-5, placed at x 170, z 25..35.
  Slit "lower" from (70, -15) along -v meets v = -20: slit point (170, 20).
  Open features are not offset; the slit runs from the offset slit point
  (170, 19.5) to the first vertex (170, 25).

Moves (length -> F = 100 / length):
  lead-in   (270, 40.5)   -> (200.5, 40.5)  69.5      F1.43885
  top       -> (140, 40.5)                  60.5      F1.65289
  slit in   -> (140.5, 25.5)  sqrt(0.5^2 + 15^2) = 15.00833  F6.66297
  hole      -> (159.5, 25.5)                19        F5.26316
            -> (159.5, 34.5)                9         F11.1111
            -> (140.5, 34.5)                19        F5.26316
            -> (140.5, 25.5)                9         F11.1111
  slit out  -> (140, 40.5)                  15.00833  F6.66297
  top       -> (99.5, 40.5)                 40.5      F2.46914
  LE        -> (99.5, 19.5)                 21        F4.76190
  bottom    -> (170, 19.5)                  70.5      F1.41844
  slit in   -> (170, 25)                    5.5       F18.1818
  open      -> (170, 35)                    10        F10.0000
            -> (170, 25)                    10        F10.0000
  slit out  -> (170, 19.5)                  5.5       F18.1818
  bottom    -> (200.5, 19.5)                30.5      F3.27869
  TE        -> (200.5, 40.5)                21        F4.76190
  exit      -> (270, 40.5)                  69.5      F1.43885
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from foamsim.job import parse_job
from foamsim.profile import parse_profile
from foamsim.sim import simulate

REPO = Path(__file__).resolve().parents[2]

PATH = [
    (270.0, 40.5, 1.43885),
    (200.5, 40.5, 1.43885),
    (140.0, 40.5, 1.65289),
    (140.5, 25.5, 6.66297),
    (159.5, 25.5, 5.26316),
    (159.5, 34.5, 11.1111),
    (140.5, 34.5, 5.26316),
    (140.5, 25.5, 11.1111),
    (140.0, 40.5, 6.66297),
    (99.5, 40.5, 2.46914),
    (99.5, 19.5, 4.76190),
    (170.0, 19.5, 1.41844),
    (170.0, 25.0, 18.1818),
    (170.0, 35.0, 10.0),
    (170.0, 25.0, 10.0),
    (170.0, 19.5, 18.1818),
    (200.5, 19.5, 3.27869),
    (200.5, 40.5, 4.76190),
    (270.0, 40.5, 1.43885),
]


def program(path: list[tuple[float, float, float]]) -> str:
    lines = ["G21", "G54", "G90", "G93", "G0 X300.0000 Y100.0000 Z300.0000 A100.0000"]
    x, z, _ = path[0]
    lines.append(f"G0 X{x:.4f} Y{z:.4f} Z{x:.4f} A{z:.4f}")
    for x, z, f in path[1:]:
        lines.append(f"G1 X{x:.4f} Y{z:.4f} Z{x:.4f} A{z:.4f} F{f}")
    lines += ["G0 X300.0000 Y100.0000 Z300.0000 A100.0000", "G94"]
    return "\n".join(lines) + "\n"


@pytest.fixture
def feature_job(job_data: dict[str, Any]) -> dict[str, Any]:
    for section in ("root", "tip"):
        job_data[section]["features"] = [
            {
                "name": "spar",
                "kind": "hole",
                "slit": "upper",
                "points": [[40.0, -15.0], [60.0, -15.0], [60.0, -5.0], [40.0, -5.0]],
            },
            {
                "name": "hinge",
                "kind": "open",
                "slit": "lower",
                "points": [[70.0, -15.0], [70.0, -5.0]],
            },
        ]
    return job_data


def test_features_match(profile_data: dict[str, Any], feature_job: dict[str, Any]) -> None:
    result = simulate(program(PATH), parse_profile(profile_data), parse_job(feature_job, REPO))
    assert result.passed, result.summary()
    root = result.sections["root"]
    assert set(root.elements) == {"outer", "hole spar", "open hinge"}
    for element in root.elements.values():
        assert element.max_deviation < 1e-6
    # A wire cannot cut a sharp inside corner: each hole corner keeps a fillet
    # of radius kerf/2. Its distance from the sharp corner is
    # 0.5 * sqrt(2) - 0.5 = 0.20711. Reported, not counted as a deviation.
    assert root.elements["hole spar"].inside_corner_rounding == pytest.approx(0.20711, abs=1e-4)
    assert root.elements["outer"].inside_corner_rounding == 0.0


def test_hole_too_small_is_found(
    profile_data: dict[str, Any], feature_job: dict[str, Any]
) -> None:
    # Hole cut without offset: the wire runs on the target hole edges, so the
    # hole is 0.5 mm too large on each side: corner deviation 0.70711.
    path = list(PATH)
    # Slit 15.5 mm -> F6.45161; long hole edges 20 -> F5; short 10 -> F10.
    path[3] = (140.0, 25.0, 6.45161)
    path[4] = (160.0, 25.0, 5.0)
    path[5] = (160.0, 35.0, 10.0)
    path[6] = (140.0, 35.0, 5.0)
    path[7] = (140.0, 25.0, 10.0)
    path[8] = (140.0, 40.5, 6.45161)
    result = simulate(program(path), parse_profile(profile_data), parse_job(feature_job, REPO))
    assert not result.passed
    hole = result.sections["root"].elements["hole spar"]
    assert hole.max_deviation == pytest.approx(0.70711, abs=1e-4)
    assert result.sections["root"].elements["outer"].max_deviation < 1e-6


def test_open_cut_in_wrong_place_is_found(
    profile_data: dict[str, Any], feature_job: dict[str, Any]
) -> None:
    # The open cut stops 2 mm short: (170, 33) instead of (170, 35).
    path = list(PATH)
    path[13] = (170.0, 33.0, 12.5)
    path[14] = (170.0, 25.0, 12.5)
    result = simulate(program(path), parse_profile(profile_data), parse_job(feature_job, REPO))
    assert not result.passed
    assert result.sections["tip"].elements["open hinge"].max_deviation == pytest.approx(2.0)


def test_missing_hole_is_found(profile_data: dict[str, Any], feature_job: dict[str, Any]) -> None:
    # Drop the slit and the hole: the whole hole boundary is uncovered.
    path = PATH[:3] + PATH[9:]
    result = simulate(program(path), parse_profile(profile_data), parse_job(feature_job, REPO))
    assert not result.passed
    assert result.sections["root"].elements["hole spar"].max_deviation == float("inf")
