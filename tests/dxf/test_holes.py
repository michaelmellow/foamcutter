"""Inner contours cut through a channel (src/foamdxf/holes.py, job-file.md §8)."""

import math
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

from foamcam.cam import generate
from foamcam.job import load_job_text
from foamcam.profile import Profile, load_profile
from foamdxf.errors import DxfError
from foamdxf.holes import pair_holes
from foamdxf.jobgen import CutSettings, Placement, build_job
from foamdxf.outline import read_outline
from foamdxf.pipeline import Request, make_job, preview_geometry, run_cam

REPO = Path(__file__).resolve().parents[2]
PROFILE = REPO / "examples" / "profiles" / "real-machine.yaml"

Point = tuple[float, float]


@pytest.fixture(scope="module")
def profile() -> Profile:
    return load_profile(PROFILE)


def naca(chord: float, dx: float = 0.0, n: int = 60) -> list[Point]:
    """NACA 2412 as one closed loop: trailing edge, upper side, leading edge, lower side."""
    m, p, t = 0.02, 0.4, 0.12
    upper, lower = [], []
    for i in range(n):
        x = (1 - math.cos(math.pi * i / (n - 1))) / 2
        yt = 5 * t * (0.2969 * math.sqrt(x) - 0.126 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1015 * x**4)
        yc = m / p**2 * (2 * p * x - x * x) if x < p else m / (1 - p) ** 2 * (1 - 2 * p + 2 * p * x - x * x)
        upper.append((dx + x * chord, (yc + yt) * chord))
        lower.append((dx + x * chord, (yc - yt) * chord))
    return upper[::-1] + lower[1:-1]


def drawing(path: Path, chord: float, dx: float, circles: list[tuple[float, float, float]] = (),  # type: ignore[assignment]
            rects: list[tuple[float, float, float, float]] = ()) -> Path:  # type: ignore[assignment]
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline(naca(chord, dx), close=True)
    for x, y, r in circles:
        msp.add_circle((x, y), r)
    for x0, y0, x1, y1 in rects:
        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True)
    doc.saveas(path)
    return path


def job_for(root: Path, tip: Path, profile: Profile, channel: str = "shortest") -> str:
    placement = Placement(span=300, stock_length=220, stock_height=40)
    return build_job(read_outline(root), read_outline(tip), profile, placement, CutSettings(channel=channel)).text


def test_circular_spar_hole_is_cut_through_a_channel(tmp_path: Path, profile: Profile) -> None:
    # a 6 mm spar tube at 30 % chord, a little above the chord line
    root = drawing(tmp_path / "r.dxf", 180, 0, circles=[(54, 5, 3)])
    tip = drawing(tmp_path / "t.dxf", 130, 20, circles=[(59, 4, 2.5)])
    text = job_for(root, tip, profile)
    assert text.count('kind: "hole"') == 2 and text.count('slit: "upper"') == 2
    job = load_job_text(text, tmp_path)
    assert len(job.root.features[0].points) == len(job.tip.features[0].points) >= 16
    first = job.root.features[0].points[0]
    assert first == pytest.approx((54.0, 5.0 + 3.0), abs=0.02)  # starts at the top of the circle
    result = generate(job, profile)
    assert result.total_time > 0

    # the wire goes in, round the hole and out the same way: the root path passes the
    # hole's top twice (in and out) and gets within the half kerf of its whole edge
    plan = make_job(Request(root, tip, PROFILE, Placement(300, 220, 40), CutSettings()))
    g = preview_geometry(plan, run_cam(plan))
    assert len(g.root_holes) == 1
    centre = (sum(p[0] for p in g.root_holes[0]) / len(g.root_holes[0]),
              sum(p[1] for p in g.root_holes[0]) / len(g.root_holes[0]))
    near = [p for p in g.root_cut if math.dist(p, centre) < 3.0]
    assert near and all(math.dist(p, centre) > 1.5 for p in near)  # offset into the hole by half a kerf
    angles = {round(math.degrees(math.atan2(p[1] - centre[1], p[0] - centre[0])) / 30) for p in near}
    assert len(angles) >= 12  # all the way round


