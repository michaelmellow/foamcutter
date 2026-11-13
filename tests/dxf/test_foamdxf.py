"""Tests for the DXF front end (src/foamdxf)."""

import math
from itertools import pairwise
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

from foamcam.cam import generate
from foamcam.job import load_job_text
from foamcam.profile import Profile, load_profile
from foamcam.sections import build_outline
from foamdxf.cli import main
from foamdxf.errors import DxfError
from foamdxf.jobgen import CutSettings, Placement, build_job
from foamdxf.outline import (
    _segment_distance,
    read_outline,
    sharpen_trailing_edge,
    signed_area,
    simplify,
)

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "examples" / "profiles" / "sample-machine.yaml"

Point = tuple[float, float]


@pytest.fixture(scope="module")
def profile() -> Profile:
    return load_profile(PROFILE_PATH)


def naca(
    chord: float, thickness: float = 0.12, camber: float = 0.02, n: int = 60, closed_te: bool = False
) -> tuple[list[Point], list[Point]]:
    """Upper and lower NACA 4-digit surfaces, leading edge to trailing edge, mm."""
    p = 0.4
    upper, lower = [], []
    for i in range(n):
        x = (1 - math.cos(math.pi * i / (n - 1))) / 2
        yt = 5 * thickness * (0.2969 * math.sqrt(x) - 0.126 * x - 0.3516 * x**2 + 0.2843 * x**3 - (0.1036 if closed_te else 0.1015) * x**4)
        yc = camber / p**2 * (2 * p * x - x**2) if x < p else camber / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x**2)
        upper.append((x * chord, (yc + yt) * chord))
        lower.append((x * chord, (yc - yt) * chord))
    return upper, lower


def shift(points: list[Point], dx: float, dy: float) -> list[Point]:
    return [(x + dx, y + dy) for x, y in points]


def save(path: Path, build: object, units: int = 4) -> Path:
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = units
    build(doc.modelspace())  # type: ignore[operator]
    doc.saveas(path)
    return path


def closed_polyline(path: Path, chord: float, dx: float = 0.0, dy: float = 0.0, clockwise: bool = False) -> Path:
    upper, lower = naca(chord)
    loop = shift(upper[::-1] + lower[1:-1], dx, dy)  # TE, upper, LE, lower (TE gap closed by the polyline)
    if clockwise:
        loop = loop[::-1]
    return save(path, lambda msp: msp.add_lwpolyline(loop, close=True))


# --- reading outlines ------------------------------------------------------------------------


def test_closed_polyline_is_read_in_selig_order(tmp_path: Path) -> None:
    out = read_outline(closed_polyline(tmp_path / "a.dxf", 200.0))
    pts = out.points
    assert out.warnings == ()
    assert pts[0][0] == pytest.approx(200.0)  # starts at the trailing edge
    assert pts[out.le_index] == pytest.approx((0.0, 0.0), abs=1e-9)
    upper = pts[: out.le_index]
    lower = pts[out.le_index + 1 :]
    assert sum(p[1] for p in upper) / len(upper) > sum(p[1] for p in lower) / len(lower)
    assert out.chord == pytest.approx(200.0)


def test_clockwise_drawing_is_reoriented(tmp_path: Path) -> None:
    ccw = read_outline(closed_polyline(tmp_path / "a.dxf", 150.0))
    cw = read_outline(closed_polyline(tmp_path / "b.dxf", 150.0, clockwise=True))
    assert len(cw.points) == len(ccw.points)
    for a, b in zip(cw.points, ccw.points, strict=True):
        assert a == pytest.approx(b)


def test_open_trailing_edge_is_closed_with_a_warning(tmp_path: Path) -> None:
    upper, lower = naca(200.0)  # TE points differ slightly: an open polyline
    path = save(tmp_path / "open.dxf", lambda msp: msp.add_lwpolyline(upper[::-1] + lower[1:]))
    out = read_outline(path)
    assert any("closed a" in w for w in out.warnings)
    assert out.points[0] == pytest.approx(upper[-1])
    assert out.points[-1] == pytest.approx(lower[-1])


def test_pieces_splines_and_lines_are_joined(tmp_path: Path) -> None:
    upper, lower = naca(120.0)

    def build(msp: object) -> None:
        msp.add_spline(upper)  # type: ignore[attr-defined]  # fit-point splines, as CAD programs write them
        msp.add_spline(lower[::-1])  # type: ignore[attr-defined]  # drawn in the other direction
        msp.add_line(upper[-1], lower[-1])  # type: ignore[attr-defined]  # blunt trailing-edge face

    out = read_outline(save(tmp_path / "pieces.dxf", build))
    assert out.warnings == ()
    assert out.chord == pytest.approx(120.0)
    drawn = upper[::-1] + lower[1:]
    assert abs(signed_area(list(out.points))) == pytest.approx(abs(signed_area(drawn)), rel=0.01)
    # blunt TE: start at the upper corner, end at the lower corner
    assert out.points[0] == pytest.approx(upper[-1], abs=1e-6)
    assert out.points[-1] == pytest.approx(lower[-1], abs=1e-6)


