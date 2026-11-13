"""Tests for the shared pipeline and the GUI of the DXF front end."""

from pathlib import Path

import pytest

pytest.importorskip("ezdxf")

import tkinter as tk
from tkinter import ttk

from foamdxf import gui
from foamdxf.gui import FormError, SlicerWindow, request_from_form
from foamdxf.pipeline import alignment_edges, make_job, preview_geometry, run_cam
from foamdxf.view3d import Camera

REPO = Path(__file__).resolve().parents[2]
ROOT_DXF = REPO / "examples" / "dxf" / "dryrun-root.dxf"
TIP_DXF = REPO / "examples" / "dxf" / "dryrun-tip.dxf"
PROFILE = REPO / "examples" / "profiles" / "real-machine.yaml"


def form(**changes: str) -> dict[str, str]:
    values = dict(gui.DEFAULTS)
    values |= {"root": str(ROOT_DXF), "tip": str(TIP_DXF), "profile": str(PROFILE),
               "span": "400", "stock_length": "220", "stock_height": "40"}
    values |= changes
    return values


def test_form_becomes_a_request() -> None:
    request = request_from_form(form(block_x="", speed="150,5", kerf_table=""))
    assert request.placement.block_x is None
    assert request.settings.cut_speed == 150.5  # decimal comma accepted
    assert request.settings.kerf_table is None
    assert request.placement.span == 400.0
    assert request.stem == "dryrun-root-dryrun-tip"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"span": ""}, "Span (root to tip): required"),
        ({"stock_height": "tall"}, "Stock height: not a number"),
        ({"root": "missing.dxf"}, "Root DXF: file not found"),
        ({"tip": ""}, "Tip DXF: required"),
    ],
)
def test_form_errors_name_the_field(changes: dict[str, str], message: str) -> None:
    with pytest.raises(FormError, match=message.replace("(", r"\(").replace(")", r"\)")):
        request_from_form(form(**changes))


def test_preview_geometry_matches_the_program() -> None:
    plan = make_job(request_from_form(form()))
    result = run_cam(plan)
    g = preview_geometry(plan, result)
    feeds = [m for m in result.moves if m.kind != "rapid"]
    assert len(g.root_cut) == len(g.tip_cut) == len(g.tower_12) == len(feeds) + 1  # + entry point
    x0, z0, x1, z1 = g.travel
    for x, z in g.tower_12 + g.tower_34:
        assert x0 - 1e-6 <= x <= x1 + 1e-6 and z0 - 1e-6 <= z <= z1 + 1e-6
    # the compensated cut surrounds the drawn outline at each face: it starts and ends at the entry point
    assert g.root_cut[0] == pytest.approx(g.root_cut[-1])
    sx0, sz0, sx1, sz1 = g.stock
    assert sx1 - sx0 == 220.0 and sz1 - sz0 == 40.0


@pytest.fixture
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(gui, "SETTINGS_FILE", tmp_path / "settings.json")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tkinter")
    root.withdraw()
    win = SlicerWindow(root)
    yield win
    root.destroy()


def test_window_previews_and_generates(window: SlicerWindow, tmp_path: Path) -> None:
    for key, value in form(out=str(tmp_path / "out")).items():
        window.vars[key].set(value)
    window.root.update()
    assert window.kerf_box.cget("values")  # kerf tables loaded from the profile
    assert window.preview()
    assert len(window.canvas.find_all()) > 10
    window.generate()
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == [f"dryrun-root-dryrun-tip{s}" for s in (".nc", ".svg", ".txt", ".yaml")]
    assert (tmp_path / "settings.json").is_file()


def test_window_shows_errors(window: SlicerWindow) -> None:
    for key, value in form(stock_length="100").items():
        window.vars[key].set(value)
    assert not window.preview()
    assert window.status.get().startswith("Error:")
    assert "the stock is" in window.report.get("1.0", tk.END)


def test_projection_is_right_handed_and_z_is_up() -> None:
    cam = Camera(yaw=90.0, pitch=0.0)
    centre = (0.0, 0.0, 0.0)
    px = cam.project((1.0, 0.0, 0.0), centre, 100.0, 200, 200)
    py = cam.project((0.0, 1.0, 0.0), centre, 100.0, 200, 200)
    pz = cam.project((0.0, 0.0, 1.0), centre, 100.0, 200, 200)
    assert px == pytest.approx((100.0, 100.0))  # +x goes straight into the screen
    assert py[0] < 100.0  # so +y must point left (x = y cross z, right-handed)
    assert pz[1] < 100.0  # +z is up on screen (canvas y grows downwards)
    tilted = Camera(yaw=90.0, pitch=30.0).project((1.0, 0.0, 0.0), centre, 100.0, 200, 200)
    assert tilted[1] < 100.0  # seen from above, points further away appear higher