def test_lower_channel_and_shortest_side(tmp_path: Path, profile: Profile) -> None:
    root = drawing(tmp_path / "r.dxf", 180, 0, circles=[(54, -2, 3)])  # nearer the lower surface
    tip = drawing(tmp_path / "t.dxf", 130, 20, circles=[(59, -1.5, 2.5)])
    assert job_for(root, tip, profile).count('slit: "lower"') == 2
    assert job_for(root, tip, profile, channel="upper").count('slit: "upper"') == 2
    generate(load_job_text(job_for(root, tip, profile), tmp_path), profile)


def test_rectangular_slot_keeps_its_corners(tmp_path: Path, profile: Profile) -> None:
    root = drawing(tmp_path / "r.dxf", 180, 0, rects=[(50, -3, 62, 7)])
    tip = drawing(tmp_path / "t.dxf", 130, 20, rects=[(55, -2, 63, 5)])
    job = load_job_text(job_for(root, tip, profile), tmp_path)
    pts = job.root.features[0].points
    for corner in ((50, -3), (62, -3), (62, 7), (50, 7)):
        assert any(math.dist(p, corner) < 1e-6 for p in pts)
    assert generate(job, profile).total_time > 0


def test_tube_drawn_with_both_diameters_is_one_hole(tmp_path: Path) -> None:
    out = read_outline(drawing(tmp_path / "r.dxf", 180, 0, circles=[(54, 5, 3), (54, 5, 2)]))
    assert len(out.holes) == 1
    assert max(math.dist(p, (54, 5)) for p in out.holes[0]) == pytest.approx(3.0, abs=0.02)
    assert any("inside a hole ignored" in w for w in out.warnings)


def test_holes_must_be_at_both_ends(tmp_path: Path, profile: Profile) -> None:
    root = drawing(tmp_path / "r.dxf", 180, 0, circles=[(54, 5, 3)])
    tip = drawing(tmp_path / "t.dxf", 130, 20)
    with pytest.raises(DxfError, match="root has 1 inner contour"):
        job_for(root, tip, profile)


def test_two_holes_pair_by_position() -> None:
    outer = tuple(naca(200))
    r = (tuple((40 + 2 * math.cos(a / 8 * math.pi), 4 + 2 * math.sin(a / 8 * math.pi)) for a in range(16)),
         tuple((120 + 2 * math.cos(a / 8 * math.pi), 3 + 2 * math.sin(a / 8 * math.pi)) for a in range(16)))
    t = (r[1], r[0])  # listed the other way round
    pairs = pair_holes(outer, r, outer, t)
    assert [p.name for p in pairs] == ["hole1", "hole2"]
    assert pairs[0].root[0][0] == pytest.approx(40, abs=0.01) and pairs[0].tip[0][0] == pytest.approx(40, abs=0.01)


def test_channel_choice_in_form_and_cli(tmp_path: Path, profile: Profile) -> None:
    from foamdxf import gui
    from foamdxf.cli import main

    root = drawing(tmp_path / "r.dxf", 180, 0, circles=[(54, 5, 3)])
    tip = drawing(tmp_path / "t.dxf", 130, 20, circles=[(59, 4, 2.5)])
    values = dict(gui.DEFAULTS) | {"root": str(root), "tip": str(tip), "profile": str(PROFILE), "span": "300",
                                   "stock_length": "220", "stock_height": "40", "channel": "lower"}
    assert gui.request_from_form(values).settings.channel == "lower"
    out = tmp_path / "out"
    assert main([str(root), str(tip), "--span", "300", "--stock", "220,40", "--profile", str(PROFILE),
                 "--channel", "lower", "--out", str(out)]) == 0
    assert (out / "r-t.yaml").read_text(encoding="utf-8").count('slit: "lower"') == 2
