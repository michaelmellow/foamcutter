"""Acceptance tests from docs/briefs/brief-3-cam-core.md.

Each test builds its job and profile here, runs the CAM, and checks the G-code
with a small parser of its own that follows docs/formats/job-file.md §13, so the
checks do not reuse the CAM's own geometry code.
"""

import math
import re
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import yaml

from foamcam import CamError, generate, load_job, load_profile

ROOT = Path(__file__).resolve().parents[2]
AIRFOILS = ROOT / "examples" / "airfoils"
SAMPLE_PROFILE = ROOT / "examples" / "profiles" / "sample-machine.yaml"

AXIS_LETTERS = ("X", "Y", "Z", "A")

Motion = tuple[str, tuple[float, float, float, float], float | None]


# --- helpers ---------------------------------------------------------------


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def motions(gcode: str, letters: tuple[str, ...] = AXIS_LETTERS) -> list[Motion]:
    """Parse G0/G1 lines into (command, (a1, a2, a3, a4), F or None)."""
    result: list[Motion] = []
    for line in gcode.splitlines():
        words = line.split()
        if not words or words[0] not in ("G0", "G1"):
            continue
        values = {w[0]: float(w[1:]) for w in words[1:]}
        axes = (values[letters[0]], values[letters[1]], values[letters[2]], values[letters[3]])
        result.append((words[0], axes, values.get("F")))
    return result


def motion_words(gcode: str) -> list[list[str]]:
    return [line.split() for line in gcode.splitlines() if line.startswith(("G0 ", "G1 "))]


def face_point(axes: tuple[float, float, float, float], y: float, span: float) -> tuple[float, float]:
    """Point (x, z) where the wire crosses the plane at y (machine-profile.md §6)."""
    a1, a2, a3, a4 = axes
    s = y / span
    return a1 + s * (a3 - a1), a2 + s * (a4 - a2)


def constant_kerf_profile(**axis4: float) -> dict[str, Any]:
    """A profile with constant kerf 0.2 mm, so offsets can be worked out by hand."""
    axes = [
        {"letter": "X", "min": 0.0, "max": 600.0, "max_rate": 2000.0, "lead": 8.0},
        {"letter": "Y", "min": 0.0, "max": 350.0, "max_rate": 2000.0, "lead": 8.0},
        {"letter": "Z", "min": 0.0, "max": 600.0, "max_rate": 2000.0, "lead": 8.0},
        {"letter": "A", "min": 0.0, "max": 350.0, "max_rate": 2000.0, "lead": 8.0},
    ]
    axes[3].update(axis4)
    return {
        "format": "foamcutter-machine-profile",
        "version": 1,
        "name": "test machine",
        "axes": axes,
        "wire_span": 900.0,
        "limits": {"max_wire_angle": 30.0, "max_speed_ratio": 3.0},
        "kerf_tables": [
            {
                "name": "constant-0.2",
                "description": "constant kerf for hand calculation",
                "points": [{"speed": 1.0, "kerf": 0.2}, {"speed": 100000.0, "kerf": 0.2}],
            }
        ],
    }


# Rectangle "section" in chord fractions, Selig order: upper TE, LE (top-left
# corner), bottom-left corner, lower TE. After job-file.md §6.1 it becomes
# (1, 0), (0, 0), (0, -0.2), (1, -0.2).
RECTANGLE = [[1.0, 0.1], [0.0, 0.1], [0.0, -0.1], [1.0, -0.1]]


def rectangle_job(**overrides: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "format": "foamcutter-job",
        "version": 1,
        "name": "tapered rectangle",
        "kerf_table": "constant-0.2",
        "cut_speed": 100.0,
        "direction": "upper_first",
        "max_segment_length": 10.0,
        "min_te_thickness": 0.0,
        "lead_in": 10.0,
        "safe_position": [230.0, 100.0, 230.0, 100.0],
        "mirror": False,
        "block": {"x": 90.0, "length": 120.0, "height": 80.0},
        "root": {
            "shape": {"points": RECTANGLE},
            "chord": 100.0,
            "twist": 0.0,
            "twist_axis": 0.0,
            "position": {"x": 100.0, "y": 300.0, "z": 50.0},
        },
        "tip": {
            "shape": {"points": RECTANGLE},
            "chord": 50.0,
            "twist": 0.0,
            "twist_axis": 0.0,
            "position": {"x": 150.0, "y": 600.0, "z": 60.0},
        },
    }
    job.update(overrides)
    return job


