"""Drawn hole channels and the channel exit (holes.py, job-file.md §8 Channels and §10.4)."""

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

ezdxf = pytest.importorskip("ezdxf")

from foamcam.cam import generate, validate
from foamcam.geometry import inside
from foamcam.job import RAPID, load_job_text
from foamcam.profile import Profile, load_profile
from foamdxf.errors import DxfError
from foamdxf.holes import (
    ChannelSpec,
    channel_path,
    preview_channels,
    specs_from_text,
    specs_to_text,
)
from foamdxf.jobgen import CutSettings, Placement, build_job
from foamdxf.outline import read_outline

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


def drawing(path: Path, chord: float, dx: float, circles: list[tuple[float, float, float]]) -> Path:
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline(naca(chord, dx), close=True)
    for x, y, r in circles:
        msp.add_circle((x, y), r)
    doc.saveas(path)
    return path


@pytest.fixture
def dxfs(tmp_path: Path) -> tuple[Path, Path]:
    # a spar tube at 30 % chord and a lightening hole further back, root and tip
    root = drawing(tmp_path / "r.dxf", 180, 0, [(54, 5, 3), (110, 3, 4)])
    tip = drawing(tmp_path / "t.dxf", 130, 20, [(59, 4, 2.5), (100, 3, 3)])
    return root, tip


def spar(path: Path) -> tuple[Point, ...]:
    """The root spar hole (centre 54, 5) as read from the drawing."""
    return min(read_outline(path).holes, key=lambda h: math.dist(h[0], (54, 5)))


def job(dxfs: tuple[Path, Path], profile: Profile, **settings: object) -> str:
    placement = Placement(span=300, stock_length=220, stock_height=40)
    return build_job(read_outline(dxfs[0]), read_outline(dxfs[1]), profile, placement,
                     CutSettings(**settings)).text  # type: ignore[arg-type]


def test_straight_channel_at_an_angle_and_offset(dxfs: tuple[Path, Path], profile: Profile, tmp_path: Path) -> None:
    spec = ChannelSpec("straight", angle=120.0, offset=1.0)
    text = job(dxfs, profile, channels={"hole1": spec})
    parsed = load_job_text(text, tmp_path)
    feature = parsed.root.features[0]
    assert len(feature.channel) == 1 and parsed.tip.features[0].channel  # one point: a straight line
    assert parsed.root.features[1].channel == ()  # the other hole keeps its automatic slit
    # the hole starts where the line (direction 120 degrees, 1 mm to its left of the centre) leaves the circle
    d = (math.cos(math.radians(120)), math.sin(math.radians(120)))
    path = channel_path(spec, tuple(naca(180)), spar(dxfs[0]))
    assert math.dist(path.entry, (54, 5)) == pytest.approx(3.0, abs=0.02)
    side = d[0] * (path.entry[1] - 5) - d[1] * (path.entry[0] - 54)
    assert side == pytest.approx(1.0, abs=0.02)  # 1 mm to the left of the centre line
    assert (path.entry[0] - 54) * d[0] + (path.entry[1] - 5) * d[1] > 0  # on the far side, facing out
    assert path.slit == "upper"
    result = generate(parsed, profile)
    assert parsed.channel_exit == RAPID and result.total_time > 0


def test_curved_channel_leaves_the_hole_along_its_edge(dxfs: tuple[Path, Path], profile: Profile,
                                                       tmp_path: Path) -> None:
    spec = ChannelSpec("curved", angle=90.0, entry=30.0, radius=4.0)
    hole = spar(dxfs[0])
    path = channel_path(spec, tuple(naca(180)), hole)
    radial = (path.entry[0] - 54, path.entry[1] - 5)
    first = (path.points[0][0] - path.entry[0], path.points[0][1] - path.entry[1])
    cos = abs(radial[0] * first[0] + radial[1] * first[1]) / (math.hypot(*radial) * math.hypot(*first))
    assert cos < 0.1  # the first step runs along the circle's edge (perpendicular to the radius)
    last = (path.points[-1][0] - path.points[-2][0], path.points[-1][1] - path.points[-2][1])
    assert math.degrees(math.atan2(last[1], last[0])) == pytest.approx(90.0, abs=0.5)  # ends heading up
    assert path.clockwise  # leaving at 30 degrees and turning up is shorter going right: cut clockwise
    parsed = load_job_text(job(dxfs, profile, channels={"hole1": spec}), tmp_path)
    assert len(parsed.root.features[0].channel) == len(parsed.tip.features[0].channel) > 2
    assert generate(parsed, profile).total_time > 0


def test_bad_channels_name_the_hole(dxfs: tuple[Path, Path], profile: Profile) -> None:
    with pytest.raises(DxfError, match="hole1.*misses the hole"):
        job(dxfs, profile, channels={"hole1": ChannelSpec("straight", 90.0, 5.0)})
    # the spar's channel ends in the lightening hole and that hole's channel back in the spar: no way out
    loop = {"hole1": ChannelSpec("straight", 0.0, 0.0), "hole2": ChannelSpec("straight", 180.0, 0.0)}
    with pytest.raises(DxfError, match="never reach the outer contour"):
        job(dxfs, profile, channels=loop)


