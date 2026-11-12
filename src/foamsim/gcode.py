"""Parser for the accepted G-code subset (Brief 5 task 1).

Accepted: G0, G1, G4, G21, G54, G90, G91, G93, G94, the four axis letters of
the profile, F, P (with G4 only), and comment lines `( ... )`. Anything else
is rejected with the line number.

Interpretation (controller behaviour assumed, see the Brief 5 report):

- G0 and G1 are straight lines in axis space; positions are G54 mm.
- G93: F on every G1 line; the move takes 1/F minutes.
- G94: F is modal, mm/min, along the length of the move in all four axes.
- G4 P: dwell of P seconds.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .errors import GCodeError

Axes = tuple[float, float, float, float]

_WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
_SUPPORTED_G = {0, 1, 4, 21, 54, 90, 91, 93, 94}
_HINTS = {
    "M": "M words (spindle, coolant, program end) are not accepted",
    "N": "N line numbers are not accepted",
    "S": "S words (spindle speed) are not accepted",
    "T": "T words (tool) are not accepted",
}


@dataclass(frozen=True)
class Motion:
    kind: str  # "rapid", "feed" or "dwell"
    line: int
    start: Axes
    end: Axes
    duration: float | None  # minutes; None for rapids (set by the controller)
    start_known: bool = True  # False for the first motion of a program


@dataclass
class Program:
    motions: list[Motion] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    final_feed_mode: str | None = None


def _words(text: str, number: int) -> list[tuple[str, str]]:
    words: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        if text[pos] in "()":
            raise GCodeError(number, "a comment must be a whole line ( ... ) with no ( ) inside")
        m = _WORD.match(text, pos)
        if m is None:
            if text[pos].isalpha():
                raise GCodeError(number, f"word {text[pos:pos + 8]!r} has no valid number")
            raise GCodeError(number, f"unexpected character {text[pos]!r}")
        end = m.end()
        if end < len(text) and text[end] in "eE" and end + 1 < len(text) \
                and (text[end + 1].isdigit() or text[end + 1] in "+-"):
            raise GCodeError(number, f"number with an exponent in {text[pos:end + 4]!r}")
        words.append((m.group(1).upper(), m.group(2)))
        pos = end
    return words


def parse_gcode(text: str, letters: tuple[str, str, str, str]) -> Program:
    """Parse `text`. `letters` are the G-code letters of axes 1 to 4."""
    program = Program()
    units = wcs = distance = feed_mode = None
    motion_mode: int | None = None
    feed: float | None = None  # modal F in G94
    position: list[float] | None = None

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("(") and line.endswith(")") and "(" not in line[1:-1] \
                and ")" not in line[1:-1]:
            program.comments.append(line[1:-1])
            continue

        g_codes: list[int] = []
        axis_words: dict[int, float] = {}
        f_word: float | None = None
        p_word: float | None = None
        for letter, value in _words(line, number):
            if letter == "G":
                if "." in value or not value.lstrip("+").isdigit():
                    raise GCodeError(number, f"G{value} is not supported")
                code = int(value)
                if code not in _SUPPORTED_G:
                    raise GCodeError(number, f"G{code} is not supported")
                if code in g_codes:
                    raise GCodeError(number, f"G{code} appears twice")
                g_codes.append(code)
            elif letter in letters:
                axis = letters.index(letter)
                if axis in axis_words:
                    raise GCodeError(number, f"axis word {letter} appears twice")
                axis_words[axis] = float(value)
            elif letter == "F":
                if f_word is not None:
                    raise GCodeError(number, "F appears twice")
                f_word = float(value)
            elif letter == "P":
                if p_word is not None:
                    raise GCodeError(number, "P appears twice")
                p_word = float(value)
            elif letter in _HINTS:
                raise GCodeError(number, _HINTS[letter])
            else:
                raise GCodeError(
                    number,
                    f"word {letter}{value}: {letter} is not an axis letter of this profile "
                    f"({' '.join(letters)}) nor F or P",
                )

        motions = [g for g in g_codes if g in (0, 1, 4)]
        if len(motions) > 1:
            raise GCodeError(number, "more than one motion command (G0, G1, G4) on one line")
        if 90 in g_codes and 91 in g_codes:
            raise GCodeError(number, "G90 and G91 on one line")
        if 93 in g_codes and 94 in g_codes:
            raise GCodeError(number, "G93 and G94 on one line")
        for g in g_codes:
            if g == 21:
                units = "G21"
            elif g == 54:
                wcs = "G54"
            elif g in (90, 91):
                distance = f"G{g}"
            elif g in (93, 94):
                feed_mode = f"G{g}"
                feed = None

        if 4 in motions:
            if axis_words:
                raise GCodeError(number, "G4 cannot have axis words")
            if p_word is None or p_word < 0:
                raise GCodeError(number, "G4 needs P (dwell in seconds, >= 0)")
            if f_word is not None:
                raise GCodeError(number, "F on a G4 line")
            if position is None:
                raise GCodeError(number, "dwell before the first motion")
            here: Axes = (position[0], position[1], position[2], position[3])
            program.motions.append(Motion("dwell", number, here, here, p_word / 60.0))
            continue
        if p_word is not None:
            raise GCodeError(number, "P is only accepted with G4")
        if motions:
            motion_mode = motions[0]
        if f_word is not None:
            if f_word <= 0:
                raise GCodeError(number, "F must be > 0")
            if feed_mode is None:
                raise GCodeError(number, "F before the feed mode is set (G93 or G94)")
            feed = f_word
        if not axis_words:
            continue

        if motion_mode is None:
            raise GCodeError(number, "axis words with no motion mode (G0 or G1) set")
        if units is None:
            raise GCodeError(number, "units not set: write G21 before the first motion")
        if wcs is None:
            raise GCodeError(number, "work coordinates not set: write G54 before the first motion")
        if distance is None:
            raise GCodeError(number, "distance mode not set: write G90 before the first motion")
        if position is None:
            if distance != "G90" or len(axis_words) != 4:
                raise GCodeError(
                    number,
                    "the first motion must set all four axes in absolute mode (G90): "
                    "the position before it is unknown",
                )
            if motion_mode != 0:
                raise GCodeError(
                    number, "the first motion must be a rapid (G0): the position before it "
                    "is unknown"
                )
            position = [0.0, 0.0, 0.0, 0.0]
            start: Axes | None = None
        else:
            start = (position[0], position[1], position[2], position[3])
        new = list(position)
        for axis, amount in axis_words.items():
            new[axis] = amount if distance == "G90" else position[axis] + amount
        end: Axes = (new[0], new[1], new[2], new[3])

        if motion_mode == 0:
            if f_word is not None and feed_mode == "G93":
                raise GCodeError(number, "F on a G0 line in G93 mode")
            program.motions.append(
                Motion("rapid", number, start or end, end, None, start is not None)
            )
        else:
            assert start is not None
            begin = start
            if feed_mode is None:
                raise GCodeError(number, "feed mode not set: write G93 (or G94) before the first G1")
            if feed_mode == "G93":
                if f_word is None:
                    raise GCodeError(number, "G93 needs an F word on every G1 line")
                duration = 1.0 / f_word
            else:
                if feed is None:
                    raise GCodeError(number, "G1 in G94 mode with no F set")
                duration = math.dist(begin, end) / feed
            program.motions.append(Motion("feed", number, begin, end, duration))
        position = new

    program.final_feed_mode = feed_mode
    return program
