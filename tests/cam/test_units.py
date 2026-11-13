"""Unit tests for foamcam: formats, geometry steps, checks, kerf test and CLI."""

import copy
import math
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from foamcam import (
    CamError,
    ValidationError,
    generate,
    geometry,
    load_job,
    load_profile,
    load_profile_text,
)
from foamcam import cam as cam_module
from foamcam.cam import EXACT_JOIN_ANGLE, Cam, Move, transition_vertices, validate
from foamcam.cli import main
from foamcam.dat import parse_dat, read_dat
from foamcam.geometry import dist
from foamcam.kerftest import KerfTest, kerf_test_gcode
from foamcam.sections import clean_contour, thicken_trailing_edge

ROOT = Path(__file__).resolve().parents[2]
AIRFOILS = ROOT / "examples" / "airfoils"
SAMPLE_PROFILE = ROOT / "examples" / "profiles" / "sample-machine.yaml"


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def sample_profile_data() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(SAMPLE_PROFILE.read_text(encoding="utf-8"))
    return data


def wing_job(**overrides: Any) -> dict[str, Any]:
    """Tapered Clark Y panel with a spar hole, valid for the sample profile."""
    job: dict[str, Any] = {
        "format": "foamcutter-job",
        "version": 1,
        "name": "unit wing",
        "kerf_table": "eps-20-sample",
        "cut_speed": 150.0,
        "direction": "upper_first",
        "max_segment_length": 2.0,
        "min_te_thickness": 0.6,
        "lead_in": 20.0,
        "safe_position": [550.0, 250.0, 550.0, 250.0],
        "mirror": False,
        "block": {"x": 100.0, "length": 300.0, "height": 60.0},
        "root": {
            "shape": {"dat": str(AIRFOILS / "clarky.dat")},
            "chord": 250.0,
            "twist": 0.0,
            "twist_axis": 0.25,
            "position": {"x": 182.5, "y": 150.0, "z": 25.0},
            "features": [
                {"name": "spar", "kind": "hole", "slit": "upper",
                 "points": [[70.0, 4.0], [80.0, 4.0], [80.0, 14.0], [70.0, 14.0]]},
            ],
        },
        "tip": {
            "shape": {"dat": str(AIRFOILS / "clarky.dat")},
            "chord": 160.0,
            "twist": -2.0,
            "twist_axis": 0.25,
            "position": {"x": 180.0, "y": 750.0, "z": 25.0},
            "features": [
                {"name": "spar", "kind": "hole", "slit": "upper",
                 "points": [[42.0, 3.0], [52.0, 3.0], [52.0, 10.0], [42.0, 10.0]]},
            ],
        },
    }
    job.update(overrides)
    return job


def run(tmp_path: Path, job: dict[str, Any], profile: dict[str, Any] | None = None) -> str:
    profile_path = write_yaml(tmp_path / "profile.yaml", profile or sample_profile_data())
    return generate(load_job(write_yaml(tmp_path / "job.yaml", job)), load_profile(profile_path)).gcode


# --- .dat files (job-file.md §7) --------------------------------------------


def test_lednicer_file_is_converted_to_selig_order() -> None:
    points = read_dat(AIRFOILS / "clarky.dat")
    # 61 upper + 61 lower, sharing the leading edge point once.
    assert len(points) == 121
    assert points[0] == (1.0, 0.0005993)
    assert points[60] == (0.0, 0.0)
    assert points[-1] == (1.0, -0.0005993)


def test_selig_file_is_read_as_written() -> None:
    points = read_dat(AIRFOILS / "e205.dat")
    assert len(points) == 61
    assert points[0] == (1.0, 0.0) and points[-1] == (1.0, 0.0)


def test_bad_dat_line_names_the_line() -> None:
    with pytest.raises(ValidationError, match="line 3"):
        parse_dat("NAME\n1.0 0.0\n0.5 oops\n0.0 0.0\n", "bad.dat")


# --- target geometry (job-file.md §6) ---------------------------------------


def test_contour_is_oriented_and_normalised() -> None:
    selig = [(1.0, 0.0), (0.5, 0.06), (0.0, 0.0), (0.5, -0.04), (1.0, 0.0)]
    # Lower side first, doubled in size and shifted: must give the same result.
    raw = [(2 * u + 3, 2 * v - 1) for u, v in reversed(selig)]
    problems: list[str] = []
    points, i_le = clean_contour(raw, "root", problems)
    assert problems == []
    assert i_le == 2
    assert [c for p in points for c in p] == pytest.approx([c for p in selig for c in p])


