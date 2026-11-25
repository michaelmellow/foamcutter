"""A cut past the axis travel is previewed with the out-of-travel parts drawn pink, and the
Machine tab has nothing to Start until the stock is moved and Preview pressed again."""

import tkinter as tk
from pathlib import Path

import pytest

pytest.importorskip("ezdxf")

from foamdxf import gui
from foamdxf.pipeline import beyond_runs

REPO = Path(__file__).resolve().parents[2]


def test_beyond_runs_join_flagged_segments() -> None:
    points = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0), (5.0, 0.0)]
    flags = (False, True, False, False, False, True)
    assert beyond_runs(points, flags) == [[(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)], [(4.0, 0.0), (5.0, 0.0)]]
    assert beyond_runs(points, (False,) * 6) == []


def test_preview_past_the_travel_draws_it_and_blocks_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert win.plan is not None
        x = win.plan.job.block.x  # where the form placed the stock (Block X left empty)
        win.vars["block_x"].set(f"{x - 60:g}")  # the stock 60 mm toward x 0: past the carriages' min
        assert win.preview()  # made, not refused
        assert win.result is not None and win.result.out_of_travel
        assert win.result.gcode.startswith("(OUT OF TRAVEL")
        assert win.status.get().startswith("OUT OF TRAVEL")
        assert "away from the operator" in win.status.get()
        g = win.geometry
        assert g is not None and (any(g.beyond_12) or any(g.beyond_34))
        pink = [i for i in win.canvas.find_all() if win.canvas.itemcget(i, "fill") == gui.COLOURS["beyond"]]  # type: ignore[no-untyped-call]
        assert pink  # drawn over the carriage paths, and in the legend
        with pytest.raises(ValueError, match="past the axis travel"):
            win._current_program()
        assert "past the axis travel" in win.machine.program_note.get()
        win.vars["block_x"].set(f"{x:g}")  # moved back
        assert win.preview()
        assert win.result is not None and not win.result.out_of_travel
        assert win._current_program().gcode == win.result.gcode
    finally:
        root.destroy()
