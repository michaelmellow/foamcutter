"""Desktop window for the DXF front end: python -m foamdxf --gui [PROJECT.foamproj]

Pick the root and tip DXFs and the machine profile, enter the span and stock
size (or pick a STEP model and its root and tip faces: the span is then
measured from the model), press Preview to see the cut, then Generate to write the job, G-code,
report and SVG. File > Save project keeps every value and a copy of each input
file in one .foamproj file (see project.py). Uses Tkinter only (part of Python), so it needs no extra
packages. The last values are remembered in ~/.foamdxf-gui.json.
"""

import functools
import json
import math
import os
import re
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from foamcam.cam import CamResult
from foamcam.errors import FoamcamError
from foamcam.profile import load_profile

from .errors import DxfError
from .holes import CHANNELS
from .jobgen import CutSettings, Placement
from .machine import (
    MOUSE_FEED_FALLBACK_MAX,
    MOUSE_FEED_MIN,
    MachinePanel,
    Program,
    on_wire,
    tower_positions,
    tower_targets,
)
from .outline import Point
from .pipeline import (
    AlignEdge,
    Plan,
    PreviewGeometry,
    Request,
    alignment_edges,
    make_job,
    outline_size,
    preview_geometry,
    read_outlines,
    run_cam,
    write_files,
)
from .project import SUFFIX
from .project import open_project as read_project
from .project import save_project as write_project
from .sender import Status
from .step import StepModel, read_step, sections
from .view3d import View3D

REPO = Path(__file__).resolve().parents[2]
SETTINGS_FILE = Path.home() / ".foamdxf-gui.json"

# (key, label, default, unit); a blank default means "optional"
FILE_FIELDS = [
    ("root", "Root DXF", "", "dxf"),
    ("tip", "Tip DXF", "", "dxf"),
    ("profile", "Machine profile", str(REPO / "examples" / "profiles" / "real-machine.yaml"), "yaml"),
]
PART_FIELDS = [
    ("span", "Span (root to tip)", "400", "mm"),
    ("stock_length", "Stock length (chord)", "220", "mm"),
    ("margin_length", "   or margin each side", "", "mm past the outlines"),
    ("stock_height", "Stock height", "40", "mm"),
    ("margin_height", "   or margin each side", "", "mm past the outlines"),
]
MARGINS = {"stock_length": "margin_length", "stock_height": "margin_height"}  # stock = outlines + 2 * margin
CUT_FIELDS = [
    ("speed", "Cut speed", "150", "mm/min"),
    ("lead_in", "Lead-in", "20", "mm"),
    ("block_x", "Stock back face x", "", "mm, blank = centred"),
    ("safe_clearance", "Safe height above stock", "20", "mm"),
    ("min_te", "Min trailing edge", "0.6", "mm, 0 = as drawn"),
    ("segment", "Max segment length", "1", "mm"),
    ("simplify", "Simplify tolerance", "0.01", "mm, 0 = keep all"),
]
DEFAULTS = {key: default for key, _, default, _ in FILE_FIELDS + PART_FIELDS + CUT_FIELDS}
DEFAULTS |= {"kerf_table": "", "direction": "upper_first", "name": "", "out": str(REPO / "output")}
DEFAULTS |= {"channel": "shortest"}  # side the wire enters inner contours (holes) from
# STEP workflow: source "step" uses the model and two of its flat faces (numbers) instead of the DXFs
DEFAULTS |= {"source": "dxf", "step": "", "root_face": "", "tip_face": ""}
# which stock values were typed last ("absolute" or "margin"): kept when the outlines change
DEFAULTS |= {"stock_mode": "absolute"}
STEP_LABELS = {"step": "STEP model", "root_face": "Root face", "tip_face": "Tip face"}

COLOURS = {
    "travel": "#9aa0a6",
    "stock": "#fff4c2",
    "stock_edge": "#c9a227",
    "root": "#1f6fb4",
    "tip": "#e8710a",
    "tower_12": "#1f6fb4",
    "tower_34": "#e8710a",
    "target": "#202124",
}


class FormError(ValueError):
    pass


def request_from_form(values: dict[str, str]) -> Request:
    """Turn the form's text values into a Request. Raises FormError naming the field."""

    def text(key: str, label: str) -> str:
        v = values.get(key, "").strip()
        if not v:
            raise FormError(f"{label}: required")
        return v

    def number(key: str, label: str, optional: bool = False) -> float | None:
        v = values.get(key, "").strip()
        if not v:
            if optional:
                return None
            raise FormError(f"{label}: required")
        try:
            return float(v.replace(",", "."))
        except ValueError:
            raise FormError(f"{label}: not a number ({v})") from None

    def req(key: str, label: str) -> float:
        n = number(key, label)
        assert n is not None
        return n

    labels = {key: label for key, label, _, _ in FILE_FIELDS + PART_FIELDS + CUT_FIELDS} | STEP_LABELS
    step = values.get("source", "dxf") == "step"
    keys = ("step", "profile") if step else ("root", "tip", "profile")
    paths = {key: Path(text(key, labels[key])) for key in keys}
    for key, path in paths.items():
        if not path.is_file():
            raise FormError(f"{labels[key]}: file not found ({path})")
    faces: tuple[int, int] | None = None
    if step:
        tags = []
        for key in ("root_face", "tip_face"):
            try:
                tags.append(int(text(key, labels[key])))
            except ValueError:
                raise FormError(f"{labels[key]}: pick a face of the model") from None
        faces = (tags[0], tags[1])
        paths["root"] = paths["tip"] = paths["step"]
    placement = Placement(
        span=0.0 if step else req("span", labels["span"]),  # STEP: measured between the faces
        stock_length=req("stock_length", labels["stock_length"]),
        stock_height=req("stock_height", labels["stock_height"]),
        block_x=number("block_x", labels["block_x"], optional=True),
        lead_in=req("lead_in", labels["lead_in"]),
        safe_clearance=req("safe_clearance", labels["safe_clearance"]),
    )
    settings = CutSettings(
        cut_speed=req("speed", labels["speed"]),
        kerf_table=values.get("kerf_table", "").strip() or None,
        direction=values.get("direction", "upper_first"),
        max_segment_length=req("segment", labels["segment"]),
        min_te_thickness=req("min_te", labels["min_te"]),
        channel=values.get("channel", "shortest"),
    )
    return Request(
        root=paths["root"],
        tip=paths["tip"],
        profile=paths["profile"],
        placement=placement,
        settings=settings,
        name=values.get("name", "").strip() or None,
        simplify=req("simplify", labels["simplify"]),
        faces=faces,
    )


LEGEND_HEIGHT = 76  # the section view's legend: two columns of four lines
ZOOM_STEP = 1.2  # per wheel notch
ZOOM_RANGE = (0.2, 50.0)
DRAG_PIXELS = 4  # a left press that moves further than this is a drag, not a click


