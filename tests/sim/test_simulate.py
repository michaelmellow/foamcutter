"""Brief 5 tasks 2 to 5 and the acceptance list, on hand-written programs.

The programs and the arithmetic behind them are in conftest.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from foamsim.job import parse_job
from foamsim.profile import parse_profile
from foamsim.sim import SimResult, simulate
from foamsim.wire import trace_point, wire_angle

REPO = Path(__file__).resolve().parents[2]


def run(
    gcode: str,
    profile_data: dict[str, Any],
    job_data: dict[str, Any],
    planes: tuple[float, ...] = (),
) -> SimResult:
    return simulate(gcode, parse_profile(profile_data), parse_job(job_data, REPO), planes=planes)


def check(result: SimResult, name: str) -> tuple[bool, str]:
    found = [c for c in result.checks if c.name == name]
    assert len(found) == 1, name
    return found[0].passed, "\n".join(found[0].messages)


def scale_axis(gcode: str, letter: str, factor: float) -> str:
    def repl(m: re.Match[str]) -> str:
        return f"{letter}{float(m.group(1)) * factor:.4f}"

    return re.sub(rf"{letter}(-?\d+\.\d+)", repl, gcode)


# ---------------------------------------------------------------- wire geometry


def test_wire_meets_a_plane_on_the_straight_line() -> None:
    # L = (206.75, 0, 18.25), R = (144.25, 1000, 30.75). At y = 900:
    # x = 206.75 + 0.9 * (144.25 - 206.75) = 150.5, z = 18.25 + 0.9 * 12.5 = 29.5
    assert trace_point((206.75, 18.25, 144.25, 30.75), 1000.0, 900.0) == pytest.approx(
        (150.5, 29.5)
    )


def test_wire_angle_formula() -> None:
    # sqrt(62.5^2 + 12.5^2) / 1000 = 0.0637377; atan(x) = x - x^3/3 + ... = 0.0636515 rad
    # = 3.6470 degrees
    assert wire_angle((206.75, 18.25, 144.25, 30.75), 1000.0) == pytest.approx(3.6470, abs=1e-4)


# ---------------------------------------------------------------- correct programs pass


def test_straight_program_matches_target(
    straight_gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    result = run(straight_gcode, profile_data, job_data)
    assert result.passed, result.summary()
    for name in ("root", "tip"):
        assert result.sections[name].max_deviation < 1e-9
    # 2*0.695 + 2*1.01 + 2*0.21 = 3.83 min (F values rounded to 6 digits)
    assert result.feed_time == pytest.approx(3.83, abs=1e-4)


def test_tapered_program_matches_target(
    tapered_gcode: str, profile_data: dict[str, Any], tapered_job_data: dict[str, Any]
) -> None:
    result = run(tapered_gcode, profile_data, tapered_job_data)
    assert result.passed, result.summary()
    for name in ("root", "tip"):
        assert result.sections[name].max_deviation < 1e-6
    # 2*1.195 + 2*1.01 + 2*0.21 = 4.83 min
    assert result.feed_time == pytest.approx(4.83, abs=1e-4)


def test_face_speeds_reported(
    tapered_gcode: str, profile_data: dict[str, Any], tapered_job_data: dict[str, Any]
) -> None:
    result = run(tapered_gcode, profile_data, tapered_job_data)
    # Contour moves only (lead-in and exit excluded). Root: always 100 mm/min.
    # Tip: long edges 51 / 1.01 = 50.495, short edges 11 / 0.21 = 52.381.
    speeds = result.face_speeds
    assert speeds["root"] == pytest.approx((100.0, 100.0), abs=1e-3)
    assert speeds["tip"] == pytest.approx((50.495, 52.381), abs=1e-3)
    # Lead-in: 119.5 / 69.5 = 1.7194, exempt but reported.
    assert result.lead_ratios == pytest.approx((1.7194, 1.7194), abs=1e-4)


# ---------------------------------------------------------------- kerf removal


def test_uncompensated_program_fails_by_half_kerf(
    straight_gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    # Wire on the target itself: the part comes out 0.5 mm small on every side.
    # The worst point is a target corner, sqrt(0.5^2 + 0.5^2) = 0.70711 from the
    # nearest corner of the simulated part.
    gcode = (
        straight_gcode.replace("200.5000", "200.0000")
        .replace("99.5000", "100.0000")
        .replace("40.5000", "40.0000")
        .replace("19.5000", "20.0000")
    )
    result = run(gcode, profile_data, job_data)
    assert not result.passed
    for name in ("root", "tip"):
        assert result.sections[name].max_deviation == pytest.approx(0.70711, abs=1e-4)
        assert not result.sections[name].passed


def test_kerf_is_interpolated_from_the_table(
    straight_gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    # Table "lin" gives kerf 1.2 at 100 mm/min, so the surface is 0.6 mm inside
    # the wire, 0.1 mm inside the target: corners 0.1 * sqrt(2) = 0.14142.
    job_data["kerf_table"] = "lin"
    result = run(straight_gcode, profile_data, job_data)
    assert result.sections["root"].max_deviation == pytest.approx(0.14142, abs=1e-4)
    assert not result.passed


def test_kerf_outside_table_is_an_error(
    straight_gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    job_data["kerf_table"] = "fast"
    result = run(straight_gcode, profile_data, job_data)
    assert not result.passed
    error = result.sections["root"].error
    assert error is not None
    assert 'kerf table "fast": speed 100.0 mm/min' in error
    assert "outside the table range 200.0 to 400.0 mm/min" in error


def test_kerf_step_between_nearly_parallel_edges(
    profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    # Table "lin". The top edge is split at V = (150, 39.5): the wire dips 1 mm
    # toward the material. Edge a, S (200.5, 40.5) -> V, at 50 mm/min: kerf 1.4.
    # Edge b, V -> (99.5, 40.5), at 150 mm/min: kerf 1.0. Every other edge at
    # 150 mm/min (kerf 1.0, so 0.5 offset: exact). Lengths: a and b
    # sqrt(50.5^2 + 1) = 50.5099, short edges 21, bottom 101.
    #   F(a) = 50 / 50.5099 = 0.989905 (speed 50.00001: kerf 1.39999)
    #   F(b) = 2.96970, short 7.14285, bottom 1.48514 (speeds just under 150)
    # Moved edges, material below: a ends at V + 0.7 * n_a with
    # n_a = (1, -50.5) / 50.5099 -> (150.0139, 38.8001); b starts at
    # V + 0.5 * n_b = (149.9901, 39.0001). The two moved lines meet at
    # x = 155.05, behind the start of b, so they do not meet as a corner: the
    # surface steps from the end of a to the start of b, around the 0.7 mm end
    # of slot a. Its lowest point is V - (0, 0.7) = (150, 38.8), 1.2 mm under
    # the target z = 40 (the straight step misses the arc bottom by 0.0002).
    # Joining at the line intersection instead would give 40 - 38.90 = 1.10.
    gcode = """\