def test_trailing_edge_is_opened_to_the_minimum() -> None:
    problems: list[str] = []
    points, i_le = clean_contour(read_dat(AIRFOILS / "e205.dat"), "root", problems)
    t_min = 0.6 / 200.0
    thick = thicken_trailing_edge(points, i_le, t_min)
    assert thick[0][1] - thick[-1][1] == pytest.approx(t_min)
    assert thick[i_le] == points[i_le]


def test_positive_twist_raises_the_leading_edge(tmp_path: Path) -> None:
    job = load_job(write_yaml(tmp_path / "job.yaml", wing_job()))
    root, _ = validate(job, load_profile(SAMPLE_PROFILE))
    data = wing_job()
    data["root"]["twist"] = 5.0
    twisted_job = load_job(write_yaml(tmp_path / "twisted.yaml", data))
    twisted, _ = validate(twisted_job, load_profile(SAMPLE_PROFILE))
    le_flat = root.place(root.contour[root.le_index])
    le_up = twisted.place(twisted.contour[twisted.le_index])
    # Leading edge 62.5 mm ahead of the twist axis: rises by 62.5 sin 5°.
    assert le_up[1] - le_flat[1] == pytest.approx(62.5 * math.sin(math.radians(5.0)), abs=1e-9)


# --- kerf (machine-profile.md §7, job-file.md §10.7) ------------------------


def test_kerf_lookup_interpolates_and_names_range_when_outside() -> None:
    table = load_profile(SAMPLE_PROFILE).kerf_tables["eps-20-sample"]
    assert table.kerf(150.0) == pytest.approx(1.10 + (0.80 - 1.10) * 30.0 / 120.0)
    with pytest.raises(CamError) as caught:
        table.kerf(20.0)
    assert str(caught.value) == (
        'kerf table "eps-20-sample": speed 20.0 mm/min is outside the table range 30 to 480 mm/min'
    )


def test_compensated_contour_is_half_kerf_from_target(tmp_path: Path) -> None:
    profile = sample_profile_data()
    profile["kerf_tables"] = [{"name": "eps-20-sample", "description": "",
                               "points": [{"speed": 1.0, "kerf": 0.4}, {"speed": 1000.0, "kerf": 0.4}]}]
    job = load_job(write_yaml(tmp_path / "job.yaml", wing_job()))
    prof = load_profile(write_yaml(tmp_path / "profile.yaml", profile))
    root, tip = validate(job, prof)
    cam = Cam(job, prof, root, tip)
    comp_r, comp_t, _ = cam.compensate()
    for outline, comp in ((root, comp_r), (tip, comp_t)):
        target = [outline.place(p) for p in outline.contour]
        distances = [
            min(segment_distance(point, a, b) for a, b in zip(target, target[1:] + target[:1]))
            for ref, point in comp.items()
            if ref[0] == "outer"
        ]
        # Never closer to the part than half the kerf. At a convex corner the mitred point
        # is h / cos(turn / 2) from the corner; the sharpest corners here (trailing edge)
        # turn by less than 100 degrees, so 0.2 / cos(50°) = 0.31 bounds it.
        assert min(distances) >= 0.2 - 0.001
        assert max(distances) <= 0.31
        # Along the smooth contour the wire runs exactly half the kerf from the part.
        exact = sum(1 for d in distances if abs(d - 0.2) <= 0.002)
        assert exact >= 0.95 * len(distances)
        hole = [outline.place(p) for p in outline.features["spar"].points]
        for i, corner in enumerate(hole):
            moved = comp[("feature", "spar", i)]
            # Rectangle corner moves into the hole by 0.2 along both sides: sqrt(2) * 0.2.
            assert dist(corner, moved) == pytest.approx(math.sqrt(2) * 0.2, abs=1e-9)


def segment_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dz = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dz * dz
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / length2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz)


def test_speed_outside_kerf_table_stops_the_cut(tmp_path: Path) -> None:
    with pytest.raises(CamError, match=r"outside the table range 30 to 480"):
        run(tmp_path, wing_job(cut_speed=600.0))


