"""Tests for the bench-test excerpts and the baseline settings file.

Run with: python -m pytest firmware
"""

import importlib.util
import itertools
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
BENCH = HERE / "bench"


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mx = load("fw_make_excerpts", BENCH / "make_excerpts.py")
st = load("fw_settings", HERE / "settings.py")

EXCERPT_A = BENCH / "excerpt-a-g93-with-g0.nc"
EXCERPT_B = BENCH / "excerpt-b-short-moves.nc"


def code_lines(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if line and not line.startswith("(")]


def moves(path: Path) -> list[tuple[str, tuple[Decimal, ...], str | None]]:
    out = []
    for line in code_lines(path):
        words = line.split()
        if words[0] in ("G0", "G1"):
            values = {w[0]: w[1:] for w in words[1:]}
            out.append((words[0], tuple(Decimal(values[k]) for k in "XYZA"), values.get("F")))
    return out


def stated_envelope(path: Path) -> dict[str, tuple[Decimal, Decimal]]:
    found = re.findall(r"^\(  ([XYZA]) (\S+) to (\S+)\)$", path.read_text(), re.MULTILINE)
    return {k: (Decimal(lo), Decimal(hi)) for k, lo, hi in found}


def source_available() -> bool:
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{mx.SOURCE_COMMIT}:{mx.SOURCE_PATH}"],
        cwd=mx.ROOT,
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


needs_source = pytest.mark.skipif(
    not source_available(), reason=f"source commit {mx.SOURCE_COMMIT[:12]} is not in this clone"
)


@pytest.mark.parametrize("path", [EXCERPT_A, EXCERPT_B])
def test_excerpt_runs_on_its_own(path: Path) -> None:
    lines = code_lines(path)
    assert lines[0] == "G21 G54 G90 G93"
    assert re.fullmatch(r"G0 X\S+ Y\S+ Z\S+ A\S+", lines[1])
    assert lines[-1] == "G94"
    # In G93 every G1 needs its own F word (gcode.c:2339-2344).
    assert all(f is not None for code, _, f in moves(path) if code == "G1")
    assert all(f is None for code, _, f in moves(path) if code == "G0")


@pytest.mark.parametrize("path", [EXCERPT_A, EXCERPT_B])
def test_excerpt_records_source_commit(path: Path) -> None:
    assert f"at commit {mx.SOURCE_COMMIT}" in path.read_text()


@pytest.mark.parametrize("path", [EXCERPT_A, EXCERPT_B])
def test_every_position_is_inside_the_stated_envelope(path: Path) -> None:
    envelope = stated_envelope(path)
    assert sorted(envelope) == ["A", "X", "Y", "Z"]
    for _, pos, _ in moves(path):
        for k, value in zip("XYZA", pos, strict=True):
            lo, hi = envelope[k]
            assert lo <= value <= hi
    # The envelope is tight: its bounds are reached.
    for i, k in enumerate("XYZA"):
        values = [pos[i] for _, pos, _ in moves(path)]
        assert (min(values), max(values)) == envelope[k]
        assert 10 <= min(values) < 11


def test_excerpt_a_has_a_rapid_between_feed_moves() -> None:
    codes = [code for code, _, _ in moves(EXCERPT_A)]
    sequence = "".join("R" if c == "G0" else "F" for c in codes)
    assert re.fullmatch(r"RF+RF+", sequence)


def test_excerpt_b_contains_the_short_move() -> None:
    m = moves(EXCERPT_B)
    deltas = [max(abs(a - b) for a, b in zip(p, q, strict=True)) for (_, p, _), (_, q, _) in itertools.pairwise(m)]
    assert min(deltas) == Decimal("0.0031")


@needs_source
def test_committed_excerpts_match_the_generator() -> None:
    for name, content in mx.build_all(mx.source_text()).items():
        assert (BENCH / name).read_text() == content, f"{name} is stale: run make_excerpts.py"