def test_inch_drawing_is_scaled_to_mm(tmp_path: Path) -> None:
    upper, lower = naca(4.0)  # 4 inches
    path = save(tmp_path / "in.dxf", lambda msp: msp.add_lwpolyline(upper[::-1] + lower[1:-1], close=True), units=1)
    assert read_outline(path).chord == pytest.approx(101.6)


def test_inner_loop_is_ignored_with_a_warning(tmp_path: Path) -> None:
    upper, lower = naca(200.0)

    def build(msp: object) -> None:
        msp.add_lwpolyline(upper[::-1] + lower[1:-1], close=True)  # type: ignore[attr-defined]
        msp.add_lwpolyline([(60, 0), (70, 0), (70, 8), (60, 8)], close=True)  # type: ignore[attr-defined]

    out = read_outline(save(tmp_path / "slot.dxf", build))
    assert out.chord == pytest.approx(200.0)
    assert any("largest is used" in w for w in out.warnings)


def test_open_outline_with_a_big_gap_is_an_error(tmp_path: Path) -> None:
    upper, _ = naca(200.0)
    path = save(tmp_path / "half.dxf", lambda msp: msp.add_lwpolyline(upper))
    with pytest.raises(DxfError, match="not closed"):
        read_outline(path)


def test_empty_drawing_is_an_error(tmp_path: Path) -> None:
    path = save(tmp_path / "empty.dxf", lambda msp: msp.add_text("nothing"))
    with pytest.raises(DxfError, match="no lines"):
        read_outline(path)


def test_dense_points_are_thinned_within_tolerance(tmp_path: Path) -> None:
    upper, lower = naca(200.0, n=400)
    path = save(tmp_path / "dense.dxf", lambda msp: msp.add_lwpolyline(upper[::-1] + lower[1:-1], close=True))
    full = read_outline(path, simplify_tol=0)
    thin = read_outline(path, simplify_tol=0.01)
    assert len(thin.points) < len(full.points) / 2
    assert thin.points[0] == full.points[0] and thin.points[-1] == full.points[-1]
    assert thin.points[thin.le_index] == full.points[full.le_index]
    loop = list(thin.points) + [thin.points[0]]
    for p in full.points:
        assert min(_segment_distance(p, a, b) for a, b in pairwise(loop)) <= 0.01 + 1e-12


def test_simplify_keeps_ends_and_corners() -> None:
    square_ish: list[Point] = [(0, 0), (1, 0.001), (2, 0), (2, 1), (2, 2)]
    assert simplify(square_ish, 0.01) == [(0, 0), (2, 0), (2, 2)]


def rounded_te_dxf(path: Path, chord: float) -> Path:
    """Closed outline whose trailing edge is a tiny arc 0.08 mm across, as CAD airfoils often have."""
    upper, lower = naca(chord, camber=0.0, closed_te=True)  # sharp trailing edge at (chord, 0)
    tip_arc = [(chord - 0.03, -0.02), (chord + 0.01, 0.0), (chord - 0.03, 0.02)]
    loop = tip_arc + upper[::-1][1:-1] + lower[:-1]
    return save(path, lambda msp: msp.add_lwpolyline(loop, close=True))


def test_rounded_trailing_edge_is_made_sharp(tmp_path: Path) -> None:
    out = read_outline(rounded_te_dxf(tmp_path / "r.dxf", 200.0))
    assert any("rounded trailing edge" in w for w in out.warnings)
    assert out.points[0] == pytest.approx((200.01, 0.0))
    assert all(math.dist(p, out.points[0]) >= 0.3 for p in out.points[1:])


def test_blunt_trailing_edge_face_is_kept() -> None:
    blunt: list[Point] = [(100.0, 0.5), (50.0, 5.0), (0.0, 0.0), (50.0, -4.0), (100.0, -0.5)]
    assert sharpen_trailing_edge(blunt, 0.3) == (blunt, 0)


def test_rounded_trailing_edge_job_settles_and_cuts(tmp_path: Path, profile: Profile) -> None:
    # A 0.08 mm trailing edge used to make foamcam's kerf iteration oscillate (§10.7 step 6).
    root = read_outline(rounded_te_dxf(tmp_path / "root.dxf", 200.0))
    tip = read_outline(rounded_te_dxf(tmp_path / "tip.dxf", 170.0))
    job = build_job(root, tip, profile, Placement(270.0, 220.0, 45.0), CutSettings())
    result = generate(load_job_text(job.text, tmp_path), profile)
    assert result.total_time > 0


# --- building the job ------------------------------------------------------------------------


