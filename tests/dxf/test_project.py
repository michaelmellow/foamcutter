"""Tests for slicer projects (src/foamdxf/project.py) and their use in the window."""

import json
import shutil
import tkinter as tk
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("ezdxf")

from foamdxf import gui
from foamdxf.errors import DxfError
from foamdxf.gui import SlicerWindow
from foamdxf.project import open_project, save_project

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, str]:
    """A working set of form values with the input files copied into tmp_path/in."""
    src = tmp_path / "in"
    src.mkdir()
    for name in ("dxf/dryrun-root.dxf", "dxf/dryrun-tip.dxf", "profiles/real-machine.yaml"):
        shutil.copy(EXAMPLES / name, src)
    values = dict(gui.DEFAULTS)
    values |= {
        "root": str(src / "dryrun-root.dxf"), "tip": str(src / "dryrun-tip.dxf"),
        "profile": str(src / "real-machine.yaml"), "span": "400", "stock_length": "220",
        "stock_height": "40", "speed": "140", "name": "my wing", "block_x": "",
        "kerf_table": "sample-not-measured",
    }
    return values


def test_round_trip_keeps_every_value(tmp_path: Path, inputs: dict[str, str]) -> None:
    project = tmp_path / "wing.foamproj"
    save_project(project, inputs)
    opened = open_project(project)
    assert opened.values == inputs
    assert opened.notes == ()
    assert not (tmp_path / "wing_files").exists()  # originals were still there


def test_missing_files_are_restored_from_the_project(tmp_path: Path, inputs: dict[str, str]) -> None:
    project = tmp_path / "proj" / "wing.foamproj"
    save_project(project, inputs)
    original_root = Path(inputs["root"]).read_bytes()
    shutil.rmtree(tmp_path / "in")
    opened = open_project(project)
    restored = Path(opened.values["root"])
    assert restored.parent == tmp_path / "proj" / "wing_files"
    assert restored.read_bytes() == original_root
    assert len(opened.notes) == 3 and all("is missing" in n for n in opened.notes)
    # opening again reuses the restored copies
    assert open_project(project).values["root"] == str(restored)


def test_changed_file_uses_the_saved_copy(tmp_path: Path, inputs: dict[str, str]) -> None:
    project = tmp_path / "wing.foamproj"
    save_project(project, inputs)
    saved = Path(inputs["tip"]).read_bytes()
    Path(inputs["tip"]).write_bytes(saved + b"\n999\nEOF\n")
    opened = open_project(project)
    assert opened.values["root"] == inputs["root"]  # unchanged file: used where it is
    assert Path(opened.values["tip"]).read_bytes() == saved
    assert any("has changed" in n for n in opened.notes)


def test_damaged_or_foreign_files_are_rejected(tmp_path: Path, inputs: dict[str, str]) -> None:
    project = tmp_path / "wing.foamproj"
    save_project(project, inputs)
    doc = json.loads(project.read_text(encoding="utf-8"))
    doc["files"]["root"]["content"] = "AAAA"
    project.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(DxfError, match="damaged"):
        open_project(project)
    other = tmp_path / "other.foamproj"
    other.write_text('{"format": "something-else"}', encoding="utf-8")
    with pytest.raises(DxfError, match="not a slicer project"):
        open_project(other)


def test_saving_needs_readable_files(tmp_path: Path, inputs: dict[str, str]) -> None:
    inputs["root"] = str(tmp_path / "nope.dxf")
    with pytest.raises(DxfError, match="cannot save the project"):
        save_project(tmp_path / "wing.foamproj", inputs)


# --- in the window -----------------------------------------------------------------------------


@pytest.fixture
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SlicerWindow]:
    monkeypatch.setattr(gui, "SETTINGS_FILE", tmp_path / "settings.json")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tkinter")
    root.withdraw()
    win = SlicerWindow(root)
    yield win
    root.destroy()


def test_window_saves_and_reopens_a_project(window: SlicerWindow, tmp_path: Path, inputs: dict[str, str]) -> None:
    window.load_values(inputs)
    loaded = window.values()  # inputs plus the margins measured from the outlines
    assert loaded == inputs | {"margin_length": "20", "margin_height": "9.05"}
    window.project_path = tmp_path / "wing.foamproj"
    assert window.save_project()
    assert not window.modified() and window.root.title().startswith("wing.foamproj -")
    window.vars["span"].set("350")
    assert window.modified() and window.root.title().startswith("wing.foamproj * -")

    window.saved_values = window.values()  # discard the edit without a dialog
    window.new_project()
    assert window.vars["root"].get() == ""
    assert window.open_project(tmp_path / "wing.foamproj")
    assert window.values() == loaded
    assert window.geometry is not None  # opening runs the preview


def test_readout_follows_the_wire(window: SlicerWindow, inputs: dict[str, str]) -> None:
    window.load_values(inputs | {"kerf_table": ""})
    assert window.vars["kerf_table"].get() == "sample-not-measured"  # filled from the profile
    assert window.preview()
    g = window.geometry
    assert g is not None
    letters = [letter.get() for letter, _ in window.dro_axes]
    assert letters == list(g.letters)
    i = len(g.tower_12) // 2
    window._scrub(str(i))
    values = [float(value.get()) for _, value in window.dro_axes]
    assert values == pytest.approx([*g.tower_12[i], *g.tower_34[i]], abs=1e-3)
    detail = window.dro_detail.get()
    assert "root face" in detail and "cutting" in detail and "wire speed" in detail
    window._scrub("0")
    assert window.dro_detail.get().splitlines()[1].startswith("at entry point")