G21
G54
G90
G93
G0 X300.0000 Y100.0000 Z300.0000 A100.0000
G0 X270.0000 Y40.5000 Z270.0000 A40.5000
G1 X200.5000 Y40.5000 Z200.5000 A40.5000 F2.15827
G1 X150.0000 Y39.5000 Z150.0000 A39.5000 F0.989905
G1 X99.5000 Y40.5000 Z99.5000 A40.5000 F2.96970
G1 X99.5000 Y19.5000 Z99.5000 A19.5000 F7.14285
G1 X200.5000 Y19.5000 Z200.5000 A19.5000 F1.48514
G1 X200.5000 Y40.5000 Z200.5000 A40.5000 F7.14285
G1 X270.0000 Y40.5000 Z270.0000 A40.5000 F2.15827
G0 X300.0000 Y100.0000 Z300.0000 A100.0000
G94
"""
    job_data["kerf_table"] = "lin"
    job_data["cut_speed"] = 150.0
    result = run(gcode, profile_data, job_data)
    outer = result.sections["root"].elements["outer"]
    assert outer.max_deviation == pytest.approx(1.2, abs=1e-3)
    assert outer.worst is not None
    assert outer.worst[0] == pytest.approx(150.0, abs=0.05)
    # No point of the simulated surface is further from the wire than the
    # largest half kerf 0.7.
    for chain in outer.surface:
        for p in chain:
            gap = min(
                float(np.hypot(*(p - q))) for q in result.sections["root"].trace[1:-1]
            )
            assert gap <= 0.7 + 1e-3 or abs(p[0] - 150) > 1


def test_short_edge_inside_the_slot_of_a_wider_corner_edge(
    profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    # Table "step": kerf 1.4 up to 75 mm/min, 1.0 from 125 mm/min. Top and
    # bottom at 50 mm/min (kerf 1.4, wire 0.7 outside: z = 40.7 and 19.3),
    # left and right sides at 150 mm/min (kerf 1.0, wire 0.5 outside: x = 99.5
    # and 200.5). Each corner of the wire path is the meeting point of the two
    # offset lines, so the program is exact. Top and bottom are each split
    # 0.3 mm from the right side, at x = 200.2:
    #   S (200.5, 40.7) -> (200.2, 40.7) -> (99.5, 40.7) -> (99.5, 19.3)
    #   -> (200.2, 19.3) -> (200.5, 19.3) -> S
    # Durations T = L / v, F = 1 / T:
    #   0.3 mm at 50:     T = 0.006    F = 166.667
    #   100.7 mm at 50:   T = 2.014    F = 0.496524
    #   21.4 mm at 150:   T = 0.142667 F = 7.00935
    #   lead-in / exit 69.5 mm at 150: F = 2.15827
    # The short bottom edge, moved 0.7 toward the material, is (200.2, 20) to
    # (200.5, 20). The right side's wire runs along x = 200.5 from z = 19.3 up
    # and melts everything within 0.5 of it: both those points are 0.3 and
    # 0.0 mm from it, so that slot removes the whole surface the short edge
    # would leave. The real corner is where the long bottom edge (z = 20) meets
    # the right side (x = 200): (200, 20), the target corner. The same at the
    # top right: (200, 40). So the deviation is 0.
    # Joining only consecutive moved edges instead steps from (200.5, 20) to
    # (200, 19.3), a point the right-hand slot has melted: 0.7 = half the
    # top and bottom kerf, at both right-hand corners.
    profile_data["kerf_tables"].append(
        {
            "name": "step",
            "description": "1.4 up to 75 mm/min, 1.0 from 125 mm/min",
            "points": [
                {"speed": 25.0, "kerf": 1.4},
                {"speed": 75.0, "kerf": 1.4},
                {"speed": 125.0, "kerf": 1.0},
                {"speed": 175.0, "kerf": 1.0},
            ],
        }
    )
    gcode = """\
