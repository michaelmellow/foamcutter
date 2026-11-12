"""Shared fixtures for the simulator tests.

Every program here is written by hand. The comments show how each number was
calculated, so the expected results in the tests can be checked with a pencil.

Common set-up (see `profile_data` and `job_data`):

- wire_span = 1000 mm. Root plane y = 100, tip plane y = 900.
- Section shape: the points list
      (1.0, 0.1) (0.0, 0.1) (0.0, -0.1) (1.0, -0.1)
  Job format section 6.1: the leading edge is point 1 (first point with the
  smallest u); mean v before it is 0.1, after it is -0.1, so no reversal.
  Translate by (0, -0.1): (1, 0) (0, 0) (0, -0.2) (1, -0.2). The u range is 1,
  so nothing changes when normalising.
- chord 100, twist 0, twist_axis 0 (so c_t = 0 and the twist-axis point is the
  leading edge). Section 6.5: x = X + u, z = Z + v.
- position (100, y, 40) gives the target rectangle
      x from 100 to 200, z from 20 to 40,
  traversed (200, 40) -> (100, 40) -> (100, 20) -> (200, 20).
- Block: x from 50 to 250, z from 0 to 60, y from 100 to 900.
- Kerf table "const-1.0": kerf 1.0 mm from 50 to 400 mm/min, so the wire must
  run 0.5 mm outside the target.
- cut_speed 100 mm/min.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

PROFILE: dict[str, Any] = {
    "format": "foamcutter-machine-profile",
    "version": 1,
    "name": "test machine",
    "axes": [
        {"letter": "X", "min": 0.0, "max": 600.0, "max_rate": 1200.0, "lead": 8.0},
        {"letter": "Y", "min": 0.0, "max": 350.0, "max_rate": 1200.0, "lead": 8.0},
        {"letter": "Z", "min": 0.0, "max": 600.0, "max_rate": 1200.0, "lead": 8.0},
        {"letter": "A", "min": 0.0, "max": 350.0, "max_rate": 1200.0, "lead": 8.0},
    ],
    "wire_span": 1000.0,
    "limits": {"max_wire_angle": 30.0, "max_speed_ratio": 3.0},
    "kerf_tables": [
        {
            "name": "const-1.0",
            "description": "constant 1.0 mm",
            "points": [{"speed": 50.0, "kerf": 1.0}, {"speed": 400.0, "kerf": 1.0}],
        },
        {
            # At 100 mm/min: 1.4 + (1.0 - 1.4) * (100 - 50) / (150 - 50) = 1.2
            "name": "lin",
            "description": "linear 1.4 at 50 to 1.0 at 150",
            "points": [{"speed": 50.0, "kerf": 1.4}, {"speed": 150.0, "kerf": 1.0}],
        },
        {
            "name": "fast",
            "description": "only fast speeds",
            "points": [{"speed": 200.0, "kerf": 1.0}, {"speed": 400.0, "kerf": 1.0}],
        },
    ],
}

RECTANGLE = [[1.0, 0.1], [0.0, 0.1], [0.0, -0.1], [1.0, -0.1]]

JOB: dict[str, Any] = {
    "format": "foamcutter-job",
    "version": 1,
    "name": "test rectangle",
    "kerf_table": "const-1.0",
    "cut_speed": 100.0,
    "direction": "upper_first",
    "max_segment_length": 1.0,
    "min_te_thickness": 0.0,
    "lead_in": 20.0,
    "safe_position": [300.0, 100.0, 300.0, 100.0],
    "mirror": False,
    "block": {"x": 50.0, "length": 200.0, "height": 60.0},
    "root": {
        "shape": {"points": RECTANGLE},
        "chord": 100.0,
        "twist": 0.0,
        "twist_axis": 0.0,
        "position": {"x": 100.0, "y": 100.0, "z": 40.0},
    },
    "tip": {
        "shape": {"points": RECTANGLE},
        "chord": 100.0,
        "twist": 0.0,
        "twist_axis": 0.0,
        "position": {"x": 100.0, "y": 900.0, "z": 40.0},
    },
}

# Straight wire (a1 = a3, a2 = a4), so the wire meets every plane at the same
# (x, z). The path is the target rectangle offset outward by kerf/2 = 0.5:
#   S  = (200.5, 40.5), then (99.5, 40.5), (99.5, 19.5), (200.5, 19.5), S.
# Entry x_E = block.x + block.length + lead_in = 50 + 200 + 20 = 270, at the
# z of S: E = (270, 40.5).
# Durations (G93, T = 1/F min) at 100 mm/min:
#   lead-in / exit 270 - 200.5 = 69.5 mm -> T = 0.695 -> F = 1/0.695 = 1.43885
#   long edges     200.5 - 99.5 = 101 mm -> T = 1.01  -> F = 0.990099
#   short edges    40.5 - 19.5  = 21 mm  -> T = 0.21  -> F = 4.76190
# Total feed time = 2*0.695 + 2*1.01 + 2*0.21 = 3.83 min.
STRAIGHT = """\
(hand-written test program: straight rectangle)
G21
G54
G90
G93
G0 X300.0000 Y100.0000 Z300.0000 A100.0000
G0 X270.0000 Y40.5000 Z270.0000 A40.5000
G1 X200.5000 Y40.5000 Z200.5000 A40.5000 F1.43885
G1 X99.5000 Y40.5000 Z99.5000 A40.5000 F0.990099
G1 X99.5000 Y19.5000 Z99.5000 A19.5000 F4.76190
G1 X200.5000 Y19.5000 Z200.5000 A19.5000 F0.990099
G1 X200.5000 Y40.5000 Z200.5000 A40.5000 F4.76190
G1 X270.0000 Y40.5000 Z270.0000 A40.5000 F1.43885
G0 X300.0000 Y100.0000 Z300.0000 A100.0000
G94
"""

# Tapered: root chord 100 at y = 100 (rectangle x 100..200, z 20..40), tip
# chord 50 at y = 900 (x 100..150, z 30..40). Both offset outward by 0.5:
#   root path (200.5, 40.5) (99.5, 40.5) (99.5, 19.5) (200.5, 19.5)
#   tip  path (150.5, 40.5) (99.5, 40.5) (99.5, 29.5) (150.5, 29.5)
# Projection to the towers: s(y) = (y - 100) / 800, s(0) = -0.125,
# s(1000) = 1.125, a = p_r + s * (p_t - p_r):
#   S:      a1 = 200.5 - 0.125*(-50) = 206.75   a3 = 200.5 + 1.125*(-50) = 144.25
#           a2 = a4 = 40.5
#   LE top: all 99.5 / 40.5
#   LE bot: a2 = 19.5 - 0.125*10 = 18.25       a4 = 19.5 + 1.125*10 = 30.75
#   TE bot: a1 = 206.75 a2 = 18.25 a3 = 144.25 a4 = 30.75
#   Entry:  x = 270 at both planes, z = 40.5 -> a1 = a3 = 270, a2 = a4 = 40.5
# Durations T = max(d_root, d_tip) / 100:
#   lead-in/exit root 69.5, tip 119.5 -> T = 1.195 -> F = 0.836820
#   long edges   root 101,  tip 51    -> T = 1.01  -> F = 0.990099
#   short edges  root 21,   tip 11    -> T = 0.21  -> F = 4.76190
# Speed ratios: long 101/51 = 1.980, short 21/11 = 1.909, lead 119.5/69.5 = 1.719.
# Wire angle at the TE-bottom corner: atan(sqrt(62.5^2 + 12.5^2) / 1000)
#   = atan(0.063738) = 3.647 degrees.
TAPERED = """\
(hand-written test program: tapered rectangle)
G21
G54
G90
G93
G0 X300.0000 Y100.0000 Z300.0000 A100.0000
G0 X270.0000 Y40.5000 Z270.0000 A40.5000
G1 X206.7500 Y40.5000 Z144.2500 A40.5000 F0.836820
G1 X99.5000 Y40.5000 Z99.5000 A40.5000 F0.990099
G1 X99.5000 Y18.2500 Z99.5000 A30.7500 F4.76190
G1 X206.7500 Y18.2500 Z144.2500 A30.7500 F0.990099
G1 X206.7500 Y40.5000 Z144.2500 A40.5000 F4.76190
G1 X270.0000 Y40.5000 Z270.0000 A40.5000 F0.836820
G0 X300.0000 Y100.0000 Z300.0000 A100.0000
G94
"""


@pytest.fixture
def profile_data() -> dict[str, Any]:
    return copy.deepcopy(PROFILE)


@pytest.fixture
def job_data() -> dict[str, Any]:
    return copy.deepcopy(JOB)


@pytest.fixture
def tapered_job_data() -> dict[str, Any]:
    job = copy.deepcopy(JOB)
    job["tip"]["chord"] = 50.0
    return job


@pytest.fixture
def straight_gcode() -> str:
    return STRAIGHT


@pytest.fixture
def tapered_gcode() -> str:
    return TAPERED
