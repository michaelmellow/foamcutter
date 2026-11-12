"""Reading the machine profile, the job file, .dat files, and the target
geometry of job file sections 6 to 8."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from foamsim.dat import read_dat
from foamsim.errors import InputError
from foamsim.job import load_job, parse_job
from foamsim.profile import kerf_lookup, load_profile, parse_profile
from foamsim.target import build_targets, contour_uv, place

REPO = Path(__file__).resolve().parents[2]


def flat(points: Any) -> list[float]:
    return [float(c) for p in points for c in p]


# ---------------------------------------------------------------- profile


def test_sample_profile_loads() -> None:
    profile = load_profile(REPO / "examples/profiles/sample-machine.yaml")
    assert profile.letters == ("X", "Y", "Z", "A")
    assert profile.wire_span == 900.0
    assert profile.kerf_tables["eps-20-sample"].points[0] == (30.0, 2.0)


def test_kerf_lookup_rule(profile_data: dict[str, Any]) -> None:
    profile = parse_profile(profile_data)
    table = profile.kerf_tables["lin"]
    assert kerf_lookup(table, 50.0) == 1.4  # equal to a table speed
    assert kerf_lookup(table, 150.0) == 1.0
    # 1.4 + (1.0 - 1.4) * (75 - 50) / (150 - 50) = 1.3
    assert kerf_lookup(table, 75.0) == pytest.approx(1.3)
    with pytest.raises(InputError) as info:
        kerf_lookup(table, 31.2)
    assert str(info.value) == (
        'kerf table "lin": speed 31.2 mm/min is outside the table range '
        "50.0 to 150.0 mm/min"
    )
    with pytest.raises(InputError, match="outside the table range"):
        kerf_lookup(table, 150.01)


def test_profile_reports_every_violation(profile_data: dict[str, Any]) -> None:
    profile_data["colour"] = "red"
    profile_data["axes"][3]["max"] = -1.0
    profile_data["axes"][2]["letter"] = "X"
    profile_data["kerf_tables"][0]["points"][1]["speed"] = 10.0
    profile_data["limits"]["max_wire_angle"] = 90.0
    del profile_data["name"]
    with pytest.raises(InputError) as info:
        parse_profile(profile_data)
    text = str(info.value)
    for path in (
        "colour",
        "axes[3].max",
        "axes[2].letter",
        "kerf_tables[0].points[1].speed",
        "limits.max_wire_angle",
        "name",
    ):
        assert path in text


def test_profile_rejects_bool_as_number(profile_data: dict[str, Any]) -> None:
    profile_data["wire_span"] = True
    with pytest.raises(InputError, match="wire_span"):
        parse_profile(profile_data)


# ---------------------------------------------------------------- job


def test_example_jobs_load() -> None:
    for name in ("straight-wing", "tapered-spar", "tapered-swept-washout"):
        job = load_job(REPO / f"examples/jobs/{name}.yaml")
        assert job.kerf_table == "eps-20-sample"


def test_job_reports_every_violation(job_data: dict[str, Any]) -> None:
    job_data["speed"] = 1
    job_data["tip"]["position"]["y"] = 100.0
    job_data["direction"] = "sideways"
    job_data["root"]["twist"] = 50.0
    with pytest.raises(InputError) as info:
        parse_job(job_data, REPO)
    text = str(info.value)
    for path in ("speed", "tip.position.y", "direction", "root.twist"):
        assert path in text


def test_section_outside_block_names_section(
    profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    # Tip rectangle z from 20 to 40; raise it by 25 -> z from 45 to 65, block
    # height 60, so the largest distance outside is 5 mm.
    job_data["tip"]["position"]["z"] = 65.0
    profile = parse_profile(profile_data)
    job = parse_job(job_data, REPO)
    with pytest.raises(InputError) as info:
        build_targets(job, profile)
    assert "tip" in str(info.value)
    assert "5.000" in str(info.value)


def test_section_plane_outside_carriages(
    profile_data: dict[str, Any], job_data: dict[str, Any]
) -> None:
    job_data["tip"]["position"]["y"] = 1000.0  # wire_span is 1000
    with pytest.raises(InputError, match="tip.position.y"):
        build_targets(parse_job(job_data, REPO), parse_profile(profile_data))


def test_slit_on_wrong_side(profile_data: dict[str, Any], job_data: dict[str, Any]) -> None:
    # A hole at u 40..60, v -15..-5 with slit "lower" meets the lower side at
    # v = -20 (fine); asking for "upper" in the tip but "lower" in the root
    # is an error, and so is a slit that misses the contour.
    for section in ("root", "tip"):
        job_data[section]["features"] = [
            {
                "name": "spar",
                "kind": "hole",
                "slit": "lower",
                "points": [[40.0, -15.0], [60.0, -15.0], [60.0, -5.0], [40.0, -5.0]],
            }
        ]
    job_data["tip"]["features"][0]["slit"] = "upper"
    with pytest.raises(InputError, match="slit"):
        build_targets(parse_job(job_data, REPO), parse_profile(profile_data))


# ---------------------------------------------------------------- .dat


def test_read_selig(tmp_path: Path) -> None:
    path = tmp_path / "s.dat"
    path.write_text("SELIG\n1.0 0.01\n0.5 0.05\n\n0.0 0.0\n0.5 -0.03\n1.0 -0.01\n")
    assert read_dat(path) == [(1.0, 0.01), (0.5, 0.05), (0.0, 0.0), (0.5, -0.03), (1.0, -0.01)]


def test_read_lednicer(tmp_path: Path) -> None:
    # Upper LE->TE reversed, then lower LE->TE with the shared LE dropped.
    path = tmp_path / "l.dat"
    path.write_text("LED\n  3.  3.\n\n0 0\n0.5 0.05\n1 0.01\n\n0 0\n0.5 -0.03\n1 -0.01\n")
    assert read_dat(path) == [(1.0, 0.01), (0.5, 0.05), (0.0, 0.0), (0.5, -0.03), (1.0, -0.01)]


def test_dat_bad_line_names_file_and_line(tmp_path: Path) -> None:
    path = tmp_path / "bad.dat"
    path.write_text("BAD\n1.0 0.0\n0.5 0.1 0.2\n")
    with pytest.raises(InputError, match=r"bad\.dat.*line 3"):
        read_dat(path)


def test_example_airfoils_read() -> None:
    for name in ("clarky", "e205"):
        points = read_dat(REPO / f"examples/airfoils/{name}.dat")
        assert len(points) > 20


# ---------------------------------------------------------------- geometry


def test_contour_reverses_lower_first_input() -> None:
    # Lower side first: LE index 1, mean before -0.1 < mean after 0.1 -> reverse.
    raw = [(1.0, -0.1), (0.0, -0.1), (0.0, 0.1), (1.0, 0.1)]
    uv = contour_uv(raw, chord=100.0, min_te_thickness=0.0)
    assert flat(uv) == pytest.approx(flat([(100.0, 0.0), (0.0, 0.0), (0.0, -20.0), (100.0, -20.0)]))


def test_contour_drops_duplicates_and_normalises() -> None:
    # u spans 0..2 after translation -> divide by 2. The repeated point is dropped.
    raw = [(2.0, 0.2), (2.0, 0.2), (0.0, 0.0), (2.0, -0.2)]
    uv = contour_uv(raw, chord=1.0, min_te_thickness=0.0)
    assert flat(uv) == pytest.approx(flat([(1.0, 0.1), (0.0, 0.0), (1.0, -0.1)]))


def test_trailing_edge_thickening() -> None:
    # chord 100, min_te 1.0 -> t_min = 0.01, gap = 0 -> d = 0.01.
    # Upper: v += 0.005 u; lower: v -= 0.005 u; LE unchanged; then x 100.
    raw = [(1.0, 0.0), (0.5, 0.1), (0.0, 0.0), (0.5, -0.1), (1.0, 0.0)]
    uv = contour_uv(raw, chord=100.0, min_te_thickness=1.0)
    assert flat(uv) == pytest.approx(
        flat([(100.0, 0.5), (50.0, 10.25), (0.0, 0.0), (50.0, -10.25), (100.0, -0.5)])
    )


def test_twist_and_placement() -> None:
    # twist 10 deg about c_t = 0.25 * 100 = 25. The LE (0, 0) goes to
    #   u' = 25 + (0 - 25) cos 10 = 25 - 24.62019 = 0.37981
    #   v' = -(0 - 25) sin 10     = 4.34120
    # position (200, 300, 30): x = 200 + 0.37981 - 25 = 175.37981, z = 34.34120
    pts = place([(0.0, 0.0)], chord=100.0, twist=10.0, twist_axis=0.25, x=200.0, z=30.0)
    assert pts[0] == pytest.approx((175.37981, 34.34120), abs=1e-5)
    c, s = math.cos(math.radians(10)), math.sin(math.radians(10))
    assert pts[0] == pytest.approx((200 - 25 * c, 30 + 25 * s))


def test_targets_of_the_test_job(profile_data: dict[str, Any], job_data: dict[str, Any]) -> None:
    targets = build_targets(parse_job(job_data, REPO), parse_profile(profile_data))
    assert targets["root"].y == 100.0
    assert targets["tip"].y == 900.0
    assert flat(targets["root"].outer) == pytest.approx(
        flat([(200.0, 40.0), (100.0, 40.0), (100.0, 20.0), (200.0, 20.0)])
    )


def test_mirror_moves_the_planes(profile_data: dict[str, Any], job_data: dict[str, Any]) -> None:
    # Mirror in y = 500: root plane 100 -> 900, tip 900 -> 100; x and z unchanged.
    job_data["mirror"] = True
    targets = build_targets(parse_job(job_data, REPO), parse_profile(profile_data))
    assert targets["root"].y == 900.0
    assert targets["tip"].y == 100.0
