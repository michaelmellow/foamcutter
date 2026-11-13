"""Brief 5 acceptance on the Brief 3 worked examples (examples/gcode, examples/jobs).

The first programs missed the 0.05 mm deviation limit because of the CAM
method (docs/reports/brief-5-report.md), and that test was a strict expected
failure. The regenerated programs meet it (docs/reports/brief-5-recheck-report.md),
so it is now a plain test.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest

from foamsim.job import load_job
from foamsim.profile import load_profile
from foamsim.sim import SimResult, simulate

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = ("straight-wing", "tapered-spar", "tapered-swept-washout")


def gcode(name: str) -> str:
    return (REPO / "examples/gcode" / f"{name}.nc").read_text()


@cache
def result(name: str, letter: str = "", factor: float = 1.0) -> SimResult:
    text = gcode(name)
    if letter:
        text = re.sub(
            rf"\b{letter}(-?\d+\.\d+)",
            lambda m: f"{letter}{float(m.group(1)) * factor:.4f}",
            text,
        )
    profile = load_profile(REPO / "examples/profiles/sample-machine.yaml")
    return simulate(text, profile, load_job(REPO / "examples/jobs" / f"{name}.yaml"))


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_checks_pass(name: str) -> None:
    r = result(name)
    assert r.error is None
    for c in r.checks:
        assert c.passed, (c.name, c.messages)
    # Kerf lookups all inside the table; every element was found.
    for s in r.sections.values():
        assert s.error is None
        assert not s.problems
        assert all(e.edges > 0 for e in s.elements.values())


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_deviation_below_tolerance(name: str) -> None:
    r = result(name)
    for s in r.sections.values():
        assert s.max_deviation < 0.05, (s.name, s.max_deviation)


@pytest.mark.parametrize("name", EXAMPLES)
@pytest.mark.parametrize(("letter", "near"), [("X", "root"), ("Y", "root"), ("Z", "tip"),
                                               ("A", "tip")])
def test_example_axis_scaled_one_percent_fails(name: str, letter: str, near: str) -> None:
    # Axes 1 and 2 drive the left tower, so a 1 % error moves the root plane
    # (y = 150 of 900) about five times as much as the tip plane (y = 750):
    # weights 1 - 150/900 = 0.83 and 1 - 750/900 = 0.17. Axes 3 and 4 the other
    # way round. The report must fail and name that section, with the larger
    # deviation, well above the uncorrupted value.
    clean = result(name)
    bad = result(name, letter, 1.01)
    assert not bad.passed
    assert near in bad.failed_sections
    far = "tip" if near == "root" else "root"
    assert bad.sections[near].max_deviation > bad.sections[far].max_deviation
    assert bad.sections[near].max_deviation > 2 * clean.sections[near].max_deviation
    assert near in bad.summary()
