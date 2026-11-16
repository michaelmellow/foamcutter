"""Kerf calibration logic (foamdxf.kerfcal): speeds, slot programs, saving to the profile."""

import itertools
from pathlib import Path

import pytest

from foamcam.profile import load_profile_text
from foamdxf.kerfcal import (
    append_kerf_table,
    calibration_speeds,
    cut_move,
    direction,
    mean_width,
    parse_speeds,
    pullout_program,
    slot_end_inside,
    slot_program,
    step_move,
    straight_wire,
)

REPO = Path(__file__).resolve().parents[2]
LETTERS = ("Z", "A", "X", "Y")  # the real profile: axes 1-4


def test_speeds_cover_the_slow_face_up_to_above_the_target() -> None:
    speeds = calibration_speeds(150, 3.0, 5)
    assert speeds == [50, 69, 95, 131, 180]  # 150 / 3 to 150 x 1.2, log spaced
    ratios = [b / a for a, b in itertools.pairwise(speeds)]
    assert max(ratios) - min(ratios) < 0.05
    assert calibration_speeds(10, 3.0, 8) == sorted(set(calibration_speeds(10, 3.0, 8)))  # no repeats after rounding
    with pytest.raises(ValueError):
        calibration_speeds(150, 3.0, 1)
    with pytest.raises(ValueError):
        calibration_speeds(0, 3.0, 5)


def test_parse_speeds() -> None:
    assert parse_speeds("50, 74;109 160") == [50, 74, 109, 160]
    for bad in ("50", "50, 40", "50, 50", "0, 10", "fast, slow"):
        with pytest.raises(ValueError):
            parse_speeds(bad)


def test_slot_programs_keep_the_wire_straight_and_come_back_out_cold() -> None:
    d = direction("h-")  # both horizontal axes toward the operator
    assert cut_move(LETTERS, d, 30) == {"Z": -30, "X": -30}
    assert step_move(LETTERS, d, 1, 10) == {"A": 10, "Y": 10}  # next slot higher
    assert step_move(LETTERS, direction("v-"), -1, 8) == {"Z": -8, "X": -8}  # vertical slot: next one closer
    cut = slot_program(LETTERS, d, 30, 74).splitlines()
    assert cut[1:] == ["G21", "G91", "G94", "G1 Z-30.000 X-30.000 F74", "G90"]
    pull = pullout_program(LETTERS, d, 30, 200, step_move(LETTERS, d, 1, 10)).splitlines()
    assert pull[1:] == ["G21", "G91", "G94", "G1 Z30.000 X30.000 F200", "G1 A10.000 Y10.000 F200", "G90"]
    last = pullout_program(LETTERS, d, 30, 200, None).splitlines()
    assert last[1:] == ["G21", "G91", "G94", "G1 Z30.000 X30.000 F200", "G90"]
    assert direction("v+").label(LETTERS).startswith("YA+")
    assert direction("h+").label(LETTERS).startswith("XZ+")


def test_checks_before_a_slot() -> None:
    travel = {"Z": (0.0, 300.0), "A": (0.0, 280.0), "X": (0.0, 300.0), "Y": (0.0, 280.0)}
    here = {"X": 20.0, "Y": 50.0, "Z": 20.0, "A": 50.0}
    assert straight_wire(here, LETTERS) == ""
    assert "differ" in straight_wire(here | {"X": 21.0}, LETTERS)
    assert slot_end_inside(here, [{"Z": -15, "X": -15}], travel) == ""
    assert "outside its travel" in slot_end_inside(here, [{"Z": -30, "X": -30}], travel)
    assert slot_end_inside(here, [{"Z": -30, "X": -30}], None) == ""


def test_mean_width_flags_faces_that_disagree() -> None:
    kerf, note = mean_width(1.80, 1.84)
    assert kerf == pytest.approx(1.82) and note == ""
    kerf, note = mean_width(1.0, 1.2)
    assert kerf == pytest.approx(1.1) and "differ" in note
    assert mean_width(None, 0.9) == (0.9, "")
    assert mean_width(None, None) == (None, "")


def test_new_table_is_appended_and_everything_else_kept() -> None:
    text = (REPO / "examples/profiles/real-machine.yaml").read_text(encoding="utf-8")
    new = append_kerf_table(text, "eps-20-wire-3A", 'EPS 20, "3 A"', [(95, 1.2), (50, 1.8), (180, 0.8)])
    assert new.startswith(text.rstrip("\n"))  # nothing before it changed
    profile = load_profile_text(new)
    table = profile.kerf_tables["eps-20-wire-3A"]
    assert table.points == ((50, 1.8), (95, 1.2), (180, 0.8))  # sorted by speed
    assert table.description == 'EPS 20, "3 A"'
    assert "sample-not-measured" in profile.kerf_tables
    with pytest.raises(ValueError, match="not be valid"):
        append_kerf_table(new, "eps-20-wire-3A", "", [(50, 1.8), (95, 1.2)])  # names must be unique
    with pytest.raises(ValueError, match="at least 2"):
        append_kerf_table(text, "one", "", [(50, 1.8)])


def test_new_table_goes_inside_kerf_tables_when_another_key_follows() -> None:
    text = (REPO / "examples/profiles/real-machine.yaml").read_text(encoding="utf-8")
    head, tables = text.split("kerf_tables:", 1)
    limits_start = head.index("limits:")
    limits_end = head.index("\n\n", limits_start)
    limits = head[limits_start:limits_end] + "\n"
    moved = head[:limits_start] + head[limits_end:] + "kerf_tables:" + tables.rstrip("\n") + "\n\n# limits last\n" + limits
    load_profile_text(moved)  # still a valid profile with kerf_tables before limits
    new = append_kerf_table(moved, "late", "", [(50, 1.8), (95, 1.2)])
    assert new.index('name: "late"') < new.index("# limits last")
    assert "late" in load_profile_text(new).kerf_tables