@needs_source
@pytest.mark.parametrize("excerpt", mx.EXCERPTS, ids=lambda e: e.name)
def test_moves_keep_source_lengths_and_times(excerpt: Any) -> None:
    source = mx.parse_moves(mx.source_text())
    first, last = excerpt.feed_lines
    expected = [source[n] for n in range(excerpt.start_line, last + 1)]
    feed = [m for m in moves(BENCH / excerpt.name) if m[0] == "G1"]
    starts = [m for m in moves(BENCH / excerpt.name) if m[0] == "G0"]
    assert len(starts) == excerpt.passes
    for n in range(excerpt.passes):
        got = [starts[n]] + feed[n * (last - first + 1) : (n + 1) * (last - first + 1)]
        for (_, p0, _), (_, p1, f), s0, s1 in zip(got, got[1:], expected, expected[1:]):
            assert f == s1.f  # F word copied character for character
            assert all(a - b == c - d for a, b, c, d in zip(p1, p0, s1.pos, s0.pos, strict=True))
        shift = {a - b for a, b in zip(got[0][1], expected[0].pos, strict=True)}
        assert all(s == s.to_integral_value() for s in shift)


@needs_source
def test_shortest_source_move_is_the_one_in_excerpt_b() -> None:
    source = mx.parse_moves(mx.source_text())
    lines = sorted(source)
    lengths = {
        b: max(abs(p - q) for p, q in zip(source[b].pos, source[a].pos, strict=True))
        for a, b in itertools.pairwise(lines)
        if source[b].code == "G1"
    }
    assert min(lengths, key=lambda n: lengths[n]) == 67
    deltas = [abs(p - q) for p, q in zip(source[67].pos, source[66].pos, strict=True)]
    assert deltas == [Decimal(d) for d in ("0.0031", "0.0004", "0.0018", "0.0003")]
    assert source[67].f == "50908.6"


@needs_source
def test_line_67_steps_at_400_steps_per_mm() -> None:
    # Report claim: at 400 steps/mm, source line 67 is 1 step on X and Z and 0 on
    # Y and A, so it is not dropped (planner.c:443 rounds absolute targets).
    source = mx.parse_moves(mx.source_text())
    steps = [round(p * 400) - round(q * 400) for p, q in zip(source[67].pos, source[66].pos, strict=True)]
    assert [abs(s) for s in steps] == [1, 0, 1, 0]


def test_baseline_settings_file_is_valid() -> None:
    settings = st.parse(st.BASELINE.read_text())
    assert settings, "no settings found"
    numbers = [n for n, _ in settings]
    assert len(numbers) == len(set(numbers)), "a setting appears twice"
    for n in (338, 339, 140, 150, 200, 220, 22, 23, 376):
        assert n in numbers


def test_baseline_settings_match_the_documented_choices() -> None:
    values = dict(st.parse(st.BASELINE.read_text()))
    assert values[338] == "15"  # TMC2209 on all four axes
    assert values[339] == "15"  # sensorless homing on all four axes
    assert values[376] == "0"  # no rotary axes
    assert values[5] == "0"  # DIAG is active high, no inversion
    assert values[21] == "0"  # hard limits off: DIAG also flags driver errors
    for axis in range(4):
        assert values[100 + axis] == values[100]  # same lead and microsteps on all axes
        assert values[150 + axis] == "16"


def test_stripped_settings_have_no_comments() -> None:
    lines = st.strip(st.BASELINE.read_text()).splitlines()
    assert lines
    assert all(re.fullmatch(r"\$\d+=-?\d+(\.\d+)?", line) for line in lines)


def test_parse_rejects_comment_on_a_setting_line() -> None:
    with pytest.raises(ValueError, match="line 2"):
        st.parse("; ok\n$100=400 ; not allowed\n")


def test_steps_per_mm_formula() -> None:
    assert st.steps_per_mm(full_steps=200, microsteps=16, lead=8.0) == 400.0
    assert st.steps_per_mm(full_steps=200, microsteps=16, lead=2.0) == 1600.0
