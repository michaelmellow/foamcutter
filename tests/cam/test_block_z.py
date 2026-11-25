"""The block's optional bottom face `z` (job-file.md §5): a block that is part of a larger
block, e.g. the next layer of a stack, instead of resting on the table."""

import dataclasses
from pathlib import Path

import pytest
import yaml

from foamcam import (
    CamError,
    ValidationError,
    generate,
    load_job,
    load_job_text,
    load_profile,
)
from foamcam.job import Job

ROOT = Path(__file__).resolve().parents[2]
JOB = ROOT / "examples" / "jobs" / "straight-wing.yaml"
PROFILE = ROOT / "examples" / "profiles" / "sample-machine.yaml"


def raised(job: Job, dz: float, block_z: float | None) -> Job:
    """Sections and the safe height raised by dz; the block's bottom face set to block_z."""
    s = job.safe_position
    return dataclasses.replace(
        job,
        root=dataclasses.replace(job.root, z=job.root.z + dz),
        tip=dataclasses.replace(job.tip, z=job.tip.z + dz),
        safe_position=(s[0], s[1] + dz, s[2], s[3] + dz),
        block=dataclasses.replace(job.block, z=block_z),
    )


def test_block_z_is_optional_and_read() -> None:
    data = yaml.safe_load(JOB.read_text(encoding="utf-8"))
    assert load_job(JOB).block.z is None
    data["block"]["z"] = 30.0
    assert load_job_text(yaml.safe_dump(data), JOB.parent).block.z == 30.0
    data["block"]["z"] = "high"
    with pytest.raises(ValidationError, match="block.z: must be a number"):
        load_job_text(yaml.safe_dump(data), JOB.parent)


def test_block_z_equal_to_the_table_changes_nothing() -> None:
    job, profile = load_job(JOB), load_profile(PROFILE)
    base = generate(job, profile)
    same = generate(dataclasses.replace(job, block=dataclasses.replace(job.block, z=profile.table_z)), profile)
    assert same.gcode == base.gcode


def test_raised_block_cuts_the_raised_part() -> None:
    """A block 30 mm up (on another one) with the part raised with it: the job is valid and
    its report gives the raised z range."""
    job, profile = load_job(JOB), load_profile(PROFILE)
    dz = 30.0
    result = generate(raised(job, dz, profile.table_z + dz), profile)
    z0 = profile.table_z + dz
    assert f"  z {z0:.3f} to {z0 + job.block.height:.3f}" in result.report


def test_part_below_the_raised_block_is_refused() -> None:
    """The part stays where it was but the block's bottom face goes up: §6.7 refuses it."""
    job, profile = load_job(JOB), load_profile(PROFILE)
    with pytest.raises(ValidationError, match="lies outside the block"):
        generate(raised(job, 0.0, profile.table_z + 30.0), profile)


def test_rapid_check_uses_the_raised_block() -> None:
    """A safe position that is clear of a block on the table is inside the raised one."""
    job, profile = load_job(JOB), load_profile(PROFILE)
    dz = 30.0
    x_mid = job.block.x + job.block.length / 2
    z_mid = profile.table_z + dz + job.block.height / 2
    assert z_mid > profile.table_z + job.block.height  # above the block on the table
    moved = raised(job, dz, profile.table_z + dz)
    with pytest.raises(CamError, match="passes through the foam block"):
        generate(dataclasses.replace(moved, safe_position=(x_mid, z_mid, x_mid, z_mid)), profile)
