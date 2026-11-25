"""The straight cut across the table (crosscut.py)."""

from pathlib import Path

import pytest

from foamcam.profile import load_profile
from foamdxf.crosscut import away_from_start, cut_program, plan_cross_cut, start_program

PROFILE = load_profile(Path(__file__).resolve().parents[2] / "examples" / "profiles" / "real-machine.yaml")
LETTERS = PROFILE.letters  # Z A X Y


def test_cut_spans_the_shared_horizontal_travel() -> None:
    cut = plan_cross_cut(PROFILE, 35.0, 200.0)
    a1, _, a3, _ = PROFILE.axes
    assert (cut.start_x, cut.end_x) == (max(a1.min, a3.min), min(a1.max, a3.max))
    assert cut.start_x < cut.end_x  # the operator side (homed, low x) first
    assert cut.minutes == pytest.approx((cut.end_x - cut.start_x) / 200.0)


@pytest.mark.parametrize(("z", "speed", "message"), [
    (-1.0, 200.0, "outside the vertical travel"),
    (500.0, 200.0, "outside the vertical travel"),
    (35.0, 0.0, "speed must be more than 0"),
    (35.0, 1e6, "speed must be more than 0"),
])
def test_bad_height_or_speed_is_refused(z: float, speed: float, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        plan_cross_cut(PROFILE, z, speed)


def test_programs_keep_the_wire_straight() -> None:
    cut = plan_cross_cut(PROFILE, 35.0, 200.0)
    start = start_program(LETTERS, cut).splitlines()
    assert start[-2:] == ["G0 Z0.000 X0.000", "G0 A35.000 Y35.000"]  # across at its height, then to z
    assert "G90" in start
    feed = cut_program(LETTERS, cut).splitlines()
    assert "G90" in feed and "G94" in feed
    assert feed[-1] == "G1 Z300.000 X300.000 A35.000 Y35.000 F200"


def test_cut_needs_the_wire_at_the_start() -> None:
    cut = plan_cross_cut(PROFILE, 35.0, 200.0)
    there = {"Z": 0.0, "X": 0.0, "A": 35.0, "Y": 35.0}
    assert away_from_start(there, LETTERS, cut) == ""
    assert away_from_start(there | {"Y": 36.0}, LETTERS, cut).startswith("the wire is at")
    assert away_from_start(there | {"X": 0.04}, LETTERS, cut) == ""
