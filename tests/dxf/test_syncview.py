"""Tests for the Sync check tab (syncview.py)."""

import tkinter as tk
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("ezdxf")

from foamdxf import gui
from foamdxf.gui import request_from_form
from foamdxf.pipeline import PreviewGeometry, make_job, preview_geometry, run_cam
from foamdxf.syncview import PALETTE, SyncView, cut_moves, sections, turn

REPO = Path(__file__).resolve().parents[2]


def form() -> dict[str, str]:
    return dict(gui.DEFAULTS) | {
        "root": str(REPO / "examples" / "dxf" / "dryrun-root.dxf"),
        "tip": str(REPO / "examples" / "dxf" / "dryrun-tip.dxf"),
        "profile": str(REPO / "examples" / "profiles" / "real-machine.yaml"),
        "span": "400", "stock_length": "220", "stock_height": "40"}


def square_geometry(tip_shift: int = 0) -> PreviewGeometry:
    """A 4 x 4 square cut in 1 mm moves at both faces, after a lead-in. With `tip_shift`
    the tip path starts that many points later round the square: out of sync."""
    ring = ([(float(i), 0.0) for i in range(4)] + [(4.0, float(i)) for i in range(4)]
            + [(4.0 - i, 4.0) for i in range(4)] + [(0.0, 4.0 - i) for i in range(4)])
    root = [(-5.0, 0.0)] + ring + ring[:1]
    tip_ring = ring[tip_shift:] + ring[:tip_shift]
    tip = [(-5.0, 0.0)] + tip_ring + tip_ring[:1]
    phases = ["entry", "lead_in"] + ["cut"] * 16
    return PreviewGeometry(
        stock=(0, 0, 4, 4), travel=(0, 0, 10, 10), root_target=ring, tip_target=ring,
        root_cut=root, tip_cut=tip, tower_12=root, tower_34=tip, wire_span=100, root_y=10, tip_y=90,
        times=[0.0] * len(root), letters=("X", "Y", "Z", "A"), phases=phases,
        safe_position=(0, 0, 0, 0))


def test_turn_measures_the_corner_angle() -> None:
    assert turn((0, 0), (1, 0), (2, 0)) == pytest.approx(0)
    assert turn((0, 0), (1, 0), (1, 1)) == pytest.approx(90)
    assert turn((0, 0), (1, 0), (1, -1)) == pytest.approx(90)
    assert turn((0, 0), (0, 0), (1, 1)) == 0  # a move without length has no direction


def test_a_synced_square_has_one_section_per_side() -> None:
    g = square_geometry()
    found = sections(g)
    assert [len(s) for s in found] == [4, 4, 4, 4]
    assert [m for s in found for m in s] == cut_moves(g)  # every cutting move, once, in order


def test_out_of_sync_corners_split_the_sections_further() -> None:
    # the tip turns its corners 2 moves after the root: each side splits in two
    assert [len(s) for s in sections(square_geometry(tip_shift=2))] == [2, 2, 2, 2, 2, 2, 2, 2]


def test_every_cutting_move_of_a_real_cut_is_in_one_section() -> None:
    plan = make_job(request_from_form(form()))
    g = preview_geometry(plan, run_cam(plan))
    found = sections(g)
    assert [m for s in found for m in s] == cut_moves(g)
    assert all(s == list(range(s[0], s[-1] + 1)) for s in found)  # runs of consecutive moves
    assert 2 <= len(found) < len(cut_moves(g)) / 5  # corners only, not every move


@pytest.fixture
def view() -> Iterator[SyncView]:
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tkinter")
    root.geometry("600x400")
    v = SyncView(root)
    v.pack(fill=tk.BOTH, expand=True)
    root.update()
    yield v
    root.destroy()


def test_root_and_tip_of_a_section_share_a_colour(view: SyncView) -> None:
    view.show(square_geometry())
    assert len(view.sections) == 4
    for n, moves in enumerate(view.sections):
        assert {view.colour(k) for k in moves} == {PALETTE[n % len(PALETTE)]}
    assert view.colour(1) not in PALETTE  # the lead-in is not part of the outline


def test_clicking_a_section_selects_it_and_the_arrows_step(view: SyncView) -> None:
    g = square_geometry()
    view.show(g)
    k = view.sections[2][1]  # a move in the middle of the third side
    (x0, y0), (x1, y1) = view._to_canvas([g.root_cut[k - 1], g.root_cut[k]])
    view._click(type("E", (), {"x": (x0 + x1) / 2, "y": (y0 + y1) / 2})())
    assert view.selected == 2
    assert view.note.get().startswith("section 3 of 4")
    view.step(1)
    assert view.selected == 3
    view.step(1)
    assert view.selected == 0  # round the outline
    view.select(None)
    tip = view.tip(g.tip_cut)  # the tip half, drawn below the root, selects the same section
    (x0, y0), (x1, y1) = view._to_canvas([tip[k - 1], tip[k]])
    view._click(type("E", (), {"x": (x0 + x1) / 2, "y": (y0 + y1) / 2})())
    assert view.selected == 2
    view.show(None)
    assert view.selected is None and not view.sections


def test_tip_is_drawn_below_the_root_unless_overlaid(view: SyncView) -> None:
    g = square_geometry()
    view.show(g)
    drop = view.tip_drop()
    assert drop > 4  # the 4 mm square plus a gap: the two do not touch
    assert max(z for _, z in view.tip(g.tip_cut)) < min(z for _, z in g.root_cut)
    view.overlay.set(True)
    assert view.tip_drop() == 0
    assert view.tip(g.tip_cut) == g.tip_cut


def test_window_fills_the_sync_tab_on_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gui, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("foamdxf.machine.MACHINE_FILE", tmp_path / "machine.json")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tkinter")
    root.withdraw()
    try:
        win = gui.SlicerWindow(root)
        assert "Sync check" in [win.tabs.tab(t, "text") for t in win.tabs.tabs()]  # type: ignore[no-untyped-call]
        for key, value in form().items():
            win.vars[key].set(value)
        assert win.preview()
        assert win.sync_view.geometry is win.geometry
        assert win.sync_view.sections
    finally:
        root.destroy()
