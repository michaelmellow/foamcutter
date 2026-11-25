"""Placing the stock from above by one of nine points (topview.py), and Root face y in the job."""

import tkinter as tk
from pathlib import Path

import pytest

pytest.importorskip("ezdxf")

from foamdxf import gui
from foamdxf.pipeline import PreviewGeometry
from foamdxf.topview import POINTS, distances, placement, point_position

REPO = Path(__file__).resolve().parents[2]


def geometry(root_y: float = 145.0, tip_y: float = 545.0) -> PreviewGeometry:
    return PreviewGeometry(
        stock=(30.0, 0.0, 280.0, 50.0), travel=(0.0, 0.0, 600.0, 300.0), root_target=[], tip_target=[],
        root_cut=[], tip_cut=[], tower_12=[], tower_34=[], wire_span=690.0, root_y=root_y, tip_y=tip_y,
        times=[], letters=("Z", "A", "X", "Y"), phases=[], safe_position=(0.0, 0.0, 0.0, 0.0))


def test_nine_points_on_the_stock() -> None:
    g = geometry()
    assert len(POINTS) == 9
    assert point_position(g, "0-0") == (30.0, 545.0)  # operator side, face nearest the X/Y tower
    assert point_position(g, "0.5-0.5") == (155.0, 345.0)  # centre
    assert point_position(g, "1-1") == (280.0, 145.0)
    # the reference corner: x 0 (operator end of the travel), y = wire_span (axes 3-4 tower)
    assert distances(g, "0-0") == (30.0, 145.0)
    assert distances(g, "1-1") == (280.0, 545.0)


@pytest.mark.parametrize("key", POINTS)
def test_placing_a_point_puts_it_there(key: str) -> None:
    g = geometry()
    block_x, root_y = placement(g, key, 12.5, 40.0)
    moved = geometry(root_y, root_y + 400.0)
    moved = PreviewGeometry(**{**moved.__dict__, "stock": (block_x, 0.0, block_x + 250.0, 50.0)})
    assert distances(moved, key) == pytest.approx((12.5, 40.0))


def test_job_takes_the_root_face_y(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gui, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("foamdxf.machine.MACHINE_FILE", tmp_path / "machine.json")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tkinter")
    root.withdraw()
    try:
        win = gui.SlicerWindow(root)
        values = dict(gui.DEFAULTS) | {
            "root": str(REPO / "examples" / "dxf" / "dryrun-root.dxf"),
            "tip": str(REPO / "examples" / "dxf" / "dryrun-tip.dxf"),
            "profile": str(REPO / "examples" / "profiles" / "real-machine.yaml"),
            "span": "400", "stock_length": "220", "stock_height": "40", "speed": "300",
            "kerf_table": "sample-not-measured"}
        for key, value in values.items():
            win.vars[key].set(value)
        assert win.preview()
        g = win.geometry
        assert g is not None and g.root_y == pytest.approx((g.wire_span - 400) / 2)  # centred
        view = win.top_view
        view.select("0-0")
        assert float(view.dy.get()) == pytest.approx(g.wire_span - g.tip_y, abs=0.05)
        view.dx.set("40")
        view.dy.set("30")
        view.place_stock()
        g = win.geometry
        assert g is not None and win.plan is not None
        assert g.tip_y == pytest.approx(g.wire_span - 30) and g.root_y == pytest.approx(g.tip_y - 400)
        assert win.plan.job.block.x == pytest.approx(40)
        assert win.vars["block_y"].get() != ""
        assert "Section planes y = 260.0 and y = 660.0." in win.plan.generated.text
        win._centre_stock()
        g = win.geometry
        assert g is not None and g.root_y == pytest.approx((g.wire_span - 400) / 2)
        assert win.vars["block_x"].get() == win.vars["block_y"].get() == ""
    finally:
        root.destroy()