def test_channel_into_another_hole(dxfs: tuple[Path, Path], profile: Profile, tmp_path: Path) -> None:
    # pointing at the lightening hole behind it: the spar is reached from inside hole2
    text = job(dxfs, profile, channels={"hole1": ChannelSpec("straight", 0.0, 0.0)})
    placement = Placement(span=300, stock_length=220, stock_height=40)
    generated = build_job(read_outline(dxfs[0]), read_outline(dxfs[1]), profile, placement,
                          CutSettings(channels={"hole1": ChannelSpec("straight", 0.0, 0.0)}))
    assert [h.into for h in generated.holes] == ["hole2", ""]
    assert "into hole2" in "\n".join(generated.summary)
    parsed = load_job_text(text, tmp_path)
    for section in (parsed.root, parsed.tip):
        spar_f, other = section.features
        assert len(spar_f.channel) == 1  # a straight line from the spar into hole2
        assert inside(spar_f.channel[-1], list(other.points))  # §8: the last point lies inside the parent
    assert generate(parsed, profile).total_time > 0
    # only hole2 has a slit on the outer contour; the spar's slit point is a vertex of hole2,
    # at the same index at root and tip (two vertices were added around it, §8)
    root, tip = validate(parsed, profile)
    assert set(root.slit_index) == set(tip.slit_index) == {"hole2"}
    assert root.branch_index["hole1"] == tip.branch_index["hole1"]
    assert root.branch_index["hole1"][0] == "hole2"


def test_preview_reports_problems_without_the_cam(dxfs: tuple[Path, Path], profile: Profile) -> None:
    placement = Placement(span=300, stock_length=220, stock_height=40)
    good = build_job(read_outline(dxfs[0]), read_outline(dxfs[1]), profile, placement, CutSettings())
    loop = {"hole1": ChannelSpec("straight", 0.0, 0.0), "hole2": ChannelSpec("straight", 180.0, 0.0)}
    found = preview_channels(good.holes, loop, good.outers)
    assert set(found) == {"hole1", "hole2"} and "never reach the outer contour" in found["hole1"].problem
    into = preview_channels(good.holes, {"hole1": ChannelSpec("straight", 0.0, 0.0)}, good.outers)["hole1"]
    assert into.problem == "" and into.root is not None and into.root.into == "hole2"
    ok = preview_channels(good.holes, {"hole1": ChannelSpec("straight", 100.0, 0.5)}, good.outers)
    assert ok["hole1"].problem == "" and ok["hole1"].root is not None


def test_channel_exit_field(dxfs: tuple[Path, Path], profile: Profile, tmp_path: Path) -> None:
    assert 'channel_exit: "rapid"' in job(dxfs, profile)  # the default
    assert "channel_exit" not in job(dxfs, profile, channel_exit="cut")
    assert load_job_text(job(dxfs, profile, channel_exit=600.0), tmp_path).channel_exit == 600.0
    with pytest.raises(DxfError, match="channel exit"):
        job(dxfs, profile, channel_exit="slow")


def test_specs_round_trip_and_form(dxfs: tuple[Path, Path]) -> None:
    from foamdxf import gui

    specs = {"hole1": ChannelSpec("curved", 80.0, 0.0, 45.0, 6.0), "hole2": ChannelSpec()}
    text = specs_to_text(specs)
    assert specs_from_text(text) == {"hole1": specs["hole1"]}  # automatic holes are not stored
    with pytest.raises(DxfError):
        specs_from_text('{"hole1": {"shape": "zigzag"}}')
    values = dict(gui.DEFAULTS) | {"root": str(dxfs[0]), "tip": str(dxfs[1]), "profile": str(PROFILE),
                                   "span": "300", "stock_length": "220", "stock_height": "40",
                                   "channels": text, "channel_exit": "450"}
    settings = gui.request_from_form(values).settings
    assert settings.channels == {"hole1": specs["hole1"]} and settings.channel_exit == 450.0
    with pytest.raises(gui.FormError, match="Channel exit"):
        gui.request_from_form(values | {"channel_exit": "fast"})


def test_cli_channel_exit(dxfs: tuple[Path, Path], tmp_path: Path) -> None:
    from foamdxf.cli import main

    out = tmp_path / "out"
    assert main([str(dxfs[0]), str(dxfs[1]), "--span", "300", "--stock", "220,40", "--profile", str(PROFILE),
                 "--channel-exit", "cut", "--out", str(out)]) == 0
    assert "channel_exit" not in next(out.glob("*.yaml")).read_text(encoding="utf-8")
    nc = next(out.glob("*.nc")).read_text(encoding="utf-8").splitlines()
    feeds = [i for i, line in enumerate(nc) if line.startswith("G1")]
    assert not any(nc[i].startswith("G0") for i in range(feeds[0], feeds[-1]))  # no rapid inside the cut


