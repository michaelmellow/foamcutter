"""Kerf calibration: speeds to test, the small programs of one slot, and saving the result.

The operator places the wire beside a scrap block and the Machine tab's Kerf tab runs,
for each speed, three relative programs (so jogging between them just moves the start):

1. **cut**: both axes of the cut direction together (the wire stays straight) through
   `depth` mm at the test speed, in G94 mm/min, wire hot;
2. then the tab waits until the operator has switched the heat off and the wire is cold;
3. **pull-out**: straight back out of the slot along the same line, cold, so the hot wire
   never runs through the slot twice (that would widen it), then a **step** to the next
   slot's start (up or down after a horizontal slot, away or toward after a vertical one).

Each slot's width, measured at both faces, is the kerf at its speed (machine-profile.md
§7: the full slot width). The speeds run from target / max_speed_ratio, the slowest the
slower foam face can go in a cut at that target, to a little above the target, spaced
evenly on a log scale, because the kerf lookup refuses speeds outside its table.
"""

import itertools
import math
import re
from dataclasses import dataclass

from foamcam.errors import FoamcamError
from foamcam.profile import load_profile_text

TOP_FACTOR = 1.2  # the fastest test speed, as a multiple of the target (headroom above it)
FACES_AGREE_MM = 0.05  # the two faces' widths should agree this well (first-cut-plan.md, cut 1)


@dataclass(frozen=True)
class Direction:
    """A cut direction: both axes of one kind move together, so the wire stays straight."""

    key: str  # "h+", "h-", "v+", "v-"
    vertical: bool
    sign: int

    def label(self, letters: tuple[str, ...]) -> str:
        pair = f"{letters[2]}{letters[0]}" if not self.vertical else f"{letters[3]}{letters[1]}"
        what = ("up" if self.sign > 0 else "down") if self.vertical else (
            "away from the operator" if self.sign > 0 else "toward the operator")
        return f"{pair}{'+' if self.sign > 0 else '-'}  ({what})"


DIRECTIONS = (Direction("h+", False, 1), Direction("h-", False, -1), Direction("v+", True, 1), Direction("v-", True, -1))
# where the next slot starts, by the kind of cut: across the cut direction
OFFSETS = {False: (("up", 1), ("down", -1)), True: (("away", 1), ("toward", -1))}


def direction(key: str) -> Direction:
    for d in DIRECTIONS:
        if d.key == key:
            return d
    raise ValueError(f"unknown cut direction {key!r}")


def calibration_speeds(target: float, ratio: float, count: int) -> list[float]:
    """`count` speeds from target / ratio to target * TOP_FACTOR, evenly spaced on a log
    scale, rounded to whole mm/min."""
    if target <= 0:
        raise ValueError("the target speed must be more than 0")
    if count < 2:
        raise ValueError("at least 2 slots are needed for a kerf table")
    lo, hi = target / max(ratio, 1.0), target * TOP_FACTOR
    if count == 1 or hi <= lo:
        return [round(target)]
    out = [round(lo * (hi / lo) ** (i / (count - 1))) for i in range(count)]
    for i in range(1, len(out)):  # rounding must not make two speeds equal
        out[i] = max(out[i], out[i - 1] + 1)
    return [float(s) for s in out]


def parse_speeds(text: str) -> list[float]:
    """Speeds typed as "50, 74, 109" (spaces or semicolons work too). Raises ValueError."""
    parts = [p for p in re.split(r"[,;\s]+", text.strip()) if p]
    speeds = [float(p) for p in parts]
    if len(speeds) < 2:
        raise ValueError("give at least 2 speeds")
    if any(s <= 0 for s in speeds):
        raise ValueError("every speed must be more than 0")
    if sorted(set(speeds)) != speeds:
        raise ValueError("the speeds must increase from left to right, without repeats")
    return speeds


def _axes(letters: tuple[str, ...], vertical: bool) -> tuple[str, str]:
    return (letters[1], letters[3]) if vertical else (letters[0], letters[2])


def cut_move(letters: tuple[str, ...], d: Direction, depth: float) -> dict[str, float]:
    """Relative move of the cut: both axes of the direction by the same amount."""
    a, b = _axes(letters, d.vertical)
    return {a: d.sign * depth, b: d.sign * depth}


def step_move(letters: tuple[str, ...], d: Direction, offset_sign: int, spacing: float) -> dict[str, float]:
    """Relative move to the next slot's start: across the cut direction."""
    a, b = _axes(letters, not d.vertical)
    return {a: offset_sign * spacing, b: offset_sign * spacing}


def _relative(moves: list[tuple[dict[str, float], float]], title: str) -> str:
    lines = [f"({title})", "G21", "G91", "G94"]
    for move, feed in moves:
        words = " ".join(f"{k}{v:.3f}" for k, v in move.items())
        lines.append(f"G1 {words} F{feed:g}")
    lines.append("G90")
    return "\n".join(lines) + "\n"