def make_job(tmp_path: Path, profile: Profile, span: float = 500.0, stock: tuple[float, float] = (260.0, 60.0),
             tip_shift: tuple[float, float] = (40.0, 3.0), **kw: float) -> tuple[str, list[Point], list[Point]]:
    root = read_outline(closed_polyline(tmp_path / "root.dxf", 200.0))
    tip = read_outline(closed_polyline(tmp_path / "tip.dxf", 140.0, *tip_shift))
    placement = Placement(span, stock[0], stock[1], **kw)
    job = build_job(root, tip, profile, placement, CutSettings(min_te_thickness=0.0))  # exact shape
    return job.text, list(root.points), list(tip.points)


def test_job_reproduces_the_drawn_outlines(tmp_path: Path, profile: Profile) -> None:
    text, root_pts, tip_pts = make_job(tmp_path, profile)
    job = load_job_text(text, tmp_path)
    placed = {}
    for section in (job.root, job.tip):
        problems: list[str] = []
        outline = build_outline(section, job.min_te_thickness, problems)
        assert outline is not None and problems == []
        placed[section.label] = [outline.place(p) for p in outline.contour]
    # the same rigid shift is applied to both outlines: sweep and dihedral are kept
    dx = placed["root"][0][0] - root_pts[0][0]
    dz = placed["root"][0][1] - root_pts[0][1]
    for label, drawn in (("root", root_pts), ("tip", tip_pts)):
        assert len(placed[label]) == len(drawn)
        for (x, z), (px, pz) in zip(drawn, placed[label], strict=True):
            assert px == pytest.approx(x + dx, abs=1e-5)
            assert pz == pytest.approx(z + dz, abs=1e-5)


def test_section_planes_are_centred(tmp_path: Path, profile: Profile) -> None:
    job = load_job_text(make_job(tmp_path, profile, span=500.0)[0], tmp_path)
    assert job.root.y == pytest.approx((profile.wire_span - 500.0) / 2)
    assert job.tip.y - job.root.y == pytest.approx(500.0)


def test_outlines_are_centred_in_the_stock(tmp_path: Path, profile: Profile) -> None:
    job = load_job_text(make_job(tmp_path, profile, stock=(300.0, 80.0))[0], tmp_path)
    xs, zs = [], []
    for section in (job.root, job.tip):
        outline = build_outline(section, 0.0, [])
        assert outline is not None
        for p in outline.contour:
            x, z = outline.place(p)
            xs.append(x)
            zs.append(z)
    b = job.block
    assert (min(xs) - b.x) == pytest.approx(b.x + b.length - max(xs), abs=1e-6)
    assert (min(zs) - profile.table_z) == pytest.approx(profile.table_z + b.height - max(zs), abs=1e-6)


def test_block_x_default_is_centred_in_travel_and_can_be_set(tmp_path: Path, profile: Profile) -> None:
    job = load_job_text(make_job(tmp_path, profile, stock=(260.0, 60.0))[0], tmp_path)
    a1, a3 = profile.axes[0], profile.axes[2]
    mid = (max(a1.min, a3.min) + min(a1.max, a3.max)) / 2
    assert job.block.x + (job.block.length + job.lead_in) / 2 == pytest.approx(mid)
    job = load_job_text(make_job(tmp_path, profile, block_x=50.0)[0], tmp_path)
    assert job.block.x == 50.0


def test_stock_too_small_is_an_error(tmp_path: Path, profile: Profile) -> None:
    with pytest.raises(DxfError, match="the stock is"):
        make_job(tmp_path, profile, stock=(150.0, 60.0))


def test_span_must_fit_between_the_towers(tmp_path: Path, profile: Profile) -> None:
    with pytest.raises(DxfError, match="wire span"):
        make_job(tmp_path, profile, span=profile.wire_span)


def test_generated_job_cuts(tmp_path: Path, profile: Profile) -> None:
    job = load_job_text(make_job(tmp_path, profile)[0], tmp_path)
    result = generate(job, profile)
    assert result.total_time > 0
    assert result.gcode.count("\nG1 ") > 100


def test_cli_writes_job_and_gcode(tmp_path: Path) -> None:
    root = closed_polyline(tmp_path / "root.dxf", 200.0)
    tip = closed_polyline(tmp_path / "tip.dxf", 140.0, 30.0, 0.0)
    out = tmp_path / "out"
    code = main([str(root), str(tip), "--span", "500", "--stock", "260,60",
                 "--profile", str(PROFILE_PATH), "--out", str(out)])
    assert code == 0
    for suffix in (".yaml", ".nc", ".txt", ".svg"):
        assert (out / f"root-tip{suffix}").is_file()


def test_cli_reports_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = closed_polyline(tmp_path / "root.dxf", 200.0)
    code = main([str(root), str(root), "--span", "500", "--stock", "100,60",
                 "--profile", str(PROFILE_PATH), "--out", str(tmp_path)])
    assert code == 1
    assert "the stock is" in capsys.readouterr().err
