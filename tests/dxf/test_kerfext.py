"""Kerf beyond the measured range for one job (kerfext.py, job-file.md §3 kerf_extension)."""

import tkinter as tk
from pathlib import Path

import pytest

pytest.importorskip("ezdxf")

from foamcam import CamError, generate, load_job, load_profile
from foamcam.errors import FoamcamError
from foamdxf import gui
from foamdxf.kerfext import (
    ExtensionDialog,
    OutOfRange,
    extension_from_text,
    extension_to_text,
    extrapolate,
    merged,
    out_of_range,
    suggested,
)

REPO = Path(__file__).resolve().parents[2]
TABLE = [(167.0, 2.0), (230.0, 1.6), (600.0, 1.4)]
MESSAGE = (
    "the job cannot be cut:\n"
    '  - kerf table "measured": speed 146.9 mm/min is outside the table range 167 to 600 mm/min\n'
    "  - why: the root face moves at only 147 mm/min there"
)


def test_out_of_range_reads_the_cam_message() -> None:
    need = out_of_range(MESSAGE)
    assert need == OutOfRange("measured", 146.9, 167.0, 600.0, "the root face moves at only 147 mm/min there")
    assert need.below
    assert out_of_range("axis 3 (X) goes to -3.6, beyond its min") is None


def test_suggested_point_is_beyond_the_need_on_the_extrapolated_line() -> None:
    low = suggested(TABLE, OutOfRange("t", 146.9, 167, 600))
    assert low[0] == 130.0  # 10 % below 146.9, rounded down to 5 mm/min
    assert low[1] == pytest.approx(2.0 + (1.6 - 2.0) * (130 - 167) / (230 - 167), abs=0.001)
    high = suggested(TABLE, OutOfRange("t", 700.0, 167, 600))
    assert high[0] == 770.0
    assert high[1] == pytest.approx(extrapolate(TABLE, 770.0), abs=0.001)
    # a steep end is never extrapolated below a small positive kerf
    assert suggested([(100.0, 1.0), (110.0, 0.2)], OutOfRange("t", 300.0, 100, 110))[1] > 0


def test_extension_text_belongs_to_its_table() -> None:
    text = extension_to_text("measured", ((130.0, 2.2),))
    assert extension_from_text(text, "measured") == ((130.0, 2.2),)
    assert extension_from_text(text, "other") == ()  # picking another table drops it
    assert extension_from_text("not json", "measured") == ()
    assert extension_to_text("measured", ()) == ""


def test_a_new_point_replaces_the_old_one_on_its_side() -> None:
    assert merged(((150.0, 2.1), (700.0, 1.3)), (120.0, 2.4), 167.0) == ((120.0, 2.4), (700.0, 1.3))


def test_dialog_refuses_points_that_do_not_reach_the_need_or_lie_inside() -> None:
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tkinter")
    root.withdraw()
    try:
        d = ExtensionDialog(root, TABLE, (), OutOfRange("measured", 146.9, 167, 600))
        point = d.chosen()
        assert not isinstance(point, str)
        assert point[0] == 130.0 and point[1] == pytest.approx(2.235, abs=0.001)
        d.speed.set("150")  # beyond the table but not as far as the cut needs
        assert "must reach 147" in str(d.chosen())
        d.speed.set("200")
        assert "outside the measured range" in str(d.chosen())
        d.speed.set("120")
        d.kerf.set("0")
        assert "more than 0" in str(d.chosen())
        d.kerf.set("2.5")
        d.accept()
        assert d.result == (120.0, 2.5)
        d2 = ExtensionDialog(root, TABLE, (), OutOfRange("measured", 146.9, 167, 600))
        d2._dragging = True  # a drag sets the fields from the graph
        x, y = d2._xy(140.0, 2.3)
        d2._drag(type("E", (), {"x": x, "y": y})())
        assert float(d2.speed.get()) == pytest.approx(140, abs=5)
        assert float(d2.kerf.get()) == pytest.approx(2.3, abs=0.05)
        d2.top.destroy()
    finally:
        root.destroy()