def slot_program(letters: tuple[str, ...], d: Direction, depth: float, speed: float) -> str:
    """The cut: straight into the foam by `depth` at `speed` (mm/min), relative to here."""
    return _relative([(cut_move(letters, d, depth), speed)], f"kerf slot at {speed:g} mm/min, {depth:g} mm deep")


def pullout_program(
    letters: tuple[str, ...], d: Direction, depth: float, feed: float, step: dict[str, float] | None
) -> str:
    """Cold wire back out along the slot, then (unless last) to the next slot's start."""
    back = {k: -v for k, v in cut_move(letters, d, depth).items()}
    moves = [(back, feed)] + ([(step, feed)] if step else [])
    return _relative(moves, "kerf slot: pull out cold" + (", then to the next start" if step else ""))


def mean_width(face1: float | None, face2: float | None) -> tuple[float | None, str]:
    """(kerf, note) from the widths at the two faces; one face alone is accepted."""
    given = [w for w in (face1, face2) if w is not None]
    if not given:
        return None, ""
    if any(w <= 0 for w in given):
        return None, "widths must be more than 0"
    note = ""
    if len(given) == 2 and abs(given[0] - given[1]) > FACES_AGREE_MM:
        note = f"faces differ by {abs(given[0] - given[1]):.2f} mm: wire not straight or not evenly hot?"
    return sum(given) / len(given), note


def table_yaml(name: str, description: str, points: list[tuple[float, float]], indent: str = "  ") -> str:
    """One kerf_tables entry as YAML text (machine-profile.md §7)."""
    def q(text: str) -> str:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

    rows = [f"{indent}- name: {q(name)}", f"{indent}  description: {q(description)}", f"{indent}  points:"]
    rows += [f"{indent}    - {{ speed: {s:.1f}, kerf: {k:.3f} }}" for s, k in points]
    return "\n".join(rows) + "\n"


def append_kerf_table(profile_text: str, name: str, description: str, points: list[tuple[float, float]]) -> str:
    """The profile text with a new kerf table added at the end of `kerf_tables`, keeping every
    comment and line as it was. Raises ValueError if the result is not a valid profile."""
    if not name.strip():
        raise ValueError("give the table a name")
    pts = sorted(points)
    if len(pts) < 2:
        raise ValueError("measure at least 2 slots")
    if any(b[0] <= a[0] for a, b in itertools.pairwise(pts)):
        raise ValueError("two measured slots have the same speed")
    lines = profile_text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if re.match(r"kerf_tables\s*:", line)), None)
    if start is None:
        raise ValueError("the profile has no kerf_tables section")
    end = len(lines)
    indent: str | None = None  # of the existing list items, so the new one lines up
    for i in range(start + 1, len(lines)):
        line = lines[i]
        m = re.match(r"([ \t]*)- ", line)
        if indent is None and m:
            indent = m.group(1)
        if line.strip() and not line.startswith((" ", "\t", "#", "-")):
            end = i  # the next top-level key
            break
    # blank lines and top-level comments just before the next key belong to that key
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
        end -= 1
    if end > 0 and not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    text = "".join(lines[:end]) + table_yaml(name, description, pts, indent or "  ") + "".join(lines[end:])
    try:
        profile = load_profile_text(text)
    except FoamcamError as error:
        raise ValueError(f"the profile would not be valid: {error}") from error
    if name not in profile.kerf_tables:
        raise ValueError("the new table did not load")
    return text


def slot_end_inside(
    wpos: dict[str, float], moves: list[dict[str, float]], travel: dict[str, tuple[float, float]] | None
) -> str:
    """"" if the wire stays inside the travel through the given relative moves, else why not."""
    if travel is None:
        return ""
    pos = dict(wpos)
    for move in moves:
        for letter, d in move.items():
            pos[letter] = pos.get(letter, 0.0) + d
            if letter in travel:
                lo, hi = travel[letter]
                if not lo - 1e-6 <= pos[letter] <= hi + 1e-6:
                    return f"{letter} would go to {pos[letter]:.1f}, outside its travel {lo:g} to {hi:g}"
    return ""


def straight_wire(wpos: dict[str, float], letters: tuple[str, ...], tol: float = 0.05) -> str:
    """"" if both towers are at the same position (straight wire), else the difference."""
    dx = wpos.get(letters[0], 0.0) - wpos.get(letters[2], 0.0)
    dz = wpos.get(letters[1], 0.0) - wpos.get(letters[3], 0.0)
    if math.hypot(dx, dz) <= tol:
        return ""
    return f"the towers differ by {dx:+.2f} mm horizontally and {dz:+.2f} mm vertically"