def tapered_wing_job(**overrides: Any) -> dict[str, Any]:
    """Tapered, swept, twisted Clark Y panel with a spar hole (sample profile)."""
    job: dict[str, Any] = {
        "format": "foamcutter-job",
        "version": 1,
        "name": "tapered swept wing",
        "kerf_table": "eps-20-sample",
        "cut_speed": 150.0,
        "direction": "upper_first",
        "max_segment_length": 1.0,
        "min_te_thickness": 0.6,
        "lead_in": 20.0,
        "safe_position": [550.0, 250.0, 550.0, 250.0],
        "mirror": False,
        "block": {"x": 100.0, "length": 300.0, "height": 60.0},
        "root": {
            "shape": {"dat": str(AIRFOILS / "clarky.dat")},
            "chord": 250.0,
            "twist": 0.0,
            "twist_axis": 0.25,
            "position": {"x": 182.5, "y": 150.0, "z": 25.0},
            "features": [
                {"name": "spar", "kind": "hole", "slit": "upper",
                 "points": [[70.0, 4.0], [80.0, 4.0], [80.0, 14.0], [70.0, 14.0]]},
            ],
        },
        "tip": {
            "shape": {"dat": str(AIRFOILS / "clarky.dat")},
            "chord": 160.0,
            "twist": -2.0,
            "twist_axis": 0.25,
            "position": {"x": 180.0, "y": 750.0, "z": 25.0},
            "features": [
                {"name": "spar", "kind": "hole", "slit": "upper",
                 "points": [[42.0, 3.0], [52.0, 3.0], [52.0, 10.0], [42.0, 10.0]]},
            ],
        },
    }
    job.update(overrides)
    return job


# --- acceptance 1 ----------------------------------------------------------


def test_identical_sections_in_centred_block_give_equal_towers(tmp_path: Path) -> None:
    """Identical root and tip, block centred: axis 1 = axis 3 and axis 2 = axis 4 on every line."""
    section = {
        "shape": {"dat": str(AIRFOILS / "clarky.dat")},
        "chord": 200.0,
        "twist": 3.0,
        "twist_axis": 0.3,
        "position": {"x": 200.0, "y": 0.0, "z": 25.0},
        "features": [
            {"name": "spar", "kind": "hole", "slit": "lower",
             "points": [[55.0, 3.0], [65.0, 3.0], [65.0, 12.0], [55.0, 12.0]]},
        ],
    }
    # wire_span 900: block from y = 150 to y = 750, centred on y = 450.
    root = {**section, "position": {"x": 200.0, "y": 150.0, "z": 25.0}}
    tip = {**section, "position": {"x": 200.0, "y": 750.0, "z": 25.0}}
    job = tapered_wing_job(root=root, tip=tip)
    result = generate(load_job(write_yaml(tmp_path / "job.yaml", job)), load_profile(SAMPLE_PROFILE))

    lines = motion_words(result.gcode)
    assert len(lines) > 100
    for words in lines:
        values = {w[0]: w[1:] for w in words[1:]}
        # Compare the written text, so the equality is exact.
        assert values["X"] == values["Z"], words
        assert values["Y"] == values["A"], words


# --- acceptance 2 ----------------------------------------------------------


def test_tapered_projection_matches_hand_calculation(tmp_path: Path) -> None:
    """Tapered case with a hand-calculated projection: output matches to 0.01 mm.

    Hand calculation (job-file.md §6, §10, machine-profile.md §3):

    Sections (twist 0, twist_axis 0, so the leading edge sits on `position`):
      root: chord 100 at y = 300, LE at (x, z) = (100, 50)
            rectangle corners: upper TE (200, 50), LE (100, 50),
                               lower LE corner (100, 30), lower TE (200, 30)
      tip:  chord 50 at y = 600, LE at (150, 60)
            corners: (200, 60), (150, 60), (150, 50), (200, 50)

    Kerf 0.2 everywhere, so every edge moves 0.1 outward and each 90° corner
    moves 0.1 in x and 0.1 in z, away from the rectangle:
      root: S = (200.1, 50.1)  LE = (99.9, 50.1)  P2 = (99.9, 29.9)  P3 = (200.1, 29.9)
      tip:  S = (200.1, 60.1)  LE = (149.9, 60.1) P2 = (149.9, 49.9) P3 = (200.1, 49.9)

    Corner P2 is kept by the resampling of §10.3: on the piece LE -> lower TE the
    corner is at arc length 20 of 120 (root) and 10 of 60 (tip), both 1/6.
    max_segment_length 10 gives m = ceil(120 / 10) = 12 steps, and 12/6 = 2, so
    step 2 lands exactly on the corner in both sections.

    Projection, y_r = 300, y_t = 600, wire_span = 900:
      s(0)   = (0 - 300) / 300   = -1  ->  a = root - 1 * (tip - root)
      s(900) = (900 - 300) / 300 =  2  ->  a = root + 2 * (tip - root)

      S:  a1 = 200.1 - 0          = 200.1   a2 = 50.1 - 10   = 40.1
          a3 = 200.1 + 0          = 200.1   a4 = 50.1 + 20   = 70.1
      LE: a1 = 99.9 - 50          = 49.9    a2 = 50.1 - 10   = 40.1
          a3 = 99.9 + 100         = 199.9   a4 = 50.1 + 20   = 70.1
      P2: a1 = 99.9 - 50          = 49.9    a2 = 29.9 - 20   = 9.9
          a3 = 99.9 + 100         = 199.9   a4 = 29.9 + 40   = 69.9
      P3: a1 = 200.1 - 0          = 200.1   a2 = 29.9 - 20   = 9.9
          a3 = 200.1 + 0          = 200.1   a4 = 29.9 + 40   = 69.9

    Entry point (§10.5): x_E = block.x + block.length + lead_in = 90 + 120 + 10 = 220,
    at the z of each section's S (root 50.1, tip 60.1):
      E:  a1 = 220  a2 = 50.1 - 10 = 40.1  a3 = 220  a4 = 50.1 + 20 = 70.1
    """
    expected = [
        ("G0", (220.0, 40.1, 220.0, 70.1)),   # rapid to E
        ("G1", (200.1, 40.1, 200.1, 70.1)),   # lead-in ends at S
        ("G1", (49.9, 40.1, 199.9, 70.1)),    # LE
        ("G1", (49.9, 9.9, 199.9, 69.9)),     # lower LE corner P2
        ("G1", (200.1, 9.9, 200.1, 69.9)),    # lower TE P3
        ("G1", (200.1, 40.1, 200.1, 70.1)),   # back at S after the TE face
        ("G1", (220.0, 40.1, 220.0, 70.1)),   # exit ends at E
    ]
    profile = write_yaml(tmp_path / "profile.yaml", constant_kerf_profile())
    job = write_yaml(tmp_path / "job.yaml", rectangle_job())
    result = generate(load_job(job), load_profile(profile))
    found = motions(result.gcode)

    position = 0
    for command, axes in expected:
        while position < len(found):
            got_command, got_axes, _ = found[position]
            position += 1
            if got_command == command and all(abs(g - e) <= 0.01 for g, e in zip(got_axes, axes)):
                break
        else:
            pytest.fail(f"{command} {axes} not found in order in the program")