def _legend_slot(i: int) -> tuple[float, float]:
    """Canvas x, y of legend line i (0-7)."""
    return 12 + 290 * (i // 4), 12 + 16 * (i % 4)


def _number(text: str) -> float | None:
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None


def load_settings() -> dict[str, str]:
    values = dict(DEFAULTS)
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        values |= {k: str(v) for k, v in stored.items() if k in values}
    except (OSError, ValueError):
        pass
    return values


def save_settings(values: dict[str, str]) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(values, indent=2), encoding="utf-8")
    except OSError:
        pass


class SlicerWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Foam cutter: DXF slicer")
        root.geometry("1200x760")
        root.minsize(900, 560)
        self.vars: dict[str, tk.StringVar] = {k: tk.StringVar(value=v) for k, v in load_settings().items()}
        self.plan: Plan | None = None
        self.result: CamResult | None = None
        self.geometry: PreviewGeometry | None = None
        self.project_path: Path | None = None
        self.preview_values: dict[str, str] | None = None  # form values of the last good Preview
        self.live: Status | None = None  # last status report from the machine, None if not connected
        self._travel_cache: tuple[tuple[str, float], dict[str, tuple[float, float]]] | None = None
        self.view_zoom = 1.0  # section view: zoom and shift (canvas pixels) on top of the fit
        self.view_shift = (0.0, 0.0)
        self._pan_from = (0, 0)
        self._press_at: tuple[int, int] | None = None
        self._press_towers: tuple[int, ...] = (0, 1)
        self._dragging = False
        self.keys_down: set[str] = set()
        self.move_target: tuple[float, float, tuple[int, ...]] | None = None  # x, z, towers last asked for
        self.saved_values: dict[str, str] = {}
        self.step_model: StepModel | None = None
        self._step_pending = False
        self.outlines: tuple[float, float] | None = None  # length, height of the box around both outlines
        self._size_pending = False
        self._syncing = False

        self.status = tk.StringVar(value="Pick the two DXFs, then press Preview.")
        ttk.Label(root, textvariable=self.status, anchor="w", padding=(8, 3), relief="sunken").pack(
            side=tk.BOTTOM, fill=tk.X)
        paned = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        form = ttk.Frame(paned, padding=8)
        right = ttk.Frame(paned)
        paned.add(form, weight=0)
        paned.add(right, weight=1)

        self._build_menu()
        self._build_form(form)
        self._build_right(right)
        self.vars["profile"].trace_add("write", lambda *_: self._load_kerf_tables())
        self._load_kerf_tables()
        for key in ("source", "step"):
            self.vars[key].trace_add("write", lambda *_: self._queue_step_load())
        self._source_changed()
        self._load_step()
        for stock, margin in MARGINS.items():
            self.vars[stock].trace_add("write", functools.partial(self._stock_edited, stock))
            self.vars[margin].trace_add("write", functools.partial(self._margin_edited, stock))
        for key in ("source", "root", "tip", "step", "root_face", "tip_face", "simplify"):
            self.vars[key].trace_add("write", lambda *_: self._queue_size_update())
        self._update_size()
        self.saved_values = self.values()
        for var in self.vars.values():
            var.trace_add("write", lambda *_: self._update_title())
            var.trace_add("write", lambda *_: self.machine.refresh())
        self._update_title()
        root.protocol("WM_DELETE_WINDOW", self.close)

    # --- projects -------------------------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New project", accelerator="Ctrl+N", command=self.new_project)
        file_menu.add_command(label="Open project…", accelerator="Ctrl+O", command=self.open_project)
        file_menu.add_command(label="Save project", accelerator="Ctrl+S", command=self.save_project)
        file_menu.add_command(label="Save project as…", accelerator="Ctrl+Shift+S", command=self.save_project_as)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)
        menubar.add_cascade(label="File", menu=file_menu)
        self.root.configure(menu=menubar)
        self.root.bind_all("<Control-n>", lambda _e: self.new_project())
        self.root.bind_all("<Control-o>", lambda _e: self.open_project())
        self.root.bind_all("<Control-s>", lambda _e: self.save_project())
        self.root.bind_all("<Control-S>", lambda _e: self.save_project_as())

    def modified(self) -> bool:
        return self.values() != self.saved_values

    def _update_title(self) -> None:
        name = self.project_path.name if self.project_path else "untitled"
        mark = " *" if self.project_path and self.modified() else ""
        self.root.title(f"{name}{mark} - Foam cutter DXF slicer")

    def _keep_changes(self) -> bool:
        """Ask to save an open project with unsaved changes. False = the user cancelled."""
        if self.project_path is None or not self.modified():
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved changes", f"Save the changes to {self.project_path.name}?", parent=self.root)
        if answer is None:
            return False
        return self.save_project() if answer else True

    def load_values(self, values: dict[str, str]) -> None:
        self._syncing = True  # the values belong together; do not sync them one by one
        try:
            for key, var in self.vars.items():
                var.set(values.get(key, DEFAULTS.get(key, "")))
        finally:
            self._syncing = False
        self._load_kerf_tables()  # a blank or unknown kerf table becomes the profile's first
        self._source_changed()
        self._load_step()
        self._update_size()
        self.plan = self.result = self.geometry = None
        self.draw()
        self.view3d.show(None)
        self._update_alignment()
        self._update_readout(0)
        self.wire_label.set("")

    def new_project(self) -> None:
        if not self._keep_changes():
            return
        self.project_path = None
        self.load_values(dict(DEFAULTS))
        self.saved_values = self.values()
        self._update_title()
        self.status.set("New project. Pick the two DXFs, then press Preview.")

    def open_project(self, path: Path | None = None) -> bool:
        if not self._keep_changes():
            return False
        if path is None:
            chosen = filedialog.askopenfilename(
                parent=self.root, filetypes=[("Slicer project", f"*{SUFFIX}"), ("All files", "*.*")])
            if not chosen:
                return False
            path = Path(chosen)
        try:
            opened = read_project(path)
        except DxfError as error:
            messagebox.showerror("Cannot open project", str(error), parent=self.root)
            return False
        self.project_path = path
        self.load_values(opened.values)
        self.saved_values = self.values()
        self._update_title()
        if opened.notes:
            messagebox.showwarning("Project files restored", "\n\n".join(opened.notes), parent=self.root)
        if self.preview():
            self.status.set(f"Opened {path.name}. " + self.status.get())
        return True

    def save_project(self) -> bool:
        if self.project_path is None:
            return self.save_project_as()
        try:
            write_project(self.project_path, self.values())
        except (DxfError, OSError) as error:
            messagebox.showerror("Cannot save project", str(error), parent=self.root)
            return False
        self.saved_values = self.values()
        self._update_title()
        self.status.set(f"Saved {self.project_path} (values and copies of the DXFs and profile).")
        return True

    def save_project_as(self) -> bool:
        source = "step" if self.vars["source"].get() == "step" else "root"
        stem = Path(self.vars[source].get()).stem or "project"
        chosen = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=SUFFIX, initialfile=f"{stem}{SUFFIX}",
            filetypes=[("Slicer project", f"*{SUFFIX}")])
        if not chosen:
            return False
        self.project_path = Path(chosen)
        return self.save_project()

    # --- layout ---------------------------------------------------------------------------

    def _build_form(self, form: ttk.Frame) -> None:
        files = ttk.LabelFrame(form, text="Files", padding=6)
        files.pack(fill=tk.X)
        choice = ttk.Frame(files)
        choice.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ttk.Label(choice, text="Source").pack(side=tk.LEFT, padx=(0, 8))
        for value, text in (("dxf", "Two DXFs"), ("step", "STEP model")):
            ttk.Radiobutton(choice, text=text, value=value, variable=self.vars["source"],
                            command=self._source_changed).pack(side=tk.LEFT, padx=(0, 8))
        self.mode_rows: dict[str, list[tk.Widget]] = {"dxf": [], "step": []}
        rows = [(key, label, kind, "dxf") for key, label, _, kind in FILE_FIELDS[:2]]
        rows += [("step", "STEP model", "step", "step")]
        rows += [(key, label, kind, "all") for key, label, _, kind in FILE_FIELDS[2:]]
        self.face_text: dict[str, tk.StringVar] = {}
        self.face_boxes: dict[str, ttk.Combobox] = {}
        row = 1
        for key, label, kind, mode in rows:
            widgets: list[tk.Widget] = [
                ttk.Label(files, text=label),
                ttk.Entry(files, textvariable=self.vars[key], width=34),
                ttk.Button(files, text="…", width=3, command=self._browse(key, kind)),
            ]
            widgets[0].grid(row=row, column=0, sticky="w", pady=2)
            widgets[1].grid(row=row, column=1, sticky="we", padx=4)
            widgets[2].grid(row=row, column=2)
            row += 1
            if mode != "all":
                self.mode_rows[mode] += widgets
            if key == "step":  # the two face pickers go right under the model
                for face_key in ("root_face", "tip_face"):
                    text_var = tk.StringVar()
                    box = ttk.Combobox(files, textvariable=text_var, state="readonly", width=34)
                    box.bind("<<ComboboxSelected>>", functools.partial(self._face_picked, face_key))
                    name = ttk.Label(files, text=STEP_LABELS[face_key])
                    name.grid(row=row, column=0, sticky="w", pady=2)
                    box.grid(row=row, column=1, columnspan=2, sticky="we", padx=4)
                    row += 1
                    self.face_text[face_key], self.face_boxes[face_key] = text_var, box
                    self.mode_rows["step"] += [name, box]
                self.span_note = ttk.Label(files, text="", foreground="#5f6368", wraplength=330, justify="left")
                self.span_note.grid(row=row, column=0, columnspan=3, sticky="w")
                row += 1
                self.mode_rows["step"].append(self.span_note)
        files.columnconfigure(1, weight=1)

        part = ttk.LabelFrame(form, text="Part and stock", padding=6)
        part.pack(fill=tk.X, pady=(8, 0))
        self._number_rows(part, PART_FIELDS)
        self.size_note = ttk.Label(part, text="", foreground="#5f6368", wraplength=330, justify="left")
        self.size_note.grid(row=len(PART_FIELDS), column=0, columnspan=3, sticky="w", pady=(2, 0))

        cut = ttk.LabelFrame(form, text="Cut", padding=6)
        cut.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(cut, text="Kerf table").grid(row=0, column=0, sticky="w", pady=2)
        self.kerf_box = ttk.Combobox(cut, textvariable=self.vars["kerf_table"], state="readonly", width=22)
        self.kerf_box.grid(row=0, column=1, columnspan=2, sticky="we", padx=4)
        ttk.Label(cut, text="Direction").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Combobox(cut, textvariable=self.vars["direction"], state="readonly", width=22,
                     values=("upper_first", "lower_first")).grid(row=1, column=1, columnspan=2, sticky="we", padx=4)
        ttk.Label(cut, text="Hole channel from").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Combobox(cut, textvariable=self.vars["channel"], state="readonly", width=22,
                     values=CHANNELS).grid(row=2, column=1, columnspan=2, sticky="we", padx=4)
        self._number_rows(cut, CUT_FIELDS, first_row=3)

        out = ttk.LabelFrame(form, text="Output", padding=6)
        out.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(out, text="Job name").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(out, textvariable=self.vars["name"], width=34).grid(row=0, column=1, sticky="we", padx=4)
        ttk.Label(out, text="Folder").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(out, textvariable=self.vars["out"], width=34).grid(row=1, column=1, sticky="we", padx=4)
        ttk.Button(out, text="…", width=3, command=self._browse("out", "dir")).grid(row=1, column=2)
        out.columnconfigure(1, weight=1)

        buttons = ttk.Frame(form, padding=(0, 10, 0, 0))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Preview", command=self.preview).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(buttons, text="Generate files", command=self.generate).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=4)
        ttk.Button(buttons, text="Open folder", command=self.open_folder).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Label(form, text="Test every program cold first: wire power off, no foam.",
                  foreground="#b3261e", padding=(0, 8, 0, 0), wraplength=330).pack(fill=tk.X)

    def _number_rows(self, parent: ttk.LabelFrame, fields: list[tuple[str, str, str, str]], first_row: int = 0) -> None:
        for i, (key, label, _, unit) in enumerate(fields):
            row = first_row + i
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(parent, textvariable=self.vars[key], width=10)
            entry.grid(row=row, column=1, sticky="w", padx=4)
            ttk.Label(parent, text=unit, foreground="#5f6368").grid(row=row, column=2, sticky="w")
            if key == "span":
                self.span_entry = entry

    # --- stock size: absolute or as a margin around the outlines ---------------------------

    def _queue_size_update(self) -> None:
        if not self._size_pending:
            self._size_pending = True
            self.root.after_idle(self._update_size)

    def _update_size(self) -> None:
        """Measure the outlines, then fill whichever stock values were not typed last."""
        self._size_pending = False
        self.outlines = None
        v = self.values()
        simplify = _number(v["simplify"])
        try:
            if v["source"] == "step":
                faces = (int(v["root_face"]), int(v["tip_face"]))
                root_path = tip_path = Path(v["step"].strip())
            else:
                faces = None
                root_path, tip_path = Path(v["root"].strip()), Path(v["tip"].strip())
            if not (root_path.is_file() and tip_path.is_file()):
                raise DxfError("files missing")
            root, tip, _, _ = read_outlines(root_path, tip_path, faces, 0.01 if simplify is None else simplify)
            self.outlines = outline_size(root, tip)
        except (DxfError, ValueError, OSError):
            self.size_note.configure(text="Margins need the outlines: pick the files first.")
            return
        length, height = self.outlines
        self.size_note.configure(text=f"Outlines {length:.1f} x {height:.1f} mm (length x height), "
                                      "centred in the stock. Leave room for the kerf.")
        for stock in MARGINS:
            if self.vars["stock_mode"].get() == "margin":
                self._margin_edited(stock, keep_mode=True)
            else:
                self._stock_edited(stock, keep_mode=True)

    def _size_of(self, stock: str) -> float | None:
        if self.outlines is None:
            return None
        return self.outlines[0] if stock == "stock_length" else self.outlines[1]

    def _stock_edited(self, stock: str, *_: object, keep_mode: bool = False) -> None:
        if self._syncing:
            return
        size, value = self._size_of(stock), _number(self.vars[stock].get())
        if not keep_mode:
            self.vars["stock_mode"].set("absolute")
        if size is None or value is None:
            return
        self._set_quietly(MARGINS[stock], (value - size) / 2)

    def _margin_edited(self, stock: str, *_: object, keep_mode: bool = False) -> None:
        if self._syncing:
            return
        size, margin = self._size_of(stock), _number(self.vars[MARGINS[stock]].get())
        if not keep_mode:
            self.vars["stock_mode"].set("margin")
        if size is None or margin is None:
            return
        self._set_quietly(stock, size + 2 * margin)

    def _set_quietly(self, key: str, value: float) -> None:
        self._syncing = True
        try:
            self.vars[key].set(f"{value:.2f}".rstrip("0").rstrip("."))
        finally:
            self._syncing = False

    # --- STEP models ----------------------------------------------------------------------

    def _source_changed(self) -> None:
        """Show the DXF rows or the STEP rows. With a STEP model the span is measured, not typed."""
        mode = "step" if self.vars["source"].get() == "step" else "dxf"
        for name, widgets in self.mode_rows.items():
            for w in widgets:
                if name == mode:
                    w.grid()
                else:
                    w.grid_remove()
        self.span_entry.configure(state="readonly" if mode == "step" else "normal")

    def _queue_step_load(self) -> None:
        if not self._step_pending:  # several fields may change at once (load_values)
            self._step_pending = True
            self.root.after_idle(self._load_step)

    def _load_step(self) -> None:
        """Read the STEP model, list its flat faces and keep (or suggest) the root and tip."""
        self._step_pending = False
        self.step_model = None
        for key, box in self.face_boxes.items():
            box.configure(values=())
            self.face_text[key].set("")
        self.span_note.configure(text="")
        name = self.vars["step"].get().strip()
        if self.vars["source"].get() != "step" or not name or not Path(name).is_file():
            return
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            model = read_step(Path(name))
        except DxfError as error:
            self.span_note.configure(text=str(error), foreground="#b3261e")
            self.status.set("Error: " + str(error))
            return
        finally:
            self.root.configure(cursor="")
        self.step_model = model
        tags = {f.tag for f in model.faces}
        chosen = [self.vars[k].get().strip() for k in ("root_face", "tip_face")]
        if not all(c.isdigit() and int(c) in tags for c in chosen) or chosen[0] == chosen[1]:
            pair = model.suggest() or (model.faces[0].tag, model.faces[0].tag)
            self.vars["root_face"].set(str(pair[0]))
            self.vars["tip_face"].set(str(pair[1]))
        labels = [f.label() for f in model.faces]
        for key, box in self.face_boxes.items():
            box.configure(values=labels)
            self.face_text[key].set(model.face(int(self.vars[key].get())).label())
        self._measure_span()

    def _face_picked(self, key: str, _event: object = None) -> None:
        found = re.match(r"face (\d+):", self.face_text[key].get())
        if found:
            self.vars[key].set(found.group(1))
            self._measure_span()

    def _measure_span(self) -> None:
        """Put the distance between the picked faces in the span field."""
        model = self.step_model
        if model is None:
            return
        try:
            cut = sections(model, int(self.vars["root_face"].get()), int(self.vars["tip_face"].get()), 0.0)
        except (DxfError, ValueError) as error:
            self.span_note.configure(text=str(error), foreground="#b3261e")
            return
        self.vars["span"].set(f"{cut.span:.3f}".rstrip("0").rstrip("."))
        note = (f"Span {cut.span:.3f} mm, measured between the faces. "
                f"Chords {cut.root.chord:.1f} and {cut.tip.chord:.1f} mm.")
        if cut.warnings and "turned over" in cut.warnings[0]:
            note += " " + cut.warnings[0].split(": ", 1)[1].capitalize() + "."
        self.span_note.configure(text=note, foreground="#5f6368")

    def _build_right(self, right: ttk.Frame) -> None:
        tabs = ttk.Notebook(right)
        tabs.pack(fill=tk.BOTH, expand=True)

        # 3D tab: orbitable view plus a wire scrubber
        frame3d = ttk.Frame(tabs)
        self.canvas3d = tk.Canvas(frame3d, background="white", highlightthickness=0)
        self.canvas3d.pack(fill=tk.BOTH, expand=True)
        self.view3d = View3D(self.canvas3d)
        self._build_readout(frame3d)
        controls = ttk.Frame(frame3d, padding=(6, 4))
        controls.pack(fill=tk.X)
        self.play_text = tk.StringVar(value="Play")
        ttk.Button(controls, textvariable=self.play_text, width=7, command=self.toggle_play).pack(side=tk.LEFT)
        self.wire_pos = tk.DoubleVar(value=0)
        self.scrubber = ttk.Scale(controls, from_=0, to=1, variable=self.wire_pos, command=self._scrub)
        self.scrubber.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.wire_label = tk.StringVar(value="")
        ttk.Label(controls, textvariable=self.wire_label, width=30).pack(side=tk.LEFT)
        ttk.Button(controls, text="Reset view", command=self.view3d.reset).pack(side=tk.LEFT)
        self.playing = False
        tabs.add(frame3d, text="3D")

        self.align_frame = ttk.Frame(tabs, padding=12)
        tabs.add(self.align_frame, text="Align stock")
        self._update_alignment()

        self.machine = MachinePanel(tabs, self._current_program, self._letters, self.status.set,
                                    faces=self._faces, on_status=self._live_status, travel=self._travel)
        tabs.add(self.machine, text="Machine")
        # the section view lives in the Machine tab, beside the console, to watch the live position
        self.section_frame = ttk.Frame(self.machine.views)
        bar = ttk.Frame(self.section_frame, padding=(4, 2))
        bar.pack(fill=tk.X)
        self.mouse_moves = tk.BooleanVar(value=False)  # not remembered: a stray click must not move the wire
        ttk.Checkbutton(bar, text="Mouse moves the wire", variable=self.mouse_moves,
                        command=self._mouse_moves_changed).pack(side=tk.LEFT)
        ttk.Label(bar, text="Speed").pack(side=tk.LEFT, padx=(14, 4))
        self.speed_scale = tk.Scale(bar, from_=MOUSE_FEED_MIN, to=MOUSE_FEED_FALLBACK_MAX, resolution=50,
                                    orient=tk.HORIZONTAL, length=220, showvalue=False,
                                    variable=self.machine.mouse_feed, command=lambda _v: self._show_speed())
        self.speed_scale.pack(side=tk.LEFT)
        self.speed_text = tk.StringVar()
        ttk.Label(bar, textvariable=self.speed_text, width=26).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(bar, text="Fit view", command=self.fit_view).pack(side=tk.LEFT, padx=8)
        ttk.Label(self.section_frame, foreground="#5f6368", padding=(4, 0), text=(
            "wheel zoom · right-drag pan · click: both towers · X or Z + click: that tower · drag: towers follow"
            )).pack(anchor="w")
        self._show_speed()
        self.canvas = tk.Canvas(self.section_frame, background="white", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.machine.views.insert(0, self.section_frame, text="Section")
        self.machine.views.select(self.section_frame)  # type: ignore[no-untyped-call]
        report_frame = ttk.Frame(tabs)
        self.report = tk.Text(report_frame, wrap="none", font=("Consolas", 10))
        scroll = ttk.Scrollbar(report_frame, orient=tk.VERTICAL, command=self.report.yview)
        self.report.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.report.pack(fill=tk.BOTH, expand=True)
        tabs.add(report_frame, text="Report")
        self.report_frame = report_frame
        self.frame3d = frame3d
        self.tabs = tabs
        self.canvas.bind("<Configure>", lambda _e: self.draw())
        self.canvas.bind("<Motion>", self._show_position)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, ZOOM_STEP))  # X11 wheel
        self.canvas.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / ZOOM_STEP))
        self.canvas.bind("<ButtonPress-3>", self._pan_start)
        self.canvas.bind("<B3-Motion>", self._pan_move)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Enter>", lambda _e: self.canvas.focus_set() if self.mouse_moves.get() else None)
        self.root.bind_all("<KeyPress>", self._key_down, add="+")
        self.root.bind_all("<KeyRelease>", self._key_up, add="+")

    def _update_alignment(self) -> None:
        """Fill the Align stock tab: where to put the wire on each long stock edge."""
        frame = self.align_frame
        for child in frame.winfo_children():
            child.destroy()
        g = self.geometry
        intro = (
            "Put the wire on a stock edge, then push the stock against the wire to place and square it.\n"
            "Both towers go to the same position, so the wire runs exactly along the edge.\n\n"
            "Run the G-code with the wire COLD and no stock on the table: it lifts first, crosses at the\n"
            "safe height, then goes straight down. Then slide the stock in until the edge just touches the\n"
            "wire along its whole length. Use a top edge to set the stock's x position; the bottom edges lie\n"
            "on the table. Positions are the wire's centre: the stock face sits half a wire diameter away."
        )
        ttk.Label(frame, text=intro, justify="left").grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 12))
        if g is None:
            ttk.Label(frame, text="Press Preview first.", foreground="#5f6368").grid(row=1, column=0, sticky="w")
            return
        l1, l2, l3, l4 = g.letters
        headers = ("Edge", f"{l1} = {l3}", f"{l2} = {l4}", "", "", "")
        for col, text in enumerate(headers):
            ttk.Label(frame, text=text, font=("Segoe UI", 9, "bold")).grid(row=1, column=col, sticky="w", padx=6)
        for row, edge in enumerate(alignment_edges(g), start=2):
            ttk.Label(frame, text=edge.name).grid(row=row, column=0, sticky="w", padx=6, pady=3)
            ttk.Label(frame, text=f"{edge.x:.3f}", font=("Consolas", 10)).grid(row=row, column=1, sticky="e", padx=6)
            ttk.Label(frame, text=f"{edge.z:.3f}", font=("Consolas", 10)).grid(row=row, column=2, sticky="e", padx=6)
            ttk.Button(frame, text="Copy G-code", command=functools.partial(self._copy_edge, edge)).grid(
                row=row, column=3, padx=4)
            ttk.Button(frame, text="Show in 3D", command=functools.partial(self._show_edge, edge)).grid(
                row=row, column=4, padx=4)
            ttk.Button(frame, text="Go there", command=functools.partial(self._go_to_edge, edge)).grid(
                row=row, column=5, padx=4)
        self.align_text = tk.Text(frame, height=4, width=60, font=("Consolas", 10))
        self.align_text.grid(row=7, column=0, columnspan=6, sticky="we", pady=(12, 0))
        self.align_text.insert("1.0", "Copy G-code puts the three lines for that edge here and on the clipboard.")

    def _current_program(self) -> Program:
        """The G-code of the last Preview, for the Machine tab. Raises ValueError if stale."""
        if self.plan is None or self.result is None or self.preview_values is None:
            raise ValueError("press Preview first")
        ignore = ("out",)
        now = {k: v for k, v in self.values().items() if k not in ignore}
        then = {k: v for k, v in self.preview_values.items() if k not in ignore}
        if now != then:
            raise ValueError("the form changed since the last Preview: press Preview again")
        return Program(f"{self.plan.request.stem}.nc", self.result.gcode, self.result.total_time)

    def _faces(self) -> tuple[float, float] | None:
        """Root and tip faces as fractions of the tower distance, for the last Preview if current."""
        g = self.geometry
        try:
            self._current_program()
        except ValueError:
            return None
        if g is None or g.wire_span <= 0:
            return None
        return g.root_y / g.wire_span, g.tip_y / g.wire_span

    def _live_status(self, status: Status | None) -> None:
        self.live = status
        self._draw_live()
        self._show_speed()

    def _show_speed(self) -> None:
        """Mouse speed label; the slider reaches the controller's rapid rate once it is known."""
        rapid = self.machine.rapid_rate()
        top = int(rapid) if rapid is not None else MOUSE_FEED_FALLBACK_MAX
        if int(float(self.speed_scale.cget("to"))) != top:
            self.speed_scale.configure(to=top)
        feed = self.machine.mouse_feed_value()
        if rapid is not None and feed >= rapid:
            self.speed_text.set(f"RAPID {feed:.0f} mm/min")
        elif rapid is None:
            self.speed_text.set(f"{feed:.0f} mm/min (rapid: connect)")
        else:
            self.speed_text.set(f"{feed:.0f} mm/min (rapid {rapid:.0f})")

    def _go_to_edge(self, edge: AlignEdge) -> None:
        """Send the edge's three lines through the Machine tab (lift, across, down)."""
        if self.machine.run_block(f"wire to the {edge.name}", "\n".join(edge.gcode), "Wire to a stock edge", (
            "The wire lifts to the safe height, crosses, then goes straight down to the edge.\n\n"
            "Check before you press OK:\n"
            "  - the wire heat is OFF\n"
            "  - no stock on the table yet (slide it in afterwards)\n"
            "  - the work zero is the one this job was made for")):
            self.tabs.select(self.machine)  # type: ignore[no-untyped-call]

    def _travel(self) -> dict[str, tuple[float, float]] | None:
        """Each axis letter's (min, max) from the machine profile in the form, or None."""
        path = Path(self.vars["profile"].get().strip())
        try:
            key = (str(path), path.stat().st_mtime)
            if self._travel_cache is None or self._travel_cache[0] != key:
                profile = load_profile(path)
                self._travel_cache = (key, {a.letter: (a.min, a.max) for a in profile.axes})
        except (OSError, FoamcamError):
            return None
        return dict(self._travel_cache[1])

    def _letters(self) -> tuple[str, str, str, str]:
        return self.geometry.letters if self.geometry is not None else ("X", "Y", "Z", "A")

    def _copy_edge(self, edge: AlignEdge) -> None:
        text = "\n".join(edge.gcode)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.align_text.delete("1.0", tk.END)
        self.align_text.insert("1.0", f"; wire on the {edge.name}\n{text}")
        self.status.set(f"Copied the G-code for the {edge.name}. Paste it into ioSender's MDI, wire cold.")

    def _show_edge(self, edge: AlignEdge) -> None:
        self.view3d.highlight = (edge.x, edge.z, edge.name)
        self.view3d.draw()
        self.tabs.select(self.frame3d)  # type: ignore[no-untyped-call]

    def _build_readout(self, parent: ttk.Frame) -> None:
        """Digital readout of the wire at the slider position, like a machine DRO."""
        dro = tk.Frame(parent, background="#202124", padx=10, pady=6)
        dro.pack(fill=tk.X)
        big = ("Consolas", 15, "bold")
        small = ("Consolas", 10)
        self.dro_axes: list[tuple[tk.StringVar, tk.StringVar]] = []
        for i in range(4):
            letter, value = tk.StringVar(value=" "), tk.StringVar(value="   ---.---")
            cell = tk.Frame(dro, background="#202124")
            cell.grid(row=0, column=i, sticky="w", padx=(0, 26))
            tk.Label(cell, textvariable=letter, font=big, fg="#8ab4f8", bg="#202124").pack(side=tk.LEFT)
            tk.Label(cell, textvariable=value, font=big, fg="#e8eaed", bg="#202124").pack(side=tk.LEFT)
            self.dro_axes.append((letter, value))
        self.dro_detail = tk.StringVar(value="Press Preview, then scrub or play the cut.")
        tk.Label(dro, textvariable=self.dro_detail, font=small, fg="#bdc1c6", bg="#202124",
                 justify="left", anchor="w").grid(row=1, column=0, columnspan=5, sticky="w", pady=(4, 0))

    def _update_readout(self, i: int) -> None:
        g = self.geometry
        if g is None:
            for letter, value in self.dro_axes:
                letter.set(" ")
                value.set("   ---.---")
            self.dro_detail.set("Press Preview, then scrub or play the cut.")
            return
        a = (*g.tower_12[i], *g.tower_34[i])
        for (letter, value), name, v in zip(self.dro_axes, g.letters, a, strict=True):
            letter.set(name)
            value.set(f"{v:10.3f}")
        rx, rz = g.root_cut[i]
        tx, tz = g.tip_cut[i]
        speeds = ""
        if i > 0 and g.times[i] > g.times[i - 1]:
            dt = g.times[i] - g.times[i - 1]
            sr = math.dist(g.root_cut[i], g.root_cut[i - 1]) / dt
            st = math.dist(g.tip_cut[i], g.tip_cut[i - 1]) / dt
            speeds = f"   wire speed root {sr:5.0f}  tip {st:5.0f} mm/min"
        phase = {"entry": "at entry point", "lead_in": "lead-in", "cut": "cutting", "exit": "exit"}[g.phases[i]]
        self.dro_detail.set(
            f"root face  x {rx:8.3f}  z {rz:8.3f}     tip face  x {tx:8.3f}  z {tz:8.3f}\n"
            f"{phase:<15}{speeds}"
        )

    # --- actions --------------------------------------------------------------------------

    def values(self) -> dict[str, str]:
        return {k: v.get() for k, v in self.vars.items()}

    def _browse(self, key: str, kind: str) -> Callable[[], None]:
        def pick() -> None:
            current = Path(self.vars[key].get() or REPO)
            start = str(current if current.is_dir() else current.parent)
            if kind == "dir":
                chosen = filedialog.askdirectory(initialdir=start)
            else:
                types = {
                    "dxf": [("DXF drawing", "*.dxf")],
                    "step": [("STEP model", "*.step *.stp")],
                }.get(kind, [("Machine profile", "*.yaml")])
                chosen = filedialog.askopenfilename(initialdir=start, filetypes=types + [("All files", "*.*")])
            if chosen:
                self.vars[key].set(chosen)
        return pick

    def _load_kerf_tables(self) -> None:
        try:
            names = list(load_profile(Path(self.vars["profile"].get())).kerf_tables)
        except (FoamcamError, OSError):
            names = []
        self.kerf_box.configure(values=names)
        if names and self.vars["kerf_table"].get() not in names:
            self.vars["kerf_table"].set(names[0])

    def preview(self) -> bool:
        """Build the job and run the CAM. Returns True if the cut is valid."""
        self.plan = self.result = self.geometry = None
        self.preview_values = None
        self.playing = False
        self.play_text.set("Play")
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            request = request_from_form(self.values())
            self.plan = make_job(request)
            self.result = run_cam(self.plan)
            self.geometry = preview_geometry(self.plan, self.result)
        except (FormError, DxfError, FoamcamError) as error:
            self._show_report(self._plan_text() + "\nERROR\n" + str(error))
            self.status.set("Error: " + str(error).splitlines()[0])
            self.tabs.select(self.report_frame)  # type: ignore[no-untyped-call]
            self.draw()
            self.view3d.show(None)
            self._update_alignment()
            self.wire_label.set("")
            self._update_readout(0)
            self.machine.refresh()
            return False
        finally:
            self.root.configure(cursor="")
        self._show_report(self._plan_text() + "\n" + self.result.report)
        self.status.set(f"OK: cut time {self.result.total_time:.2f} min, "
                        f"{sum(m.kind != 'rapid' for m in self.result.moves)} feed moves. Press Generate to write files.")
        if self.tabs.select() == str(self.report_frame):  # type: ignore[no-untyped-call]
            self.tabs.select(self.frame3d)  # type: ignore[no-untyped-call]
        self.draw()
        self.view3d.show(self.geometry)
        self._update_alignment()
        self.scrubber.configure(to=len(self.geometry.tower_12) - 1)
        self.wire_pos.set(0)
        self._scrub("0")
        self.preview_values = self.values()
        self.machine.refresh()
        save_settings(self.values())
        return True

    def _scrub(self, value: str) -> None:
        g = self.geometry
        if g is None:
            return
        i = round(float(value))
        self.view3d.set_wire(i)
        self._update_readout(i)
        t = g.times[i]
        self.wire_label.set(f"move {i} / {len(g.times) - 1}   {int(t)}:{int(t % 1 * 60):02d} of "
                            f"{int(g.times[-1])}:{int(g.times[-1] % 1 * 60):02d}")

    def toggle_play(self) -> None:
        if self.geometry is None:
            return
        self.playing = not self.playing
        self.play_text.set("Pause" if self.playing else "Play")
        if self.playing:
            if self.wire_pos.get() >= len(self.geometry.tower_12) - 1:
                self.wire_pos.set(0)
            self._step()

    def _step(self) -> None:
        """Advance the wire so the whole cut plays in about 12 seconds."""
        g = self.geometry
        if not self.playing or g is None:
            return
        last = len(g.tower_12) - 1
        i = min(last, int(self.wire_pos.get()) + max(1, last // 360))
        self.wire_pos.set(i)
        self._scrub(str(i))
        if i >= last:
            self.playing = False
            self.play_text.set("Play")
            return
        self.root.after(33, self._step)

    def generate(self) -> None:
        if not self.preview() or self.plan is None or self.result is None:
            return
        out = Path(self.vars["out"].get() or REPO / "output")
        try:
            written = write_files(self.plan, self.result, out)
        except OSError as error:
            messagebox.showerror("Cannot write files", str(error))
            return
        self.status.set(f"Wrote {len(written)} files to {out}: {', '.join(p.name for p in written)}")

    def open_folder(self) -> None:
        out = Path(self.vars["out"].get() or REPO / "output")
        out.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(out)
        else:
            subprocess.Popen(["xdg-open", str(out)])

    def close(self) -> None:
        if not self._keep_changes():
            return
        if not self.machine.disconnect():  # asks first if a cut is running
            return
        self.playing = False
        save_settings(self.values())
        self.root.destroy()

    def _plan_text(self) -> str:
        if self.plan is None:
            return ""
        lines = [f"warning: {w}" for w in self.plan.warnings] + list(self.plan.generated.summary)
        return "\n".join(lines) + "\n"

    def _show_report(self, text: str) -> None:
        self.report.configure(state="normal")
        self.report.delete("1.0", tk.END)
        self.report.insert("1.0", text)
        self.report.configure(state="disabled")

    # --- drawing --------------------------------------------------------------------------

    def _view_box(self) -> tuple[float, float, float, float]:
        """x0, z0, x1, z1 of what the section view fits: the stock and both cut paths with a
        margin, not the whole travel, so the part stays large in the Machine tab's pane."""
        g = self.geometry
        assert g is not None
        sx0, sz0, sx1, sz1 = g.stock
        pts = g.root_cut + g.tip_cut + [(sx0, sz0), (sx1, sz1)]
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        z0, z1 = min(p[1] for p in pts), max(p[1] for p in pts)
        pad = max(10.0, 0.08 * max(x1 - x0, z1 - z0))
        return x0 - pad, z0 - pad, x1 + pad, z1 + pad

    def _transform(self) -> tuple[float, float, float, float] | None:
        """(scale, x offset, y offset, world y max) to fit the view box in the canvas, below
        the legend.

        The horizontal axis is inverted: +x (away from the operator) runs to the
        left, as seen from the X/Y side of the machine. Canvas x = ox - scale * x."""
        g = self.geometry
        if g is None:
            return None
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 200 or h < 150:  # not laid out yet
            w, h = max(w, 800), max(h, 600)
        x0, z0, x1, z1 = self._view_box()
        margin, legend = 30, LEGEND_HEIGHT
        scale = min((w - 2 * margin) / max(x1 - x0, 1e-6), (h - 2 * margin - legend) / max(z1 - z0, 1e-6))
        ox = (w + scale * (x1 - x0)) / 2 + scale * x0
        oy = legend + (h - legend - scale * (z1 - z0)) / 2 + scale * z1
        # zoom and pan: canvas' = zoom * canvas + shift
        k, (tx, ty) = self.view_zoom, self.view_shift
        return scale * k, ox * k + tx, oy * k + ty, z1

    def _to_canvas(self, points: list[Point]) -> list[float]:
        t = self._transform()
        assert t is not None
        scale, ox, oy, _ = t
        out: list[float] = []
        for x, z in points:
            out += [ox - scale * x, oy - scale * z]
        return out

    def draw(self) -> None:
        c = self.canvas
        c.delete("all")
        g = self.geometry
        if g is None:
            c.create_text(20, 20, anchor="nw", fill="#5f6368",
                          text="No preview yet. Fill in the form and press Preview.")
            return
        x0, z0, x1, z1 = g.travel
        c.create_rectangle(*self._to_canvas([(x0, z0), (x1, z1)]), outline=COLOURS["travel"], dash=(4, 3))
        sx0, sz0, sx1, sz1 = g.stock
        c.create_rectangle(*self._to_canvas([(sx0, sz0), (sx1, sz1)]),
                           fill=COLOURS["stock"], outline=COLOURS["stock_edge"])
        c.create_line(*self._to_canvas(g.tower_12), fill=COLOURS["tower_12"], dash=(5, 3))
        c.create_line(*self._to_canvas(g.tower_34), fill=COLOURS["tower_34"], dash=(5, 3))
        c.create_line(*self._to_canvas(g.root_cut), fill=COLOURS["root"], width=2)
        c.create_line(*self._to_canvas(g.tip_cut), fill=COLOURS["tip"], width=2)
        for target in (g.root_target, g.tip_target, *g.root_holes, *g.tip_holes):
            c.create_line(*self._to_canvas(target + target[:1]), fill=COLOURS["target"], width=1)

        # coordinates at the corners of the view (x runs right to left)
        x0, z0, x1, z1 = self._view_box()
        br = self._to_canvas([(x0, z0)])
        tl = self._to_canvas([(x1, z1)])
        c.create_text(br[0], br[1] + 12, text=f"x {x0:.0f}", anchor="e", fill="#5f6368")
        c.create_text(tl[0], br[1] + 12, text=f"x {x1:.0f}  (<- +x away from operator)", anchor="w", fill="#5f6368")
        c.create_text(br[0] + 6, tl[1], text=f"z {z1:.0f}", anchor="w", fill="#5f6368")
        c.create_text(br[0] + 6, br[1], text=f"z {z0:.0f}", anchor="w", fill="#5f6368")

        legend = [
            (COLOURS["root"], None, 2, "wire at root face (kerf compensated)"),
            (COLOURS["tip"], None, 2, "wire at tip face (kerf compensated)"),
            (COLOURS["target"], None, 1, "drawn outlines"),
            (COLOURS["tower_12"], (5, 3), 1, "tower axes 1-2 carriage"),
            (COLOURS["tower_34"], (5, 3), 1, "tower axes 3-4 carriage"),
            (COLOURS["stock_edge"], None, 6, "stock"),
            (COLOURS["travel"], (4, 3), 1, "shared travel"),
        ]
        for i, (colour, dash, width, label) in enumerate(legend):
            lx, y = _legend_slot(i)
            c.create_line(lx, y, lx + 28, y, fill=colour, width=width, dash=dash or ())
            c.create_text(lx + 34, y, text=label, anchor="w", fill="#202124")
        self._draw_live()

    def _draw_live(self) -> None:
        """The machine's live position on the section: both carriages, the wire between them
        (seen end on) and where it is at the root and tip faces. Work coordinates are the
        section frame, as the G-code was made: true only with the program's work zero."""
        c = self.canvas
        c.delete("live")
        g, st = self.geometry, self.live
        if g is None or st is None:
            return
        towers = tower_positions(st.wpos, g.letters)
        if towers is None:
            return
        t12, t34 = towers
        root = on_wire(towers, g.root_y / g.wire_span) if g.wire_span > 0 else t12
        tip = on_wire(towers, g.tip_y / g.wire_span) if g.wire_span > 0 else t34
        c.create_line(*self._to_canvas([t12, t34]), fill="#d93025", width=1, dash=(2, 2), tags="live")
        for (x, z), colour in ((t12, COLOURS["tower_12"]), (t34, COLOURS["tower_34"])):
            cx, cz = self._to_canvas([(x, z)])
            c.create_rectangle(cx - 6, cz - 6, cx + 6, cz + 6, outline=colour, width=2, tags="live")
        for (x, z), colour in ((root, COLOURS["root"]), (tip, COLOURS["tip"])):
            cx, cz = self._to_canvas([(x, z)])
            c.create_oval(cx - 5, cz - 5, cx + 5, cz + 5, fill=colour, outline="#d93025", width=2, tags="live")
        if self.move_target is not None:  # where the mouse sent the towers
            mx, mz, which = self.move_target
            cx, cz = self._to_canvas([(mx, mz)])
            colour = "#d93025" if len(which) == 2 else (COLOURS["tower_12"] if which == (0,) else COLOURS["tower_34"])
            c.create_line(cx - 9, cz, cx + 9, cz, fill=colour, width=2, tags="live")
            c.create_line(cx, cz - 9, cx, cz + 9, fill=colour, width=2, tags="live")
        lx, y = _legend_slot(7)
        c.create_oval(lx + 9, y - 5, lx + 19, y + 5, fill=COLOURS["root"], outline="#d93025", width=2, tags="live")
        c.create_text(lx + 34, y, anchor="w", fill="#d93025", tags="live",
                      text=f"live: wire at the faces, squares = carriages ({st.state})")

    def _to_world(self, cx: float, cy: float) -> Point | None:
        t = self._transform()
        if t is None:
            return None
        scale, ox, oy, _ = t
        return (ox - cx) / scale, (oy - cy) / scale

    def _show_position(self, event: "tk.Event[tk.Canvas]") -> None:
        p = self._to_world(event.x, event.y)
        if p is None:
            return
        x, z = p
        if self.result is not None:
            self.status.set(f"x {x:7.1f}  z {z:7.1f} mm      cut time {self.result.total_time:.2f} min")

    # --- section view: zoom, pan, and moving the wire with the mouse -----------------------

    def fit_view(self) -> None:
        self.view_zoom, self.view_shift = 1.0, (0.0, 0.0)
        self.draw()

    def _zoom_at(self, cx: float, cy: float, factor: float) -> None:
        """Zoom by `factor` keeping the point under the cursor where it is."""
        k = min(max(self.view_zoom * factor, ZOOM_RANGE[0]), ZOOM_RANGE[1])
        f = k / self.view_zoom
        tx, ty = self.view_shift
        self.view_zoom = k
        self.view_shift = (cx - f * (cx - tx), cy - f * (cy - ty))
        self.draw()

    def _wheel(self, event: "tk.Event[tk.Canvas]") -> None:
        self._zoom_at(event.x, event.y, ZOOM_STEP ** (event.delta / 120))

    def _pan_start(self, event: "tk.Event[tk.Canvas]") -> None:
        self._pan_from = (event.x, event.y)

    def _pan_move(self, event: "tk.Event[tk.Canvas]") -> None:
        tx, ty = self.view_shift
        self.view_shift = (tx + event.x - self._pan_from[0], ty + event.y - self._pan_from[1])
        self._pan_from = (event.x, event.y)
        self.draw()

    def _key_down(self, event: "tk.Event[tk.Misc]") -> None:
        self.keys_down.add(str(event.keysym).lower())

    def _key_up(self, event: "tk.Event[tk.Misc]") -> None:
        self.keys_down.discard(str(event.keysym).lower())

    def _mouse_moves_changed(self) -> None:
        if self.mouse_moves.get():
            self.canvas.focus_set()
            self.status.set("Mouse moves the wire: click to send both towers there, hold X or Z for one tower, "
                            "drag to make the towers follow.")

    def towers_for_keys(self) -> tuple[int, ...]:
        """Towers a click moves: the one whose horizontal letter (X or Z here) is held, else both."""
        letters = self._letters()
        held = tuple(t for t in (0, 1) if letters[2 * t].lower() in self.keys_down)
        return held or (0, 1)

    def _press(self, event: "tk.Event[tk.Canvas]") -> None:
        self._press_at = (event.x, event.y)
        self._press_towers = self.towers_for_keys()
        self._dragging = False

    def _target(self, event: "tk.Event[tk.Canvas]") -> dict[str, float] | None:
        p = self._to_world(event.x, event.y)
        if p is None:
            return None
        self.move_target = (p[0], p[1], self._press_towers)
        self._draw_live()
        return tower_targets(self._letters(), p[0], p[1], self._press_towers)

    def _mouse_may_move(self) -> bool:
        if self.geometry is None:
            return False
        if not self.mouse_moves.get():
            return False
        if not self.machine.can_move():
            self.status.set("The wire moves with the mouse only when connected and Idle (no program running).")
            return False
        return True

    def _drag(self, event: "tk.Event[tk.Canvas]") -> None:
        if self._press_at is None:
            return
        if not self._dragging:
            dx, dy = event.x - self._press_at[0], event.y - self._press_at[1]
            if dx * dx + dy * dy < DRAG_PIXELS ** 2 or not self._mouse_may_move():
                return
            self._dragging = True
        target = self._target(event)
        if target is not None:
            self.machine.follow(target)

    def _release(self, event: "tk.Event[tk.Canvas]") -> None:
        if self._press_at is None:
            return
        self._press_at = None
        if self._dragging:
            self._dragging = False
            self.machine.end_follow()
            return
        if not self.mouse_moves.get():
            if self.geometry is not None:
                self.status.set('Tick "Mouse moves the wire" above the section to move the towers by clicking.')
            return
        if self._mouse_may_move():
            target = self._target(event)
            if target is not None:
                self.machine.move_to(target)


def main(argv: list[str] | None = None) -> int:
    root = tk.Tk()
    window = SlicerWindow(root)
    if argv:
        root.after(100, lambda: window.open_project(Path(argv[0])))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