def test_window_edits_a_channel_by_dragging(dxfs: tuple[Path, Path], tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    from foamdxf import gui

    monkeypatch.setattr(gui, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("foamdxf.machine.MACHINE_FILE", tmp_path / "machine.json")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tkinter")
    root.withdraw()
    try:
        win = gui.SlicerWindow(root)
        values = dict(gui.DEFAULTS) | {"root": str(dxfs[0]), "tip": str(dxfs[1]), "profile": str(PROFILE),
                                       "span": "300", "stock_length": "220", "stock_height": "40",
                                       "out": str(tmp_path / "out")}
        for key, value in values.items():
            win.vars[key].set(value)
        assert win.preview()
        win.edit_channels.set(True)
        win._edit_channels_changed()
        assert list(win.ch_rows) == ["hole1", "hole2"]  # one block per hole in the pane
        assert win.channel_pane.winfo_manager() == "pack"
        assert not win.channel_handles  # automatic channels have no handles
        assert win.ch_rows["hole2"].note.get().startswith("automatic")
        win.ch_rows["hole1"].vars["shape"].set("straight")  # the radio button
        win._channel_shape_changed("hole1")  # starts straight up, from the automatic upper slit
        assert specs_from_text(win.vars["channels"].get())["hole1"] == ChannelSpec("straight", 90.0, 0.0)
        assert ("hole1", "angle") in win.channel_handles

        # drag the square handle to the upper left of the hole: x runs right to left on screen
        assert win.plan is not None
        sx, sz = win.plan.generated.shift
        target = win._to_canvas([(54 + sx - 10, 5 + sz + 10)])
        hx, hy = win.channel_handles[("hole1", "angle")]
        win._press(SimpleNamespace(x=hx, y=hy))  # type: ignore[arg-type]
        win._drag(SimpleNamespace(x=target[0], y=target[1]))  # type: ignore[arg-type]
        win._release(SimpleNamespace(x=target[0], y=target[1]))  # type: ignore[arg-type]
        spec = specs_from_text(win.vars["channels"].get())["hole1"]
        assert spec.angle == pytest.approx(135, abs=2)
        assert "channel:" not in win.plan.generated.text  # editing does not cut: the old cut stays
        assert win.ch_pending.get().startswith("changed")
        win._cut_channels()  # the Cut button
        assert win.plan is not None and "channel:" in win.plan.generated.text
        assert win.ch_pending.get() == ""

        # an impossible channel keeps the last good cut on screen and says why
        good = win.plan
        win.ch_rows["hole1"].vars["offset"].set("5")  # 5 mm beside the centre of a 3 mm hole: misses it
        win._channel_fields_changed("hole1")
        win.draw()
        assert win.ch_rows["hole1"].note.get().startswith("cannot cut")
        win._cut_channels()
        assert win.plan is good and win.status.get().startswith("Cannot cut this channel")

        # the other hole's block edits only that hole; a channel from hole2 into hole1 says so
        win.ch_rows["hole2"].vars["shape"].set("straight")
        win._channel_shape_changed("hole2")
        win.ch_rows["hole2"].vars["angle"].set("180")
        win._channel_fields_changed("hole2")
        win._reset_channel("hole1")
        win.draw()
        assert win.ch_selected.get() == "hole1"
        assert "hole1" not in specs_from_text(win.vars["channels"].get())
        assert specs_from_text(win.vars["channels"].get())["hole2"].angle == 180.0
        assert win.ch_rows["hole2"].note.get() == "ends in hole1"
        win.edit_channels.set(False)
        win._edit_channels_changed()
        assert win.channel_pane.winfo_manager() == ""
    finally:
        root.destroy()


def test_preview_path_follows_rapid_channel_exits(dxfs: tuple[Path, Path], tmp_path: Path) -> None:
    """The preview draws the wire back out along each channel, as the G-code runs it,
    not a straight line from the hole to the next cut (the rapids were left out)."""
    from foamdxf import gui
    from foamdxf.pipeline import make_job, preview_geometry, run_cam

    values = dict(gui.DEFAULTS) | {"root": str(dxfs[0]), "tip": str(dxfs[1]), "profile": str(PROFILE),
                                   "span": "300", "stock_length": "220", "stock_height": "40",
                                   "channels": specs_to_text({"hole1": ChannelSpec("curved", 90.0, 0.0, 30.0, 4.0)})}
    plan = make_job(gui.request_from_form(values))
    result = run_cam(plan)
    geometry = preview_geometry(plan, result)
    feeds = [i for i, m in enumerate(result.moves) if m.kind != "rapid"]
    inner = [m.axes for m in result.moves[feeds[0] : feeds[-1]] if m.kind == "rapid"]
    assert inner  # channel_exit "rapid" (the default)
    assert geometry.phases.count("rapid") == len(inner)
    path = list(zip(geometry.tower_12, geometry.tower_34, strict=True))
    for a in inner:
        assert any(math.dist(a, (*p, *q)) < 1e-9 for p, q in path)
    assert all(b >= a for a, b in zip(geometry.times, geometry.times[1:]))