def test_out_of_table_speed_is_reported_with_the_other_checks(tmp_path: Path) -> None:
    """§11.1: the lookup failure keeps the exact message of machine-profile.md §7, and the
    other checks still run, so every problem is reported together."""
    profile = sample_profile_data()
    profile["axes"][3]["max"] = 30.0  # axis 4 goes higher than this
    with pytest.raises(CamError) as caught:
        run(tmp_path, wing_job(cut_speed=600.0), profile)
    problems = caught.value.problems
    pattern = r'kerf table "eps-20-sample": speed \d+\.\d mm/min is outside the table range 30 to 480 mm/min'
    assert any(re.fullmatch(pattern, p) for p in problems), problems
    assert any(p.startswith("axis 4 (A) goes to") for p in problems), problems


@pytest.mark.parametrize(("table_start", "refused"), [(90.8, False), (91.2, True)])
def test_only_the_settled_kerf_lookup_must_be_inside_the_table(
    tmp_path: Path, table_start: float, refused: bool
) -> None:
    """§11.1: with a constant kerf of 1 mm, the slowest stretch of this job runs at
    90.49 mm/min on the target contour and 91.06 mm/min on the settled offset contour.
    A table from 90.8 is out of range only in the first lookup and the job is cut; a
    table from 91.2 is out of range in the settled lookup and the job is refused."""
    profile = sample_profile_data()
    profile["kerf_tables"] = [{"name": "eps-20-sample", "description": "",
                               "points": [{"speed": table_start, "kerf": 1.0}, {"speed": 1000.0, "kerf": 1.0}]}]
    if refused:
        with pytest.raises(CamError) as caught:
            run(tmp_path, wing_job(), profile)
        pattern = r'kerf table "eps-20-sample": speed 91\.1 mm/min is outside the table range 91\.2 to 1000 mm/min'
        assert [p for p in caught.value.problems if re.fullmatch(pattern, p)], caught.value.problems
    else:
        assert run(tmp_path, wing_job(), profile).count("\nG1 ") > 100


def test_out_of_table_speed_writes_no_program(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    job = write_yaml(tmp_path / "wing.yaml", wing_job(cut_speed=600.0))
    out = tmp_path / "out"
    assert main(["cut", str(job), "--profile", str(SAMPLE_PROFILE), "--out", str(out)]) == 1
    assert "outside the table range" in capsys.readouterr().err
    assert not out.exists() or not any(out.iterdir())


def test_kerf_that_does_not_settle_stops_the_cut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The tapered wing needs more than one lookup from the compensated path.
    monkeypatch.setattr(cam_module, "KERF_ROUNDS", 1)
    with pytest.raises(CamError, match=r"kerf compensation of the outer contour has not settled after 1 rounds"):
        run(tmp_path, wing_job())


def corner(turn: float) -> list[tuple[float, float]]:
    """A closed polygon that turns by `turn` degrees at vertex 1, the origin."""
    t = math.radians(turn)
    return [(-10.0, 0.0), (0.0, 0.0), (10 * math.cos(t), 10 * math.sin(t)), (0.0, -30.0)]


def line_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    return abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) / dist(a, b)


@pytest.mark.parametrize("turn", [EXACT_JOIN_ANGLE - 1.0, EXACT_JOIN_ANGLE + 1.0])
def test_join_with_a_kerf_change_either_side_of_the_threshold(turn: float) -> None:
    """§10.7: below the threshold angle the join takes the mean half kerf; at or above it,
    the exact intersection of the two offset edges."""
    polygon = corner(turn)
    new, _ = geometry.offset_polygon(polygon, [0.5, 0.6, 0.5, 0.5], True, EXACT_JOIN_ANGLE)[1]
    d1 = line_distance(new, polygon[0], polygon[1])
    d2 = line_distance(new, polygon[1], polygon[2])
    if turn < EXACT_JOIN_ANGLE:
        assert d1 == pytest.approx(0.55, abs=1e-12) and d2 == pytest.approx(0.55, abs=1e-12)
    else:
        assert d1 == pytest.approx(0.5, abs=1e-12) and d2 == pytest.approx(0.6, abs=1e-12)


