"""§8 channels and the §10.4 channel exit (channel_exit)."""

import math
from pathlib import Path
from typing import Any

import pytest
import yaml

from foamcam import ValidationError, generate, load_job, load_profile_text

LETTERS = ("X", "Y", "Z", "A")
SPAN = 900.0
ROOT_Y, TIP_Y = 300.0, 600.0

# Rectangle 100 x 20 mm after §6.1: upper side v = 0, lower side v = -20.
RECTANGLE = [[1.0, 0.1], [0.0, 0.1], [0.0, -0.1], [1.0, -0.1]]
# Square hole, first point its top-left corner, counter-clockwise.
HOLE = [[40.0, -6.0], [40.0, -14.0], [50.0, -14.0], [50.0, -6.0]]
OTHER = [[70.0, -6.0], [70.0, -14.0], [80.0, -14.0], [80.0, -6.0]]


def profile_text() -> str:
    axes = [{"letter": letter, "min": 0.0, "max": 600.0, "max_rate": 2000.0, "lead": 8.0} for letter in LETTERS]
    return yaml.safe_dump({
        "format": "foamcutter-machine-profile",
        "version": 1,
        "name": "test machine",
        "axes": axes,
        "wire_span": SPAN,
        "table_z": 0.0,
        "limits": {"max_wire_angle": 30.0, "max_speed_ratio": 3.0},
        "kerf_tables": [{"name": "k", "description": "constant",
                         "points": [{"speed": 1.0, "kerf": 0.2}, {"speed": 100000.0, "kerf": 0.2}]}],
    })


def section(y: float, features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"shape": {"points": RECTANGLE}, "chord": 100.0, "twist": 0.0, "twist_axis": 0.0,
            "position": {"x": 100.0, "y": y, "z": 50.0}, "features": features}


def job(tmp_path: Path, channel: list[list[float]] | None = None, tip_channel: list[list[float]] | None = None,
        other: bool = False, **top: Any) -> Any:
    def features(ch: list[list[float]] | None) -> list[dict[str, Any]]:
        hole: dict[str, Any] = {"name": "spar", "kind": "hole", "slit": "upper", "points": HOLE}
        if ch is not None:
            hole["channel"] = ch
        extra = [{"name": "other", "kind": "hole", "slit": "upper", "points": OTHER}] if other else []
        return [hole, *extra]

    data: dict[str, Any] = {
        "format": "foamcutter-job", "version": 1, "name": "channel test", "kerf_table": "k",
        "cut_speed": 100.0, "direction": "upper_first", "max_segment_length": 10.0, "min_te_thickness": 0.0,
        "lead_in": 10.0, "safe_position": [230.0, 100.0, 230.0, 100.0], "mirror": False,
        "block": {"x": 90.0, "length": 120.0, "height": 80.0},
        "root": section(ROOT_Y, features(channel)),
        "tip": section(TIP_Y, features(tip_channel if tip_channel is not None else channel)),
        **top,
    }
    path = tmp_path / "job.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return load_job(path)


def run(tmp_path: Path, *args: Any, **kwargs: Any) -> str:
    return generate(job(tmp_path, *args, **kwargs), load_profile_text(profile_text())).gcode


def motions(gcode: str) -> list[tuple[str, tuple[float, float], float | None]]:
    """(G0/G1, wire point at the root face (x, z), F) for every full motion line."""
    out = []
    for line in gcode.splitlines():
        words = line.split()
        if not words or words[0] not in ("G0", "G1") or len(words) < 5:
            continue
        v = {w[0]: float(w[1:]) for w in words[1:]}
        s = ROOT_Y / SPAN
        x = v["X"] + s * (v["Z"] - v["X"])
        z = v["Y"] + s * (v["A"] - v["Y"])
        out.append((words[0], (x, z), v.get("F")))
    return out


def placed(u: float, v: float) -> tuple[float, float]:
    return 100.0 + u, 50.0 + v  # leading edge at x 100, z 50 (twist 0)


def near(p: tuple[float, float], q: tuple[float, float], tol: float = 2e-4) -> bool:
    return math.dist(p, q) <= tol


def test_one_point_channel_straight_up_is_the_plain_slit(tmp_path: Path) -> None:
    plain = run(tmp_path)
    assert run(tmp_path, channel=[[40.0, 5.0]]) == plain
    assert run(tmp_path, channel_exit=100.0) == plain  # exit at cut speed: same moves and feeds