def test_cam_uses_the_extension_and_reports_it(tmp_path: Path) -> None:
    """The committed tapered-spar example with its table cut short: refused, then cut with
    a kerf extension, which the report lists as not measured."""
    profile = load_profile(REPO / "examples" / "profiles" / "sample-machine.yaml")
    job_path = REPO / "examples" / "jobs" / "tapered-spar.yaml"
    job = load_job(job_path)
    table = profile.kerf_tables[job.kerf_table]
    assert len(table.points) >= 2
    # a job whose cut speed lies above the table: refused without an extension ...
    fast = job_path.read_text(encoding="utf-8").replace(f"cut_speed: {job.cut_speed:g}",
                                                       f"cut_speed: {table.points[-1][0] * 1.5:g}")
    assert fast != job_path.read_text(encoding="utf-8")
    jobs = _copy_airfoils(job_path, tmp_path)
    fast_job = jobs / "fast.yaml"
    fast_job.write_text(fast, encoding="utf-8")
    with pytest.raises(CamError) as refused:
        generate(load_job(fast_job), profile)
    need = out_of_range(str(refused.value))
    assert need is not None and not need.below
    # ... and cut with one point beyond the table's top
    speed, kerf = suggested(list(table.points), need)
    extended = fast.replace(f'kerf_table: "{job.kerf_table}"',
                            f'kerf_table: "{job.kerf_table}"\nkerf_extension:\n  - {{ speed: {speed:g}, kerf: {kerf:g} }}')
    (jobs / "extended.yaml").write_text(extended, encoding="utf-8")
    result = generate(load_job(jobs / "extended.yaml"), profile)
    assert f"this job only, not measured: {speed:g} mm/min: {kerf:.3f} mm" in result.report


def test_cam_refuses_an_extension_inside_the_table(tmp_path: Path) -> None:
    job_path = REPO / "examples" / "jobs" / "tapered-spar.yaml"
    job = load_job(job_path)
    profile = load_profile(REPO / "examples" / "profiles" / "sample-machine.yaml")
    low, high = profile.kerf_tables[job.kerf_table].points[0][0], profile.kerf_tables[job.kerf_table].points[-1][0]
    text = job_path.read_text(encoding="utf-8").replace(
        f'kerf_table: "{job.kerf_table}"',
        f'kerf_table: "{job.kerf_table}"\nkerf_extension:\n  - {{ speed: {(low + high) / 2:g}, kerf: 1.0 }}')
    jobs = _copy_airfoils(job_path, tmp_path)
    (jobs / "inside.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(FoamcamError, match=r"kerf_extension\[0\]: speed .* is inside the range"):
        generate(load_job(jobs / "inside.yaml"), profile)


def _copy_airfoils(job_path: Path, tmp_path: Path) -> Path:
    """The example's .dat paths are relative to the job ("../airfoils/..."): copy the
    airfoils next to a jobs folder in tmp_path, and return that folder for the job copies."""
    airfoils = tmp_path / "airfoils"
    airfoils.mkdir()
    for dat in (job_path.parent.parent / "airfoils").glob("*.dat"):
        (airfoils / dat.name).write_text(dat.read_text(encoding="utf-8"), encoding="utf-8")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    return jobs


def test_preview_asks_for_a_kerf_beyond_the_table_and_cuts_with_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            "span": "400", "stock_length": "220", "stock_height": "40", "speed": "700"}
        for key, value in values.items():
            win.vars[key].set(value)
        win.vars["kerf_table"].set("sample-not-measured")  # measured up to 480 mm/min
        asked: list[OutOfRange] = []

        def answer(table, existing, need):  # type: ignore[no-untyped-def]
            asked.append(need)
            return None if len(asked) == 1 else suggested(table, need)

        win.ask_extension = answer  # type: ignore[method-assign]
        assert not win.preview()  # cancelled: no point, nothing kept
        assert len(asked) == 1 and not asked[0].below
        assert win.vars["kerf_extension"].get() == ""
        assert win.preview()  # accepted: the cut goes through with the point
        assert "this job only, not measured" in win.result.report  # type: ignore[union-attr]
        assert extension_from_text(win.vars["kerf_extension"].get(), win.vars["kerf_table"].get())
        assert win.extension_row.winfo_manager() == "grid"
        win.vars["kerf_extension"].set("")  # the Clear button
        assert win.extension_row.winfo_manager() == ""
    finally:
        root.destroy()