G21
G54
G90
G93
G0 X300.0000 Y100.0000 Z300.0000 A100.0000
G0 X270.0000 Y40.7000 Z270.0000 A40.7000
G1 X200.5000 Y40.7000 Z200.5000 A40.7000 F2.15827
G1 X200.2000 Y40.7000 Z200.2000 A40.7000 F166.667
G1 X99.5000 Y40.7000 Z99.5000 A40.7000 F0.496524
G1 X99.5000 Y19.3000 Z99.5000 A19.3000 F7.00935
G1 X200.2000 Y19.3000 Z200.2000 A19.3000 F0.496524
G1 X200.5000 Y19.3000 Z200.5000 A19.3000 F166.667
G1 X200.5000 Y40.7000 Z200.5000 A40.7000 F7.00935
G1 X270.0000 Y40.7000 Z270.0000 A40.7000 F2.15827
G0 X300.0000 Y100.0000 Z300.0000 A100.0000
G94
"""
    job_data["kerf_table"] = "step"
    result = run(gcode, profile_data, job_data)
    for name in ("root", "tip"):
        outer = result.sections[name].elements["outer"]
        assert outer.max_deviation < 1e-6, (name, outer.max_deviation, outer.worst)


# ---------------------------------------------------------------- acceptance: corruption


def test_axis_scaled_by_one_percent_fails_and_names_sections(
    tapered_gcode: str, profile_data: dict[str, Any], tapered_job_data: dict[str, Any]
) -> None:
    # Axis 1 x 1.01. At the root plane x = 0.9 a1 + 0.1 a3, so x moves by
    # 0.9 * 0.01 * a1: at the TE (a1 = 206.75) 1.86075, at the LE (a1 = 99.5)
    # 0.8955. The trace stays a rectangle, so the worst deviation is the TE
    # face: 1.86075. At the tip x = 0.1 a1 + 0.9 a3: 0.1 * 0.01 * 206.75 = 0.20675.
    result = run(scale_axis(tapered_gcode, "X", 1.01), profile_data, tapered_job_data)
    assert not result.passed
    assert result.sections["root"].max_deviation == pytest.approx(1.86075, abs=1e-3)
    assert result.sections["tip"].max_deviation == pytest.approx(0.20675, abs=1e-3)
    assert set(result.failed_sections) == {"root", "tip"}
    assert "root" in result.summary() and "tip" in result.summary()


def test_vertical_axis_scaled_fails(
    tapered_gcode: str, profile_data: dict[str, Any], tapered_job_data: dict[str, Any]
) -> None:
    # Axis 4 x 1.01. Tip plane: z moves by 0.9 * 0.01 * a4, up to
    # 0.9 * 0.405 = 0.3645 at the top edge (a4 = 40.5). Root: 0.1 * 0.405 = 0.0405,
    # below the 0.05 tolerance, so only the tip fails.
    result = run(scale_axis(tapered_gcode, "A", 1.01), profile_data, tapered_job_data)
    assert result.failed_sections == ["tip"]
    assert result.sections["tip"].max_deviation == pytest.approx(0.3645, abs=1e-3)
    assert result.sections["root"].max_deviation == pytest.approx(0.0405, abs=1e-3)


# ---------------------------------------------------------------- operator planes


def test_operator_plane_trace(
    tapered_gcode: str, profile_data: dict[str, Any], tapered_job_data: dict[str, Any]
) -> None:
    # y = 500 is half way between root and tip: S = ((200.5 + 150.5)/2, 40.5)
    result = run(tapered_gcode, profile_data, tapered_job_data, planes=(500.0,))
    trace = result.plane_traces[500.0]
    # trace[0] is the start of the first feed move (the entry), trace[1] is S.
    assert trace[0] == pytest.approx((270.0, 40.5))
    assert trace[1] == pytest.approx((175.5, 40.5))
    assert trace[3] == pytest.approx((99.5, 24.5))  # (19.5 + 29.5) / 2


# ---------------------------------------------------------------- checks


def test_all_checks_pass_on_good_program(
    tapered_gcode: str, profile_data: dict[str, Any], tapered_job_data: dict[str, Any]
) -> None:
    result = run(tapered_gcode, profile_data, tapered_job_data)
    for c in result.checks:
        assert c.passed, (c.name, c.messages)


def test_soft_limit(
    straight_gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    # Safe position X650 > axis 1 max 600 by 50 mm, on lines 6 and 14.
    gcode = straight_gcode.replace("G0 X300.0000", "G0 X650.0000")
    passed, text = check(run(gcode, profile_data, job_data), "soft_limits")
    assert not passed
    assert "line 6" in text and "axis 1 (X)" in text and "50.000" in text


def test_wire_angle_limit(
    tapered_gcode: str, profile_data: dict[str, Any], tapered_job_data: dict[str, Any]
) -> None:
    # 3.647 degrees at the TE corners, limit set to 3.
    profile_data["limits"]["max_wire_angle"] = 3.0
    passed, text = check(run(tapered_gcode, profile_data, tapered_job_data), "wire_angle")
    assert not passed
    assert "3.647" in text


def test_speed_ratio_limit_exempts_lead_in(
    tapered_gcode: str, profile_data: dict[str, Any], tapered_job_data: dict[str, Any]
) -> None:
    # Long edges 1.980 and short edges 1.909 exceed 1.8; lead-in/exit 1.719 does
    # not, and would be exempt anyway. Limit 1.95 catches only the long edges
    # (lines 9 and 11).
    profile_data["limits"]["max_speed_ratio"] = 1.95
    passed, text = check(run(tapered_gcode, profile_data, tapered_job_data), "speed_ratio")
    assert not passed
    assert "line 9" in text and "line 11" in text
    assert "line 10" not in text and "line 8" not in text


def test_axis_rate_limit(
    straight_gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    # Long edge 101 mm in T = 1/19.8020 min -> 2000 mm/min on axes 1 and 3.
    gcode = straight_gcode.replace("A40.5000 F0.990099", "A40.5000 F19.8020", 1)
    passed, text = check(run(gcode, profile_data, job_data), "axis_rate")
    assert not passed
    assert "axis 1 (X)" in text and "axis 3 (Z)" in text and "2000.0" in text


def test_cut_speed_check(
    straight_gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    # Same edge at F1.98020: 101 * 1.98020 = 200 mm/min, job says 100.
    gcode = straight_gcode.replace("A40.5000 F0.990099", "A40.5000 F1.98020", 1)
    passed, text = check(run(gcode, profile_data, job_data), "cut_speed")
    assert not passed
    assert "line 9" in text


def test_wire_must_start_outside_block(
    straight_gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    # Entry moved to x = 240 < 250 (block front face): the wire starts and
    # ends inside the block. The rapid from (300, 100) to (240, 40.5) crosses
    # x = 250 at t = 50/60, where z = 100 - (5/6) * 59.5 = 50.4 < 60: also a hit.
    gcode = straight_gcode.replace("X270.0000", "X240.0000").replace("Z270.0000", "Z240.0000")
    result = run(gcode, profile_data, job_data)
    passed, text = check(result, "start_end_outside")
    assert not passed
    assert "start" in text and "end" in text
    assert not check(result, "rapids_clear")[0]


def test_rapid_through_block(
    straight_gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    # Safe position (150, 100) is above the block, but the rapid down to the
    # entry (270, 40.5) crosses x = 250 at t = 100/120 where z = 50.4 < 60.
    gcode = straight_gcode.replace(
        "G0 X300.0000 Y100.0000 Z300.0000 A100.0000",
        "G0 X150.0000 Y100.0000 Z150.0000 A100.0000",
    )
    result = run(gcode, profile_data, job_data)
    passed, text = check(result, "rapids_clear")
    assert not passed
    assert "line 7" in text
    assert check(result, "start_end_outside")[0]


def test_lead_in_through_part(
    straight_gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    # A lead-in that enters at mid height z = 30 and climbs to S crosses the
    # part (x 100..200, z 20..40) on its way.
    gcode = straight_gcode.replace(
        "G0 X270.0000 Y40.5000 Z270.0000 A40.5000",
        "G0 X270.0000 Y30.0000 Z270.0000 A30.0000",
    )
    gcode = gcode.replace(
        "G1 X200.5000 Y40.5000 Z200.5000 A40.5000 F1.43885",
        "G1 X150.0000 Y30.0000 Z150.0000 A30.0000 F0.833333\n"
        "G1 X200.5000 Y40.5000 Z200.5000 A40.5000 F1.0",
    )
    result = run(gcode, profile_data, job_data)
    assert not result.passed


def test_mirror(
    tapered_gcode: str, profile_data: dict[str, Any], tapered_job_data: dict[str, Any]
) -> None:
    # Mirror: exchange axis 1 with 3 and 2 with 4 on every line.
    swapped = re.sub(
        r"X(\S+) Y(\S+) Z(\S+) A(\S+)", r"X\3 Y\4 Z\1 A\2", tapered_gcode
    )
    tapered_job_data["mirror"] = True
    assert run(swapped, profile_data, tapered_job_data).passed
    assert not run(tapered_gcode, profile_data, tapered_job_data).passed


def test_bad_gcode_gives_failed_result(
    straight_gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    result = run(straight_gcode.replace("G94", "M5"), profile_data, job_data)
    assert not result.passed
    assert result.error is not None and "line 15" in result.error