@pytest.mark.parametrize("turn", [EXACT_JOIN_ANGLE - 1.0, EXACT_JOIN_ANGLE + 1.0])
def test_transition_vertices_only_at_nearly_in_line_joins(turn: float) -> None:
    polygon = corner(turn)
    new, fixed = transition_vertices(polygon, [0, 1, 4], 1.0)
    if turn < EXACT_JOIN_ANGLE:
        # One vertex 1.0 either side of the join at vertex 1; none at vertex 0 (a sharp corner).
        assert fixed == [0, 2, 6]
        assert new[1] == pytest.approx((-1.0, 0.0)) and new[2] == polygon[1]
        assert dist(new[3], polygon[1]) == pytest.approx(1.0)
    else:
        assert (new, fixed) == (polygon, [0, 1, 4])


@pytest.mark.parametrize(("delta", "nodes"), [(0.0004, 2), (0.0006, 3)])
def test_breakpoints_merge_only_when_closer_than_0_001_at_both_faces(delta: float, nodes: int) -> None:
    """§10.3: vertex fractions 0.5 and 0.5 + delta on pieces of length 2 are 2 * delta mm apart."""
    a = 1.0 + 2 * delta
    piece_r = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    piece_t = [(0.0, 0.0), (a, 0.0), (a, 2.0 - a)]
    nodes_r, nodes_t = geometry.synchronise(piece_r, piece_t, 10.0, 0.001)
    assert len(nodes_r) == nodes
    assert nodes_r[-1] == piece_r[-1] and nodes_t[-1] == piece_t[-1]
    for piece, found in ((piece_r, nodes_r), (piece_t, nodes_t)):
        assert min(dist(piece[1], p) for p in found) < 0.001
        if nodes == 3:
            assert piece[1] in found


def test_g1_line_equal_after_rounding_is_dropped_and_its_time_kept(tmp_path: Path) -> None:
    """§13: a G1 whose four axis words equal the previous line's is not written; its time
    is added to the next move."""
    job = load_job(write_yaml(tmp_path / "job.yaml", wing_job()))
    prof = load_profile(SAMPLE_PROFILE)
    cam = Cam(job, prof, *validate(job, prof))
    a, b, c = (300.0, 30.0, 300.0, 30.0), (290.0, 30.0, 290.0, 30.0), (280.0, 30.0, 280.0, 30.0)
    moves = [
        Move("rapid", a),
        Move("lead_in", b, 0.5),
        Move("cut", (290.00001, 30.0, 290.0, 30.0), 0.25),  # rounds to b
        Move("cut", c, 0.25),
    ]
    lines = [line for line in cam.gcode_lines(moves).text if line.startswith("G1")]
    assert lines == ["G1 X290.0000 Y30.0000 Z290.0000 A30.0000 F2.00000", "G1 X280.0000 Y30.0000 Z280.0000 A30.0000 F2.00000"]


# --- G-code (job-file.md §13) -----------------------------------------------


def test_gcode_uses_only_the_documented_subset(tmp_path: Path) -> None:
    gcode = run(tmp_path, wing_job())
    commands = []
    for line in gcode.splitlines():
        if line.startswith("("):
            assert line.endswith(")") and "(" not in line[1:-1] and ")" not in line[1:-1]
            continue
        words = line.split()
        commands.append(words[0])
        assert words[0] in {"G21", "G54", "G90", "G93", "G0", "G1", "G94"}
        if words[0] == "G0" and commands.count("G0") == 1:
            # The opening lift (§10.6): the two vertical axes only, before any other motion.
            assert "G1" not in commands
            assert [w[0] for w in words[1:]] == ["Y", "A"]
            for word in words[1:]:
                assert re.fullmatch(r"-?\d+\.\d{4}", word[1:]), word
            continue
        if words[0] in ("G0", "G1"):
            assert [w[0] for w in words[1:5]] == ["X", "Y", "Z", "A"]
            for word in words[1:5]:
                assert re.fullmatch(r"-?\d+\.\d{4}", word[1:]), word
        if words[0] == "G1":
            assert len(words) == 6 and words[5].startswith("F")
            assert re.fullmatch(r"\d+\.\d+", words[5][1:]), words[5]
            digits = words[5][1:].replace(".", "").lstrip("0")
            assert len(digits) >= 6
        elif words[0] == "G0":
            assert len(words) == 5
    assert commands[:7] == ["G21", "G54", "G90", "G93", "G0", "G0", "G0"]  # lift, across, (across,) down
    assert commands[-2:] == ["G0", "G94"]


def entry_line(gcode: str) -> str:
    """The rapid to the entry point: the last G0 before the first G1."""
    lines = gcode.splitlines()
    first_feed = next(i for i, line in enumerate(lines) if line.startswith("G1 "))
    return next(line for line in reversed(lines[:first_feed]) if line.startswith("G0 "))


