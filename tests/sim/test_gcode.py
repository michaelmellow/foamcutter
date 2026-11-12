"""Brief 5 task 1: parse the G-code subset and reject anything else."""

from __future__ import annotations

import pytest

from foamsim.errors import GCodeError
from foamsim.gcode import parse_gcode

LETTERS = ("X", "Y", "Z", "A")
HEADER = "G21\nG54\nG90\nG93\n"


def test_straight_program_parses(straight_gcode: str) -> None:
    program = parse_gcode(straight_gcode, LETTERS)
    kinds = [m.kind for m in program.motions]
    assert kinds == ["rapid", "rapid"] + ["feed"] * 6 + ["rapid"]
    first_feed = program.motions[2]
    assert first_feed.start == (270.0, 40.5, 270.0, 40.5)
    assert first_feed.end == (200.5, 40.5, 200.5, 40.5)
    # F1.43885 in G93: T = 1 / 1.43885 min
    assert first_feed.duration == pytest.approx(1 / 1.43885)
    assert first_feed.line == 8
    assert program.final_feed_mode == "G94"


def test_letters_come_from_the_profile() -> None:
    text = "G21 G54 G90 G93\nG0 U1 V2 W3 B4\nG1 U2 V2 W3 B4 F10\n"
    program = parse_gcode(text, ("U", "V", "W", "B"))
    assert program.motions[1].end == (2.0, 2.0, 3.0, 4.0)
    with pytest.raises(GCodeError, match="line 2"):
        parse_gcode(text, LETTERS)


def test_relative_mode_g91() -> None:
    # Absolute start (10, 20, 30, 40); G91 move by (+5, -5, 0, +1) -> (15, 15, 30, 41)
    text = HEADER + "G0 X10 Y20 Z30 A40\nG91\nG1 X5 Y-5 A1 F2\nG90\nG1 X0 F4\n"
    program = parse_gcode(text, LETTERS)
    assert program.motions[1].end == (15.0, 15.0, 30.0, 41.0)
    assert program.motions[1].duration == pytest.approx(0.5)
    assert program.motions[2].end == (0.0, 15.0, 30.0, 41.0)


def test_dwell_g4_seconds() -> None:
    # G4 P1.5: 1.5 s = 0.025 min, no motion.
    text = HEADER + "G0 X0 Y0 Z0 A0\nG4 P1.5\n"
    program = parse_gcode(text, LETTERS)
    dwell = program.motions[1]
    assert dwell.kind == "dwell"
    assert dwell.duration == pytest.approx(0.025)
    assert dwell.start == dwell.end


def test_g94_feed_uses_length_of_all_four_axes() -> None:
    # (0,0,0,0) -> (30, 40, 0, 0) at F100 mm/min: length 50 -> 0.5 min.
    text = "G21\nG54\nG90\nG94\nG0 X0 Y0 Z0 A0\nG1 X30 Y40 F100\n"
    program = parse_gcode(text, LETTERS)
    assert program.motions[1].duration == pytest.approx(0.5)


def test_compact_words_and_modal_motion() -> None:
    text = HEADER + "G00X1Y2Z3A4\nG01X2F1\nX3F1\n"
    program = parse_gcode(text, LETTERS)
    assert [m.kind for m in program.motions] == ["rapid", "feed", "feed"]
    assert program.motions[2].end[0] == 3.0


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("M3", "M"),
        ("G2 X1 Y1 Z1 A1", "G2"),
        ("G55", "G55"),
        ("G20", "G20"),
        ("G92 X0", "G92"),
        ("N10 G1 X1 F1", "N"),
        ("S1000", "S"),
        ("T1", "T"),
        ("; comment", "unexpected"),
        ("G1 B1 F1", "B"),
        ("G1 X1", "F"),  # G93 needs F on every G1 line
        ("G1 X1 X2 F1", "twice"),
        ("G0 G1 X1 F1", "motion"),
        ("G4", "P"),
        ("G4 P1 X3", "G4"),
        ("(nested (comment))", "comment"),
        ("G1 X1e3 F1", "number"),
        ("%", "unexpected"),
    ],
)
def test_rejects_anything_else(line: str, reason: str) -> None:
    text = HEADER + "G0 X0 Y0 Z0 A0\n" + line + "\n"
    with pytest.raises(GCodeError, match=reason) as info:
        parse_gcode(text, LETTERS)
    assert "line 6" in str(info.value)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("G54\nG90\nG93\nG0 X0 Y0 Z0 A0\n", "G21"),
        ("G21\nG90\nG93\nG0 X0 Y0 Z0 A0\n", "G54"),
        ("G21\nG54\nG93\nG0 X0 Y0 Z0 A0\n", "G90"),
        ("G21\nG54\nG90\nG0 X0 Y0 Z0 A0\nG1 X1 F1\n", "G93"),
        ("G21\nG54\nG90\nG0 X0 Y0\n", "all four"),
        ("G21\nG54\nG91\nG0 X0 Y0 Z0 A0\n", "all four"),
    ],
)
def test_rejects_unknown_modal_state(text: str, reason: str) -> None:
    with pytest.raises(GCodeError, match=reason):
        parse_gcode(text, LETTERS)


def test_comment_lines_are_kept_out_of_motions() -> None:
    text = "(hello)\n" + HEADER + "(between)\nG0 X0 Y0 Z0 A0\n"
    program = parse_gcode(text, LETTERS)
    assert len(program.motions) == 1
    assert program.comments == ["hello", "between"]


def test_zero_feed_rejected() -> None:
    with pytest.raises(GCodeError, match="F"):
        parse_gcode(HEADER + "G0 X0 Y0 Z0 A0\nG1 X1 F0\n", LETTERS)
