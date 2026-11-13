"""The table top is at z = table_z of the machine profile (machine-profile.md §3.4).

The block occupies z from table_z to table_z + height (job-file.md §5). Raising
the table and everything on it by the same amount must move the wire by that
amount vertically and change nothing else.
"""

import dataclasses
import re
from pathlib import Path

import pytest

from foamcam import CamError, ValidationError, generate, load_job, load_profile
from foamcam.job import Job
from foamcam.kerftest import KerfTest, kerf_test_gcode
from foamcam.profile import Profile

ROOT = Path(__file__).resolve().parents[2]
JOBS = sorted((ROOT / "examples" / "jobs").glob("*.yaml"))
PROFILE = ROOT / "examples" / "profiles" / "sample-machine.yaml"

WORD = re.compile(r"([A-Z])(-?\d+\.\d+)")


def roomy_profile(table_z: float) -> Profile:
    """The sample profile with table_z set and vertical travel wide enough for any shift tested."""
    profile = load_profile(PROFILE)
    axes = list(profile.axes)
    for i in (1, 3):
        axes[i] = dataclasses.replace(axes[i], min=axes[i].min - 100.0, max=axes[i].max + 100.0)
    return dataclasses.replace(profile, table_z=table_z, axes=(axes[0], axes[1], axes[2], axes[3]))


def raised(job: Job, dz: float) -> Job:
    """Every section z and the safe position's vertical axes raised by dz."""
    s = job.safe_position
    return dataclasses.replace(
        job,
        root=dataclasses.replace(job.root, z=job.root.z + dz),
        tip=dataclasses.replace(job.tip, z=job.tip.z + dz),
        safe_position=(s[0], s[1] + dz, s[2], s[3] + dz),
    )


def assert_shifted(base: str, moved: str, letters: tuple[str, ...], dz: float) -> None:
    """Axes 2 and 4 of `moved` are those of `base` plus dz on every line; every other word is equal."""
    vertical = {letters[1], letters[3]}
    base_lines, moved_lines = base.splitlines(), moved.splitlines()
    assert len(moved_lines) == len(base_lines)
    for number, (b, m) in enumerate(zip(base_lines, moved_lines), start=1):
        if b.startswith("("):
            assert m == b, f"line {number}"
            continue
        b_words, m_words = b.split(), m.split()
        assert len(m_words) == len(b_words), f"line {number}"
        for bw, mw in zip(b_words, m_words):
            match = WORD.fullmatch(bw)
            if match and match.group(1) in vertical:
                assert mw[0] == bw[0], f"line {number}"
                # One unit in the 4th decimal for rounding after the shift.
                assert float(mw[1:]) == pytest.approx(float(bw[1:]) + dz, abs=1.01e-4), f"line {number}: {b} / {m}"
            else:
                assert mw == bw, f"line {number}: {b} / {m}"


@pytest.mark.parametrize("dz", [40.0, -20.0])
@pytest.mark.parametrize("job_path", JOBS, ids=[p.stem for p in JOBS])
def test_raising_the_table_and_the_sections_shifts_only_axes_2_and_4(job_path: Path, dz: float) -> None:
    job = load_job(job_path)
    base = generate(job, roomy_profile(0.0)).gcode
    assert base == generate(job, load_profile(PROFILE)).gcode  # the wider travel changes nothing
    moved = generate(raised(job, dz), roomy_profile(dz)).gcode
    assert_shifted(base, moved, load_profile(PROFILE).letters, dz)


def test_section_must_lie_above_the_table() -> None:
    job = load_job(ROOT / "examples" / "jobs" / "straight-wing.yaml")
    generate(job, roomy_profile(0.0))
    lowest = min(job.root.z, job.tip.z)  # the sections reach below their position z
    with pytest.raises(ValidationError, match="lies outside the block"):
        generate(job, roomy_profile(lowest))


def test_rapid_check_uses_the_raised_block() -> None:
    """A safe position inside the raised block is refused; the raised job itself is not."""
    job = load_job(ROOT / "examples" / "jobs" / "straight-wing.yaml")
    dz = 40.0
    moved = raised(job, dz)
    generate(moved, roomy_profile(dz))
    x_mid = job.block.x + job.block.length / 2
    z_mid = dz + job.block.height / 2
    inside = dataclasses.replace(moved, safe_position=(x_mid, z_mid, x_mid, z_mid))
    with pytest.raises(CamError, match="passes through the foam block"):
        generate(inside, roomy_profile(dz))
    # Level with the block on an unraised table, the same safe position is above it.
    assert z_mid > job.block.height


@pytest.mark.parametrize("dz", [25.0, -15.0])
def test_kerf_test_slots_sit_on_the_table(dz: float) -> None:
    test = KerfTest(100.0, 200.0, 60.0, 40.0, (60.0, 120.0, 240.0), 10.0, 8.0, 10.0, (350.0, 200.0, 350.0, 200.0))
    base = kerf_test_gcode(test, roomy_profile(0.0))
    s = test.safe_position
    moved_test = dataclasses.replace(test, safe_position=(s[0], s[1] + dz, s[2], s[3] + dz))
    moved = kerf_test_gcode(moved_test, roomy_profile(dz))
    letters = load_profile(PROFILE).letters
    base_lines, moved_lines = base.splitlines(), moved.splitlines()
    # The slot comments give absolute z; every other line follows the shift rule.
    for i, (b, m) in enumerate(zip(base_lines, moved_lines)):
        if b.startswith("(slot "):
            z = float(re.search(r"z = (-?[\d.]+) mm", b).group(1))  # type: ignore[union-attr]
            assert m == b.replace(f"z = {z:g} mm", f"z = {z + dz:g} mm")
            base_lines[i] = moved_lines[i] = ""
    assert_shifted("\n".join(base_lines), "\n".join(moved_lines), letters, dz)