def test_lower_first_starts_at_the_lower_trailing_edge(tmp_path: Path) -> None:
    upper = entry_line(run(tmp_path, wing_job()))
    lower = entry_line(run(tmp_path, wing_job(direction="lower_first")))
    z = {name: float(line.split()[2][1:]) for name, line in (("upper", upper), ("lower", lower))}
    assert z["lower"] < z["upper"]


def test_open_feature_is_cut_out_and_back(tmp_path: Path) -> None:
    job = wing_job()
    for label, points in (("root", [[150.0, 6.0], [150.0, 15.0]]), ("tip", [[96.0, 4.0], [96.0, 10.0]])):
        job[label]["features"].append({"name": "hinge", "kind": "open", "slit": "lower", "points": points})
    assert run(tmp_path, job).count("G1 ") > 100


# --- checks (job-file.md §11.1, §10.6) --------------------------------------


def test_cutting_speed_ratio_above_limit_is_rejected(tmp_path: Path) -> None:
    job = wing_job()
    job["tip"]["chord"] = 60.0
    job["tip"]["features"][0]["points"] = [[16.0, 2.0], [20.0, 2.0], [20.0, 4.0], [16.0, 4.0]]
    with pytest.raises(CamError, match=r"speed ratio .* exceeds max_speed_ratio 3"):
        run(tmp_path, job)


def test_lead_in_and_exit_are_exempt_from_speed_ratio(tmp_path: Path) -> None:
    profile = sample_profile_data()
    # Contour ratio of this job stays below 1.7; its lead-in ratio is about 2.8.
    profile["limits"]["max_speed_ratio"] = 2.0
    assert "G94" in run(tmp_path, wing_job(), profile)


def test_wire_angle_limit(tmp_path: Path) -> None:
    profile = sample_profile_data()
    profile["limits"]["max_wire_angle"] = 1.0
    with pytest.raises(CamError, match="wire angle .* exceeds max_wire_angle 1"):
        run(tmp_path, wing_job(), profile)


def test_axis_rate_limit(tmp_path: Path) -> None:
    profile = sample_profile_data()
    profile["axes"][2]["max_rate"] = 100.0
    with pytest.raises(CamError, match=r"axis 3 \(Z\) would move at .* above its max_rate 100"):
        run(tmp_path, wing_job(), profile)


def test_rapid_through_block_is_rejected(tmp_path: Path) -> None:
    # Safe position behind the block at mid height: the rapid to the entry crosses the foam.
    with pytest.raises(CamError, match="rapid move to the entry point passes through the foam block"):
        run(tmp_path, wing_job(safe_position=[50.0, 30.0, 50.0, 30.0]))


# --- validation ---------------------------------------------------------------


def test_profile_reports_every_problem_with_its_path() -> None:
    text = SAMPLE_PROFILE.read_text(encoding="utf-8")
    text = text.replace('letter: "Z"', 'letter: "X"').replace("wire_span: 900.0", "wire_span: 900.0\ncolour: \"red\"")
    with pytest.raises(ValidationError) as caught:
        load_profile_text(text)
    problems = caught.value.problems
    assert "axes: the four axis letters must all be different" in problems
    assert "colour: unknown field" in problems


def test_profile_must_be_yaml_file(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationError, match=r"must end in \.yaml"):
        load_profile(path)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda j: j["tip"].pop("features"), 'feature "spar" must be listed in both sections'),
        (lambda j: j["root"]["features"][0].update(slit="lower"), "slit differs from the root"),
        (lambda j: j["tip"]["position"].update(y=150.0), "must differ from root.position.y"),
        (lambda j: j["tip"]["position"].update(y=950.0), "must lie between the carriage planes"),
        (lambda j: j.update(kerf_table="nope"), '"nope" is not a kerf table'),
        (lambda j: j["root"]["position"].update(z=45.0), "root: the section lies outside the block"),
        (lambda j: j.update(mirror="yes"), "mirror: must be true or false"),
    ],
)
def test_job_rule_violations_are_reported(tmp_path: Path, change: Any, expected: str) -> None:
    job = copy.deepcopy(wing_job())
    change(job)
    with pytest.raises(ValidationError) as caught:
        run(tmp_path, job)
    assert any(expected in p for p in caught.value.problems), caught.value.problems


