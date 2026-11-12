"""Brief 5 tasks 6 and 7: plots, animation, machine-readable report, CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from foamsim.cli import main
from foamsim.job import parse_job
from foamsim.plots import write_animation, write_overlay
from foamsim.profile import parse_profile
from foamsim.sim import simulate

REPO = Path(__file__).resolve().parents[2]


def write_inputs(
    tmp_path: Path, gcode: str, profile_data: dict[str, Any], job_data: dict[str, Any]
) -> tuple[Path, Path, Path]:
    nc = tmp_path / "part.nc"
    nc.write_text(gcode)
    profile = tmp_path / "profile.yaml"
    profile.write_text(yaml.safe_dump(profile_data))
    job = tmp_path / "job.yaml"
    job.write_text(yaml.safe_dump(job_data))
    return nc, profile, job


def test_report_is_json(
    tapered_gcode: str, profile_data: dict[str, Any], tapered_job_data: dict[str, Any]
) -> None:
    result = simulate(tapered_gcode, parse_profile(profile_data), parse_job(tapered_job_data, REPO))
    report = json.loads(json.dumps(result.to_dict()))
    assert report["result"] == "pass"
    assert report["tolerance_mm"] == 0.05
    assert report["sections"]["root"]["plane_y"] == 100.0
    assert report["sections"]["tip"]["max_deviation_mm"] < 1e-6
    assert report["sections"]["root"]["elements"]["outer"]["passed"] is True
    assert {c["name"] for c in report["checks"]} >= {
        "soft_limits",
        "wire_angle",
        "axis_rate",
        "speed_ratio",
        "cut_speed",
        "start_end_outside",
        "rapids_clear",
        "lead_in_exit_clear",
    }
    assert abs(report["time"]["feed_min"] - 4.83) < 1e-4  # 2*1.195 + 2*1.01 + 2*0.21
    assert report["face_speeds_mm_min"]["tip"]["min"] < report["face_speeds_mm_min"]["tip"]["max"]


def test_cli_pass_and_fail(
    tmp_path: Path,
    tapered_gcode: str,
    profile_data: dict[str, Any],
    tapered_job_data: dict[str, Any],
) -> None:
    nc, profile, job = write_inputs(tmp_path, tapered_gcode, profile_data, tapered_job_data)
    out = tmp_path / "out"
    code = main([str(nc), "--job", str(job), "--profile", str(profile), "--out", str(out),
                 "--no-plots"])
    assert code == 0
    report = json.loads((out / "part-report.json").read_text())
    assert report["result"] == "pass"

    nc.write_text(tapered_gcode.replace("X206.7500", "X208.8175"))
    code = main([str(nc), "--job", str(job), "--profile", str(profile), "--out", str(out),
                 "--no-plots"])
    assert code == 1
    report = json.loads((out / "part-report.json").read_text())
    assert report["result"] == "fail"
    assert "root" in report["failed_sections"]


def test_cli_input_error(tmp_path: Path, profile_data: dict[str, Any]) -> None:
    nc, profile, job = write_inputs(tmp_path, "G21\n", profile_data, {"format": "nope"})
    code = main([str(nc), "--job", str(job), "--profile", str(profile), "--out",
                 str(tmp_path / "o"), "--no-plots"])
    assert code == 2


def test_plots_are_written(
    tmp_path: Path,
    tapered_gcode: str,
    profile_data: dict[str, Any],
    tapered_job_data: dict[str, Any],
) -> None:
    result = simulate(
        tapered_gcode,
        parse_profile(profile_data),
        parse_job(tapered_job_data, REPO),
        planes=(500.0,),
    )
    overlay = tmp_path / "overlay.png"
    write_overlay(result, overlay)
    assert overlay.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    animation = tmp_path / "cut.gif"
    write_animation(result, animation, max_frames=10)
    assert animation.read_bytes()[:6] in (b"GIF89a", b"GIF87a")