def test_angled_channel_meets_the_contour_on_its_line(tmp_path: Path) -> None:
    moves = motions(run(tmp_path, channel=[[30.0, 5.0]]))
    # From (40, -6) toward (30, 5): crosses v = 0 at u = 40 - 10 * 6 / 11.
    slit = placed(40 - 60 / 11, 0.0)
    # The compensated slit point lies 0.1 mm (half kerf) outside the upper side.
    hits = [i for i, (_, p, _) in enumerate(moves) if near(p, (slit[0], slit[1] + 0.1), 0.05)]
    assert len(hits) == 2  # in, and back out after the hole
    inside = moves[hits[0] + 1 : hits[1]]
    assert all(p[1] < 50.0 for _, p, _ in inside)  # everything between is below the upper side
    corner = placed(40.0, -6.0)
    assert near(inside[0][1], (corner[0] + 0.1, corner[1] - 0.1), 0.01)  # compensated hole corner


def test_curved_channel_passes_its_points_in_and_back_out(tmp_path: Path) -> None:
    channel = [[38.0, -4.0], [37.0, -2.0], [37.0, 5.0]]
    moves = motions(run(tmp_path, channel=channel))
    a, b = placed(38.0, -4.0), placed(37.0, -2.0)
    at_a = [i for i, (_, p, _) in enumerate(moves) if near(p, a)]
    at_b = [i for i, (_, p, _) in enumerate(moves) if near(p, b)]
    assert len(at_a) == 2 and len(at_b) == 2  # channel points are not kerf compensated
    assert at_b[0] < at_a[0] < at_a[1] < at_b[1]  # in through b then a; out through a then b


def test_rapid_exit_leaves_every_slit_with_g0(tmp_path: Path) -> None:
    channel = [[38.0, -4.0], [37.0, -2.0], [37.0, 5.0]]
    moves = motions(run(tmp_path, channel=channel, other=True, channel_exit="rapid"))
    feeds = [i for i, m in enumerate(moves) if m[0] == "G1"]
    inner_rapids = [i for i, m in enumerate(moves) if m[0] == "G0" and feeds[0] < i < feeds[-1]]
    # upper_first runs from the trailing edge, so "other" (u 70 to 80) comes first: its straight
    # slit is 1 rapid; then the spar: back through a, b and on to the slit point (3 rapids)
    assert len(inner_rapids) == 4
    a, b = placed(38.0, -4.0), placed(37.0, -2.0)
    assert near(moves[inner_rapids[1]][1], a) and near(moves[inner_rapids[2]][1], b)
    assert all(moves[i + 1][0] == "G1" for i in (inner_rapids[0], inner_rapids[3]))  # back to cutting


def test_numeric_exit_speed_times_the_exit_moves(tmp_path: Path) -> None:
    moves = motions(run(tmp_path, channel_exit=400.0))
    corner = placed(40.0, -6.0)
    back = [i for i, (_, p, _) in enumerate(moves) if near(p, (corner[0] + 0.1, corner[1] - 0.1), 0.01)][-1]
    cmd, end, f = moves[back + 1]  # straight up to the slit point
    length = math.dist(moves[back][1], end)  # identical sections: both faces move the same
    assert cmd == "G1" and f is not None
    assert 1 / f == pytest.approx(length / 400.0, rel=1e-4)


@pytest.mark.parametrize(
    ("channel", "message"),
    [
        ([[40.0, -2.0]], "must end outside"),
        ([[45.0, -10.0], [40.0, 5.0]], "runs into its own feature"),
        ([[60.0, -3.0], [75.0, -10.0], [85.0, 5.0]], 'crosses feature "other"'),
        ([[40.0, 5.0], [41.0, -2.0], [41.0, 6.0]], "leaves the outer contour"),
    ],
)
def test_bad_channels_are_rejected(tmp_path: Path, channel: list[list[float]], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        run(tmp_path, channel=channel, other=True)


def test_channel_lists_must_match_and_exit_must_be_valid(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="channel points at the tip"):
        job(tmp_path, channel=[[40.0, 5.0]], tip_channel=[[39.0, -2.0], [39.0, 5.0]])
    with pytest.raises(ValidationError, match="channel_exit"):
        job(tmp_path, channel_exit="fast")
    with pytest.raises(ValidationError, match="channel_exit"):
        job(tmp_path, channel_exit=0)
