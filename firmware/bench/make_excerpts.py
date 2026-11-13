"""Write the bench-test G-code excerpts from a pinned version of tapered-spar.nc.

Usage:
    python firmware/bench/make_excerpts.py           write the excerpts next to this file
    python firmware/bench/make_excerpts.py --check   exit 1 if the committed excerpts differ

The source is read with "git show", so the source commit must be present in
the local repository. Each excerpt runs on its own: it starts with
G21 G54 G90 G93 and a G0 to its first position, and ends with G94.

Every position is shifted by a whole number of millimetres per axis so the
excerpt sits in a small envelope near the G54 zero. The F words are copied
character for character. In G93 an F word is the inverse of the move time, and
the shift leaves every move's length unchanged, so every move keeps its
commanded time. With a whole number of steps per mm, a whole-mm shift is a
whole number of steps, so each move also rounds to the same step counts as in
the source (grblHAL rounds absolute targets to steps, planner.c:443).
"""

import argparse
import math
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

SOURCE_COMMIT = "1769fcc254089afbb59bb641458879ada5ce1876"
SOURCE_PATH = "examples/gcode/tapered-spar.nc"
LETTERS = ("X", "Y", "Z", "A")
ENVELOPE_LOW = 10  # every shifted axis minimum lands in [10, 11) mm


@dataclass(frozen=True)
class Move:
    line: int  # line number in the source file
    code: str  # "G0" or "G1"
    pos: tuple[Decimal, ...]  # X Y Z A after the move
    f: str | None  # F word exactly as written, without the letter


def parse_moves(text: str) -> dict[int, Move]:
    """Return every G0/G1 line of a foamcam program, keyed by line number."""
    moves = {}
    for number, line in enumerate(text.splitlines(), 1):
        words = line.split()
        if not words or words[0] not in ("G0", "G1"):
            continue
        values = {w[0]: w[1:] for w in words[1:]}
        if set(values) - {*LETTERS, "F"} or not set(LETTERS) <= set(values):
            raise ValueError(f"source line {number}: expected all of X Y Z A: {line!r}")
        pos = tuple(Decimal(values[k]) for k in LETTERS)
        moves[number] = Move(number, words[0], pos, values.get("F"))
    return moves


@dataclass(frozen=True)
class Excerpt:
    name: str
    title: str
    purpose: str
    start_line: int  # source move whose end position is the excerpt start
    feed_lines: tuple[int, int]  # first and last source line, inclusive
    passes: int  # how many times the feed lines run, with a G0 back between


EXCERPTS = (
    Excerpt(
        name="excerpt-a-g93-with-g0.nc",
        title="Bench step 2a: G93 feed moves with G0 rapids between them",
        purpose=(
            "Source lines 1 to 30 hold one G0 approach and 16 G1 moves. The source has no G0 "
            "between feed moves, so the feed lines run twice with a G0 back to the start "
            "between the two passes."
        ),
        start_line=14,
        feed_lines=(15, 30),
        passes=2,
    ),
    Excerpt(
        name="excerpt-b-short-moves.nc",
        title="Bench step 2b: very short G93 moves",
        purpose=(
            "Source lines 60 to 70. Source line 67 is the shortest move in the source file: "
            "0.0031 mm on X, 0.0004 mm on Y, 0.0018 mm on Z, 0.0003 mm on A, in 1/50908.6 min "
            "(about 1.2 ms)."
        ),
        start_line=59,
        feed_lines=(60, 70),
        passes=1,
    ),
)


def fmt(value: Decimal) -> str:
    return f"{value:.4f}"


def move_line(code: str, pos: tuple[Decimal, ...], f: str | None) -> str:
    words = [code] + [f"{k}{fmt(v)}" for k, v in zip(LETTERS, pos, strict=True)]
    if f is not None:
        words.append(f"F{f}")
    return " ".join(words)


def build_excerpt(excerpt: Excerpt, moves: dict[int, Move]) -> str:
    first, last = excerpt.feed_lines
    feeds = [moves[n] for n in range(first, last + 1)]
    if any(m.code != "G1" or m.f is None for m in feeds):
        raise ValueError(f"{excerpt.name}: source lines {first}-{last} are not all G1 moves with F")
    start = moves[excerpt.start_line].pos
    points = [start] + [m.pos for m in feeds]
    lows = [min(p[i] for p in points) for i in range(4)]
    highs = [max(p[i] for p in points) for i in range(4)]
    shift = [Decimal(ENVELOPE_LOW - math.floor(low)) for low in lows]

    def moved(pos: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        return tuple(p + s for p, s in zip(pos, shift, strict=True))

    body = ["G21 G54 G90 G93", move_line("G0", moved(start), None)]
    for n in range(excerpt.passes):
        if n:
            body.append(move_line("G0", moved(start), None))
        body.extend(move_line("G1", moved(m.pos), m.f) for m in feeds)
    body.append("G94")

    total_min = sum(1 / Decimal(m.f) for m in feeds if m.f) * excerpt.passes
    header = [
        f"({excerpt.title})",
        f"(source: {SOURCE_PATH} at commit {SOURCE_COMMIT})",
        f"(source lines {first} to {last}; start position = end of source line {excerpt.start_line})",
        f"({excerpt.purpose})",
        "(Every position is the source position plus a whole-mm shift per axis. F words are)",
        "(copied unchanged, so every move keeps its length and its commanded time.)",
        "(shift added, mm: " + " ".join(f"{k}{int(s):+d}" for k, s in zip(LETTERS, shift, strict=True)) + ")",
        "(ENVELOPE, G54 mm, every position in this file lies inside:)",
    ]
    for k, low, high, s in zip(LETTERS, lows, highs, shift, strict=True):
        header.append(f"(  {k} {fmt(low + s)} to {fmt(high + s)})")
    header += [
        f"(feed moves: {len(feeds) * excerpt.passes}, commanded feed time {total_min * 60:.2f} s, plus rapids)",
        "(wire heat OFF, no foam. Generated by firmware/bench/make_excerpts.py; do not edit.)",
    ]
    return "\n".join(header + body) + "\n"


def source_text() -> str:
    return subprocess.run(
        ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def build_all(text: str) -> dict[str, str]:
    moves = parse_moves(text)
    return {e.name: build_excerpt(e, moves) for e in EXCERPTS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    args = parser.parse_args()
    stale = []
    for name, content in build_all(source_text()).items():
        path = HERE / name
        if args.check:
            if not path.exists() or path.read_text() != content:
                stale.append(name)
        else:
            path.write_text(content)
            print(f"wrote {path.relative_to(ROOT)}")
    for name in stale:
        print(f"STALE {name}")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