def test_3d_tab_draws_and_plays(window: SlicerWindow, tmp_path: Path) -> None:
    for key, value in form(out=str(tmp_path / "out")).items():
        window.vars[key].set(value)
    assert window.preview()
    assert len(window.canvas3d.find_all()) > 50
    last = len(window.geometry.tower_12) - 1  # type: ignore[union-attr]
    window._scrub(str(last))
    assert window.view3d.wire_index == last
    assert window.wire_label.get().startswith(f"move {last} / {last}")
    window.wire_pos.set(0)
    window.toggle_play()
    window._step()
    assert window.view3d.wire_index > 0
    window.toggle_play()
    assert not window.playing
    window.view3d.camera.yaw += 40
    window.view3d.draw()
    window.view3d.reset()
    assert window.view3d.camera.yaw == Camera().yaw


def test_alignment_edges_put_the_wire_on_the_stock_edges() -> None:
    plan = make_job(request_from_form(form()))
    g = preview_geometry(plan, run_cam(plan))
    edges = alignment_edges(g)
    x0, z0, x1, z1 = g.stock
    assert [(e.x, e.z) for e in edges] == [(x0, z1), (x1, z1), (x0, z0), (x1, z0)]
    l1, l2, l3, l4 = g.letters
    safe = g.safe_position
    for e in edges:
        lift, across, down = e.gcode
        # lift: only the vertical axes, to the safe height; across: only horizontals; down: only verticals
        assert lift == f"G90 G0 {l2}{safe[1]:.3f} {l4}{safe[3]:.3f}"
        assert across == f"G0 {l1}{e.x:.3f} {l3}{e.x:.3f}"
        assert down == f"G0 {l2}{e.z:.3f} {l4}{e.z:.3f}"
    assert safe[1] > z1 and safe[3] > z1  # crossing happens above the stock


def test_align_tab_copies_and_shows_an_edge(window: SlicerWindow, tmp_path: Path) -> None:
    for key, value in form(out=str(tmp_path / "out")).items():
        window.vars[key].set(value)
    assert window.preview()
    buttons = [w for w in window.align_frame.winfo_children() if isinstance(w, ttk.Button)]
    assert [b.cget("text") for b in buttons] == ["Copy G-code", "Show in 3D"] * 4
    buttons[0].invoke()
    g = window.geometry
    assert g is not None
    edge = alignment_edges(g)[0]
    assert window.root.clipboard_get() == "\n".join(edge.gcode)
    assert edge.gcode[0] in window.align_text.get("1.0", tk.END)
    buttons[1].invoke()
    assert window.view3d.highlight == (edge.x, edge.z, edge.name)
    window.preview()  # a new preview clears the highlight
    assert window.view3d.highlight is None


def test_stock_margins_follow_the_absolute_values(window: SlicerWindow) -> None:
    window.load_values(form(stock_length="220", stock_height="40", stock_mode="absolute"))
    assert window.outlines is not None
    length, height = window.outlines
    assert float(window.vars["margin_length"].get()) == pytest.approx((220 - length) / 2, abs=0.01)
    assert float(window.vars["margin_height"].get()) == pytest.approx((40 - height) / 2, abs=0.01)

    window.vars["margin_length"].set("5")  # typing a margin sets the stock
    assert float(window.vars["stock_length"].get()) == pytest.approx(length + 10, abs=0.01)
    assert window.vars["stock_mode"].get() == "margin"
    window.vars["stock_height"].set("50")  # typing the stock sets the margin
    assert float(window.vars["margin_height"].get()) == pytest.approx((50 - height) / 2, abs=0.01)
    assert window.vars["stock_mode"].get() == "absolute"

    # new outlines keep whatever was typed last: here the margins
    window.vars["margin_height"].set("3")
    window.vars["root"].set(str(TIP_DXF))  # tip twice: a smaller box than root + tip
    window._update_size()
    assert window.outlines is not None and window.outlines != (length, height)
    new_length, new_height = window.outlines
    assert float(window.vars["stock_length"].get()) == pytest.approx(new_length + 10, abs=0.01)
    assert float(window.vars["stock_height"].get()) == pytest.approx(new_height + 6, abs=0.01)
    assert window.preview()