# --- acceptance 3 ----------------------------------------------------------


def test_mirrored_job_is_exact_mirror(tmp_path: Path) -> None:
    """Mirrored job: output is the exact mirror of the original."""
    profile = load_profile(SAMPLE_PROFILE)
    normal = generate(load_job(write_yaml(tmp_path / "a.yaml", tapered_wing_job())), profile)
    mirrored = generate(load_job(write_yaml(tmp_path / "b.yaml", tapered_wing_job(mirror=True))), profile)

    a = motion_words(normal.gcode)
    b = motion_words(mirrored.gcode)
    assert len(a) == len(b) > 100
    for words_a, words_b in zip(a, b):
        va = {w[0]: w[1:] for w in words_a[1:]}
        vb = {w[0]: w[1:] for w in words_b[1:]}
        assert words_a[0] == words_b[0]
        assert (vb["X"], vb["Y"], vb["Z"], vb["A"]) == (va["Z"], va["A"], va["X"], va["Y"])
        assert vb.get("F") == va.get("F")


# --- acceptance 4 ----------------------------------------------------------


def test_job_exceeding_travel_names_axis_and_amount(tmp_path: Path) -> None:
    """A job exceeding travel is rejected with the axis and the amount named.

    From the hand calculation above, axis 4 reaches 70.1 at S, LE and E.
    With axis 4 max = 65 the excess is 70.1 - 65 = 5.1 mm.
    """
    profile = write_yaml(tmp_path / "profile.yaml", constant_kerf_profile(max=65.0))
    job = write_yaml(tmp_path / "job.yaml", rectangle_job(safe_position=[230.0, 100.0, 230.0, 60.0]))
    with pytest.raises(CamError) as caught:
        generate(load_job(job), load_profile(profile))
    message = str(caught.value)
    assert re.search(r"axis 4 \(A\)", message), message
    assert "5.1" in message, message
    assert "axis 1" not in message and "axis 2" not in message and "axis 3" not in message


# --- acceptance 5 ----------------------------------------------------------


def test_total_time_equals_faster_face_length_over_speed(tmp_path: Path) -> None:
    """Total cut time = path length at the faster end / cut speed, within 1 %."""
    job_data = tapered_wing_job()
    profile = load_profile(SAMPLE_PROFILE)
    result = generate(load_job(write_yaml(tmp_path / "job.yaml", job_data)), profile)

    span = 900.0  # wire_span of the sample profile
    y_root = job_data["root"]["position"]["y"]
    y_tip = job_data["tip"]["position"]["y"]

    moves = motions(result.gcode)
    program_time = 0.0
    faster_length = 0.0
    for previous, current in pairwise(moves):
        command, axes, feed = current
        if command != "G1":
            continue
        assert feed is not None and feed > 0
        program_time += 1.0 / feed
        lengths = []
        for y in (y_root, y_tip):
            x0, z0 = face_point(previous[1], y, span)
            x1, z1 = face_point(axes, y, span)
            lengths.append(math.hypot(x1 - x0, z1 - z0))
        faster_length += max(lengths)

    expected_time = faster_length / job_data["cut_speed"]
    assert program_time == pytest.approx(expected_time, rel=0.01)
    assert result.total_time == pytest.approx(expected_time, rel=0.01)
