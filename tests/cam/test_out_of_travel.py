"""A cut whose only problem is an axis limit (§11.1): refused by default, made and marked
when the caller allows it (the GUI shows it and blocks Start until the stock is moved)."""

from pathlib import Path

import pytest

from foamcam import CamError, generate, load_job, load_profile

from .test_acceptance import constant_kerf_profile, rectangle_job, write_yaml


def _job_and_profile(tmp_path: Path, max_wire_angle: float = 30.0, **axis4: float):  # type: ignore[no-untyped-def]
    job = load_job(write_yaml(tmp_path / "job.yaml", rectangle_job()))
    data = constant_kerf_profile(**axis4)
    data["limits"]["max_wire_angle"] = max_wire_angle
    profile = load_profile(write_yaml(tmp_path / "profile.yaml", data))
    return job, profile


def test_axis_limit_refuses_by_default(tmp_path: Path) -> None:
    job, profile = _job_and_profile(tmp_path, min=75.0)
    with pytest.raises(CamError, match=r"axis 4 \(A\) goes to .* beyond its min 75"):
        generate(job, profile)


def test_axis_limit_allowed_makes_a_marked_program(tmp_path: Path) -> None:
    job, profile = _job_and_profile(tmp_path, min=75.0)
    result = generate(job, profile, allow_out_of_travel=True)
    assert len(result.out_of_travel) == 1
    assert result.out_of_travel[0].startswith("axis 4 (A) goes to")
    lines = result.gcode.splitlines()
    assert lines[0] == "(OUT OF TRAVEL: an axis passes its limit, do not run as placed)"
    # the problem names the line of the marked program
    line = int(result.out_of_travel[0].rsplit("program line ", 1)[1].rstrip(")"))
    value = float(result.out_of_travel[0].split("goes to ")[1].split(",")[0])
    assert f"A{value:.4f}" in lines[line - 1]
    assert "OUT OF TRAVEL: do not run this program with the block where it is" in result.report


def test_allowed_program_inside_travel_is_unchanged(tmp_path: Path) -> None:
    job, profile = _job_and_profile(tmp_path)
    plain, allowed = generate(job, profile), generate(job, profile, allow_out_of_travel=True)
    assert allowed.out_of_travel == [] and allowed.gcode == plain.gcode and allowed.report == plain.report


def test_other_problems_still_refuse(tmp_path: Path) -> None:
    job, profile = _job_and_profile(tmp_path, max_wire_angle=0.5, min=75.0)  # the wire also slants too far
    with pytest.raises(CamError) as refused:
        generate(job, profile, allow_out_of_travel=True)
    assert any("beyond its min" in p for p in refused.value.problems)
    assert any("max_wire_angle" in p for p in refused.value.problems)