def test_feature_outside_the_contour_is_rejected(tmp_path: Path) -> None:
    job = wing_job()
    # Clark Y at 250 mm chord is about 29 mm thick near u = 75: this hole is above it.
    job["root"]["features"][0]["points"] = [[70.0, 40.0], [80.0, 40.0], [80.0, 45.0], [70.0, 45.0]]
    with pytest.raises(ValidationError, match="must lie strictly inside the outer contour"):
        run(tmp_path, job)


# --- kerf test generator ------------------------------------------------------


def test_kerf_test_cuts_one_slot_per_speed() -> None:
    profile = load_profile(SAMPLE_PROFILE)
    test = KerfTest(100.0, 200.0, 60.0, 40.0, (60.0, 120.0, 240.0), 10.0, 8.0, 10.0, (350.0, 200.0, 350.0, 200.0))
    gcode = kerf_test_gcode(test, profile)
    feeds = [line.split() for line in gcode.splitlines() if line.startswith("G1 ")]
    assert len(feeds) == 3
    for words, speed, z in zip(feeds, (60.0, 120.0, 240.0), (8.0, 18.0, 28.0)):
        values = {w[0]: float(w[1:]) for w in words[1:]}
        assert values["X"] == values["Z"] == 260.0  # front face 300, 40 deep
        assert values["Y"] == values["A"] == z
        # Each feed move covers the 10 mm clearance plus the 40 mm slot.
        assert 1.0 / values["F"] == pytest.approx(50.0 / speed, rel=1e-5)


def test_kerf_test_rejects_too_many_slots() -> None:
    test = KerfTest(100.0, 200.0, 30.0, 40.0, (60.0, 120.0, 240.0), 10.0, 8.0, 10.0, (350.0, 200.0, 350.0, 200.0))
    with pytest.raises(CamError, match="need a block at least 36 mm high"):
        kerf_test_gcode(test, load_profile(SAMPLE_PROFILE))


# --- command line ------------------------------------------------------------


def test_cli_cut_writes_program_report_and_preview(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    job = write_yaml(tmp_path / "wing.yaml", wing_job())
    out = tmp_path / "out"
    assert main(["cut", str(job), "--profile", str(SAMPLE_PROFILE), "--out", str(out)]) == 0
    assert (out / "wing.nc").read_text(encoding="utf-8").startswith("(foamcam")
    assert "Total cut time" in (out / "wing.txt").read_text(encoding="utf-8")
    assert (out / "wing.svg").read_text(encoding="utf-8").startswith("<svg")


def test_cli_reports_errors_and_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    job = write_yaml(tmp_path / "wing.yaml", wing_job(cut_speed=-1.0))
    assert main(["check", str(job), "--profile", str(SAMPLE_PROFILE)]) == 1
    assert "cut_speed: must be greater than 0" in capsys.readouterr().err


@pytest.mark.parametrize("safe", [[550.0, 250.0, 550.0, 250.0], [400.0, 250.0, 400.0, 250.0]])
def test_rapids_never_move_diagonally(tmp_path: Path, safe: list[float]) -> None:
    """§10.6: lift first, then cross at the safe height, then straight down to the
    entry point; leave straight up, then across. No rapid moves horizontal and
    vertical axes together."""
    gcode = run(tmp_path, wing_job(safe_position=safe))
    lines = [line.split() for line in gcode.splitlines() if line.startswith(("G0 ", "G1 "))]
    assert [w[0] for w in lines[0][1:]] == ["Y", "A"]  # the opening lift
    assert lines[0][1:] == [f"Y{safe[1]:.4f}", f"A{safe[3]:.4f}"]
    position = {"Y": safe[1], "A": safe[3]}  # horizontal axes unknown until the next line
    previous_command = "G0"
    for words in lines[1:]:
        values = {w[0]: float(w[1:]) for w in words[1:] if w[0] != "F"}
        if words[0] == "G0":
            moved = {k for k, v in values.items() if k in position and abs(v - position[k]) > 1e-9}
            new = {k for k in values if k not in position}
            changed = moved | new
            assert changed <= {"X", "Z"} or changed <= {"Y", "A"}, f"diagonal rapid: {' '.join(words)}"
            if previous_command == "G1":
                assert changed <= {"Y", "A"}, "the first rapid after cutting must go straight up"
        position.update(values)
        previous_command = words[0]
    assert previous_command == "G0"
