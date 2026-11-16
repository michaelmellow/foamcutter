"""Job file section 8 channels and the 10.4 channel exit, simulated from foamcam programs."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from foamcam import generate, load_job_text, load_profile_text
from foamsim import parse_job, parse_profile, simulate

LETTERS = ("X", "Y", "Z", "A")
RECTANGLE = [[1.0, 0.1], [0.0, 0.1], [0.0, -0.1], [1.0, -0.1]]  # 100 x 20 mm, upper side v = 0
HOLE = [[40.0, -6.0], [40.0, -14.0], [50.0, -14.0], [50.0, -6.0]]
OTHER = [[70.0, -6.0], [70.0, -14.0], [80.0, -14.0], [80.0, -6.0]]
CURVED = [[38.0, -4.0], [37.0, -2.0], [37.0, 5.0]]


def profile() -> dict[str, Any]:
    axes = [{"letter": c, "min": 0.0, "max": 600.0, "max_rate": 2000.0, "lead": 8.0} for c in LETTERS]
    return {
        "format": "foamcutter-machine-profile", "version": 1, "name": "test machine", "axes": axes,
        "wire_span": 900.0, "limits": {"max_wire_angle": 30.0, "max_speed_ratio": 3.0},
        "kerf_tables": [{"name": "k", "description": "constant",
                         "points": [{"speed": 1.0, "kerf": 0.2}, {"speed": 100000.0, "kerf": 0.2}]}],
    }


def job(channel: list[list[float]] | None = None, tip_chord: float = 100.0, **top: Any) -> dict[str, Any]:
    def section(y: float, chord: float) -> dict[str, Any]:
        scale = chord / 100.0
        hole: dict[str, Any] = {"name": "spar", "kind": "hole", "slit": "upper",
                                "points": [[u * scale, v * scale] for u, v in HOLE]}
        if channel is not None:
            hole["channel"] = [[u * scale, v * scale] for u, v in channel]
        other = {"name": "other", "kind": "hole", "slit": "upper",
                 "points": [[u * scale, v * scale] for u, v in OTHER]}
        return {"shape": {"points": RECTANGLE}, "chord": chord, "twist": 0.0, "twist_axis": 0.0,
                "position": {"x": 100.0, "y": y, "z": 50.0}, "features": [hole, other]}

    return {
        "format": "foamcutter-job", "version": 1, "name": "channels", "kerf_table": "k",
        "cut_speed": 100.0, "direction": "upper_first", "max_segment_length": 5.0, "min_te_thickness": 0.0,
        "lead_in": 10.0, "safe_position": [230.0, 100.0, 230.0, 100.0], "mirror": False,
        "block": {"x": 90.0, "length": 120.0, "height": 80.0},
        "root": section(300.0, 100.0), "tip": section(600.0, tip_chord), **top,
    }


def cam(data: dict[str, Any]) -> str:
    import yaml

    cam_profile = profile() | {"table_z": 0.0}  # the simulator does not read table_z yet
    gcode = generate(load_job_text(yaml.safe_dump(data), Path(".")),
                     load_profile_text(yaml.safe_dump(cam_profile))).gcode
    # The simulator does not accept the CAM's opening lift (a G0 with only the two
    # vertical axes) yet: leave it out, the next G0 sets all four axes.
    lines = gcode.splitlines()
    lift = next(i for i, line in enumerate(lines) if line.startswith("G0"))
    assert len(lines[lift].split()) == 3
    return "\n".join(lines[:lift] + lines[lift + 1 :]) + "\n"


def sim(gcode: str, data: dict[str, Any]) -> Any:
    return simulate(gcode, parse_profile(profile()), parse_job(copy.deepcopy(data), Path(".")))


@pytest.mark.parametrize(
    "case",
    [
        {},
        {"channel": [[30.0, 5.0]]},
        {"channel": CURVED},
        {"channel": CURVED, "channel_exit": "rapid"},
        {"channel": [[30.0, 5.0]], "channel_exit": 400.0},
        {"channel": CURVED, "channel_exit": "rapid", "tip_chord": 80.0},
    ],
)
def test_channel_programs_pass(case: dict[str, Any]) -> None:
    data = job(**case)
    result = sim(cam(data), data)
    assert result.passed, result.summary()


def test_rapid_exit_off_the_slit_is_caught() -> None:
    data = job(channel=CURVED, channel_exit="rapid")
    lines = cam(data).splitlines()
    feeds = [i for i, line in enumerate(lines) if line.startswith("G1")]
    rapid = next(i for i in range(feeds[0], feeds[-1]) if lines[i].startswith("G0"))
    words = lines[rapid].split()
    moved = [w if w[0] not in "XZ" else f"{w[0]}{float(w[1:]) + 3:.4f}" for w in words]  # 3 mm aside
    lines[rapid] = " ".join(moved)
    result = sim("\n".join(lines) + "\n", data)
    assert not result.passed
    assert any("away from the nearest slit" in p for s in result.sections.values() for p in s.problems)


def test_exit_speed_must_match_the_job() -> None:
    gcode = cam(job(channel=[[30.0, 5.0]], channel_exit=400.0))
    result = sim(gcode, job(channel=[[30.0, 5.0]], channel_exit=300.0))
    speed = next(c for c in result.checks if c.name == "cut_speed")
    assert not speed.passed and "channel_exit is 300" in speed.messages[0]


def test_bad_channel_is_rejected() -> None:
    from foamsim import InputError

    data = job(channel=[[60.0, -3.0], [75.0, -10.0], [85.0, 5.0]])  # through "other"
    with pytest.raises(InputError, match='crosses the feature "other"'):
        sim("G0 X0 Y0 Z0 A0\n", data)
