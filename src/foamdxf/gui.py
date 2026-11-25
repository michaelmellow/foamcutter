"""Desktop window for the DXF front end: python -m foamdxf --gui [PROJECT.foamproj]

Pick the root and tip DXFs and the machine profile, enter the span and stock
size (or pick a STEP model and its root and tip faces: the span is then
measured from the model), press Preview to see the cut, then Generate to write the job, G-code,
report and SVG. File > Save project keeps every value and a copy of each input
file in one .foamproj file (see project.py). Uses Tkinter only (part of Python), so it needs no extra
packages. The last values are remembered in ~/.foamdxf-gui.json.
"""

import argparse
import functools
import json
import math
import os
import re
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from foamcam.cam import CamResult
from foamcam.errors import FoamcamError
from foamcam.geometry import inside
from foamcam.profile import Profile, load_profile

from .crosstab import CrossCutPanel
from .errors import DxfError
from .facepick import FacePicker
from .holes import (
    CHANNELS,
    SHAPES,
    ChannelPreview,
    ChannelSpec,
    HolePair,
    area_centre,
    preview_channels,
    specs_from_text,
    specs_to_text,
)
from .jobgen import CutSettings, Placement
from .kerftab import KerfPanel
from .machine import (
    MOUSE_FEED_FALLBACK_MAX,
    MOUSE_FEED_MIN,
    MachinePanel,
    Program,
    load_machine_settings,
    on_wire,
    save_machine_settings,
    tower_positions,
    tower_targets,
)
from .outline import Point
from .pipeline import (
    CORNERS,
    AlignEdge,
    Plan,
    PreviewGeometry,
    Request,
    alignment_edges,
    block_at_corner,
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
from .uiscale import (
    UI_SIZE_KEY,
    UI_SIZES,
    ScrollArea,
    apply_ui_size,
    fit_to_screen,
    parse_ui_size,
)
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
    ("safe_clearance", "Safe height above stock", "20", "mm"),
    ("min_te", "Min trailing edge", "0.6", "mm, 0 = as drawn"),
    ("segment", "Max segment length", "1", "mm"),
    ("simplify", "Simplify tolerance", "0.01", "mm, 0 = keep all"),
]
# where the stock is: shown in the 3D tab's stock panel, set by hand or from the wire position
STOCK_FIELDS = [
    ("block_x", "Back face x", "", "mm, blank = centred"),
    ("block_z", "Bottom face z", "", "mm, blank = on the table"),
]
DEFAULTS = {key: default for key, _, default, _ in FILE_FIELDS + PART_FIELDS + CUT_FIELDS + STOCK_FIELDS}
DEFAULTS |= {"kerf_table": "", "direction": "upper_first", "name": "", "out": str(REPO / "output")}
DEFAULTS |= {"channel": "shortest"}  # side the wire enters inner contours (holes) from
# how the wire leaves a hole's channel ("rapid", "cut" or mm/min), and the drawn channels (JSON, holes.py)
DEFAULTS |= {"channel_exit": "rapid", "channels": ""}
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


VIEW3D_SHARE = 0.4  # the 3D view's first share of the Machine tab's width
RIGHT_PANE_MIN = 560  # px the tabs keep beside the form before the window scrolls sideways
STOCK_NOTE_WIDTH = 420  # wrap of the Stock tab's notes, px
STRAIGHT_WIRE_MM = 0.05  # towers closer than this: the wire runs along a stock edge
TABLE_TOLERANCE_MM = 0.01  # a stock bottom this far below the table is still on it


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
    labels |= {key: f"Stock {label[0].lower()}{label[1:]}" for key, label, _, _ in STOCK_FIELDS}
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
        block_z=number("block_z", labels["block_z"], optional=True),
    )
    exit_text = values.get("channel_exit", "rapid").strip().lower() or "rapid"
    channel_exit: str | float = exit_text
    if exit_text not in ("rapid", "cut"):
        try:
            channel_exit = float(exit_text)
        except ValueError:
            raise FormError('Channel exit: "rapid", "cut" or a speed in mm/min') from None
        if channel_exit <= 0:
            raise FormError("Channel exit: the speed must be more than 0")
    try:
        channels = specs_from_text(values.get("channels", ""))
    except DxfError as error:
        raise FormError(f"Hole channels: {error}") from None
    settings = CutSettings(
        cut_speed=req("speed", labels["speed"]),
        kerf_table=values.get("kerf_table", "").strip() or None,
        direction=values.get("direction", "upper_first"),
        max_segment_length=req("segment", labels["segment"]),
        min_te_thickness=req("min_te", labels["min_te"]),
        channel=values.get("channel", "shortest"),
        channels=channels,
        channel_exit=channel_exit,
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
HANDLE_PIXELS = 9  # a press this close to a channel handle grabs it
CHANNEL_PANE_WIDTH = 300  # pixels


@dataclass
class ChannelRow:
    """One hole's block in the channel pane."""

    block: tk.LabelFrame
    vars: dict[str, tk.StringVar]  # shape, angle, offset, entry, radius
    spins: dict[str, ttk.Spinbox]
    note: tk.StringVar  # where the channel ends, or why it cannot be cut


def _legend_slot(i: int) -> tuple[float, float]:
    """Canvas x, y of legend line i (0-7)."""
    return 12 + 290 * (i // 4), 12 + 16 * (i % 4)


def _fixed(value: float) -> str:
    """A position for a form field: 3 decimals, no trailing zeros."""
    return f"{value:.3f}".rstrip("0").rstrip(".") if abs(value) >= 5e-4 else "0"


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
        fit_to_screen(root, 1200, 760)
        self.vars: dict[str, tk.StringVar] = {k: tk.StringVar(value=v) for k, v in load_settings().items()}
        self.plan: Plan | None = None
        self.result: CamResult | None = None
        self.geometry: PreviewGeometry | None = None
        self.project_path: Path | None = None
        self.preview_values: dict[str, str] | None = None  # form values of the last good Preview
        self.live: Status | None = None  # last status report from the machine, None if not connected
        self._profile_cache: tuple[tuple[str, float], Profile] | None = None
        self.view_zoom = 1.0  # section view: zoom and shift (canvas pixels) on top of the fit
        self.view_shift = (0.0, 0.0)
        self._pan_from = (0, 0)
        self._press_at: tuple[int, int] | None = None
        self._press_towers: tuple[int, ...] = (0, 1)
        self._dragging = False
        self.keys_down: set[str] = set()
        self.move_target: tuple[float, float, tuple[int, ...]] | None = None  # x, z, towers last asked for
        self.channel_handles: dict[tuple[str, str], tuple[float, float]] = {}  # (hole, "entry"/"angle"): canvas
        self._channel_drag: tuple[str, str] | None = None
        self.saved_values: dict[str, str] = {}
        self.step_model: StepModel | None = None
        self._step_pending = False
        self.outlines: tuple[float, float] | None = None  # length, height of the box around both outlines
        self._size_pending = False
        self._syncing = False

        self.status = tk.StringVar(value="Pick the two DXFs, then press Preview.")
        ttk.Label(root, textvariable=self.status, anchor="w", padding=(8, 3), relief="sunken").pack(
            side=tk.BOTTOM, fill=tk.X)
        # the whole window scrolls when it is smaller than the form (high display scaling)
        self.scroll = ScrollArea(root, lambda: (form.winfo_reqwidth() + RIGHT_PANE_MIN, paned.winfo_reqheight()))
        self.scroll.pack(fill=tk.BOTH, expand=True)
        paned = ttk.PanedWindow(self.scroll.body, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        form = ttk.Frame(paned, padding=8)
        right = ttk.Frame(paned)
        paned.add(form, weight=0)
        paned.add(right, weight=1)

        self._build_menu()
        self._build_form(form)
        self.scroll.wheel_scrolls(form)
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
        view_menu = tk.Menu(menubar, tearoff=False)
        size_menu = tk.Menu(view_menu, tearoff=False)
        self.ui_size = tk.DoubleVar(value=parse_ui_size(load_machine_settings().get(UI_SIZE_KEY, 1.0)))
        for size in UI_SIZES:
            size_menu.add_radiobutton(label=f"{size:.0%}", value=size, variable=self.ui_size,
                                      command=self._ui_size_chosen)
        view_menu.add_cascade(label="UI size", menu=size_menu)
        menubar.add_cascade(label="View", menu=view_menu)
        self.root.configure(menu=menubar)
        self.root.bind_all("<Control-n>", lambda _e: self.new_project())
        self.root.bind_all("<Control-o>", lambda _e: self.open_project())
        self.root.bind_all("<Control-s>", lambda _e: self.save_project())
        self.root.bind_all("<Control-S>", lambda _e: self.save_project_as())

    def _ui_size_chosen(self) -> None:
        self.machine.settings[UI_SIZE_KEY] = self.ui_size.get()
        save_machine_settings(self.machine.settings)
        messagebox.showinfo("UI size", f"The UI opens at {self.ui_size.get():.0%} from the next start.",
                            parent=self.root)

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
                self.pick_button = ttk.Button(files, text="Pick faces on the model…", command=self._pick_faces)
                self.pick_button.grid(row=row, column=1, columnspan=2, sticky="w", padx=4, pady=(0, 2))
                row += 1
                self.mode_rows["step"].append(self.pick_button)
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
        ttk.Label(cut, text="Channel exit").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Entry(cut, textvariable=self.vars["channel_exit"], width=10).grid(row=3, column=1, sticky="we", padx=4)
        ttk.Label(cut, text="rapid, cut or mm/min", foreground="#5f6368").grid(row=3, column=2, sticky="w")
        self._number_rows(cut, CUT_FIELDS, first_row=4)

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

    def _pick_faces(self) -> FacePicker | None:
        """Open the 3D face picker on the loaded model (step.py, facepick.py)."""
        if self.step_model is None:
            self._load_step()
        if self.step_model is None:
            self.status.set("Pick faces: choose a STEP model first.")
            return None

        def chosen(text: str) -> int | None:
            return int(text) if text.strip().isdigit() else None

        def done(root: int, tip: int) -> None:
            model = self.step_model
            if model is None:
                return
            for key, tag in (("root_face", root), ("tip_face", tip)):
                self.vars[key].set(str(tag))
                self.face_text[key].set(model.face(tag).label())
            self._measure_span()

        return FacePicker(self.root, self.step_model, chosen(self.vars["root_face"].get()),
                          chosen(self.vars["tip_face"].get()), done)

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

        # Machine tab: the 3D view as a full-height pane on the left (with a wire scrubber),
        # the machine panel on the right; the stock panel is a tab beside Program, Jog and Home
        self.machine_tab = ttk.PanedWindow(tabs, orient=tk.HORIZONTAL)
        tabs.add(self.machine_tab, text="Machine")
        frame3d = ttk.Frame(self.machine_tab)
        self.machine = MachinePanel(self.machine_tab, self._current_program, self._letters, self.status.set,
                                    faces=self._faces, on_status=self._live_status, travel=self._travel)
        self.machine_tab.add(frame3d, weight=2)
        self.machine_tab.add(self.machine, weight=3)
        # the machine panel asks for all the width it can use: give the 3D view its share once
        # the tab has a size (after that, the divider stays where it is dragged)
        self._split_done = False
        self.machine_tab.bind("<Configure>", self._split_machine_tab, add="+")
        view_side = frame3d
        # the scrubber and readout first, at the bottom, so a short pane shrinks only the view
        controls = ttk.Frame(view_side, padding=(6, 4))
        controls.pack(side=tk.BOTTOM, fill=tk.X)
        self._build_readout(view_side)
        self.canvas3d = tk.Canvas(view_side, background="white", highlightthickness=0)
        self.canvas3d.pack(fill=tk.BOTH, expand=True)
        self.view3d = View3D(self.canvas3d)
        self.view3d.on_pick = self._pick_corner
        self.play_text = tk.StringVar(value="Play")
        ttk.Button(controls, textvariable=self.play_text, width=7, command=self.toggle_play).pack(side=tk.LEFT)
        self.wire_pos = tk.DoubleVar(value=0)
        self.scrubber = ttk.Scale(controls, from_=0, to=1, variable=self.wire_pos, command=self._scrub)
        self.scrubber.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.wire_label = tk.StringVar(value="")
        ttk.Label(controls, textvariable=self.wire_label, width=22).pack(side=tk.LEFT)
        ttk.Button(controls, text="Reset view", command=lambda: self.view3d.reset()).pack(side=tk.LEFT)
        self.playing = False
        self.stock_pane = ttk.Frame(self.machine.controls, padding=8)
        self.stock_corner = tk.StringVar(value="front_top")  # not a form value: picking one keeps the preview
        self.stock_wire_text = tk.StringVar(value="")
        self._update_alignment()

        # the section view lives in the Machine tab, beside the console, to watch the live position
        self.section_frame = ttk.Frame(self.machine.views)
        bar = ttk.Frame(self.section_frame, padding=(4, 2))
        bar.pack(fill=tk.X)
        self.section_bar = bar
        self.mouse_moves = tk.BooleanVar(value=False)  # not remembered: a stray click must not move the wire
        ttk.Checkbutton(bar, text="Mouse moves the wire", variable=self.mouse_moves,
                        command=self._mouse_moves_changed).pack(side=tk.LEFT)
        self.edit_channels = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Edit channels", variable=self.edit_channels,
                        command=self._edit_channels_changed).pack(side=tk.LEFT, padx=(10, 0))
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
        body = ttk.Frame(self.section_frame)
        body.pack(fill=tk.BOTH, expand=True)
        self._build_channel_pane(body)
        self.canvas = tk.Canvas(body, background="white", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.kerf = KerfPanel(self.machine.controls, self.machine, profile=self._profile,
                              profile_path=lambda: Path(self.vars["profile"].get().strip()),
                              target=lambda: self.vars["speed"].get(), saved=self._kerf_saved)
        self.machine.controls.add(self.kerf, text="Kerf")
        self.machine.controls.add(self.stock_pane, text="Stock")
        self.cross_cut = CrossCutPanel(self.machine.controls, self.machine, profile=self._profile)
        self.machine.controls.add(self.cross_cut, text="Cut")
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

    def _split_machine_tab(self, event: "tk.Event[ttk.PanedWindow]") -> None:
        if self._split_done or event.width < 200:
            return
        self._split_done = True
        self.machine_tab.sashpos(0, int(event.width * VIEW3D_SHARE))  # type: ignore[no-untyped-call]

    def _update_alignment(self) -> None:
        """Fill the Stock tab (Machine tab controls): where the stock is, its corners, the live wire."""
        frame = self.stock_pane
        for child in frame.winfo_children():
            child.destroy()
        wrap = STOCK_NOTE_WIDTH
        place = ttk.LabelFrame(frame, text="Stock position (G54)", padding=6)
        place.grid(row=0, column=0, sticky="nsw", padx=(0, 8))
        for row, (key, label, _, unit) in enumerate(STOCK_FIELDS):
            ttk.Label(place, text=label).grid(row=2 * row, column=0, sticky="w", pady=(2, 0))
            ttk.Entry(place, textvariable=self.vars[key], width=10).grid(row=2 * row, column=1, sticky="w", padx=4)
            ttk.Label(place, text=unit, foreground="#5f6368").grid(row=2 * row + 1, column=0, columnspan=2, sticky="w")

        corner = ttk.LabelFrame(frame, text="Corner", padding=6)
        corner.grid(row=0, column=1, sticky="nsw", padx=(0, 8))
        for row, (key, (label, _, _)) in enumerate(CORNERS.items()):
            ttk.Radiobutton(corner, text=label, value=key, variable=self.stock_corner,
                            command=self._corner_changed).grid(row=row, column=0, sticky="w")
        ttk.Label(corner, foreground="#5f6368", text="or click a long stock\nedge in the 3D view").grid(
            row=4, column=0, sticky="w", pady=(2, 0))
        self.corner_text = ttk.Label(corner, font=("Consolas", 9), justify="left")
        self.corner_text.grid(row=0, column=1, rowspan=3, sticky="nw", padx=(12, 0))
        buttons = ttk.Frame(corner)
        buttons.grid(row=3, column=1, rowspan=2, sticky="sw", padx=(12, 0))
        self.corner_buttons = [
            ttk.Button(buttons, text="Wire to this corner", command=self._go_to_corner),
            ttk.Button(buttons, text="Copy G-code", command=self._copy_corner),
        ]
        for button in self.corner_buttons:
            button.pack(fill=tk.X, pady=1)

        wire = ttk.LabelFrame(frame, text="Wire now", padding=6)
        wire.grid(row=0, column=2, sticky="nsw", padx=(0, 8))
        ttk.Label(wire, textvariable=self.stock_wire_text, font=("Consolas", 9), justify="left").pack(anchor="w")
        ttk.Button(wire, text="This corner is at the wire", command=self._corner_at_wire).pack(
            anchor="w", pady=(6, 0))

        notes = ttk.Frame(frame)
        notes.grid(row=0, column=3, sticky="nsew")
        frame.columnconfigure(3, weight=1)
        note = ttk.Label(notes, foreground="#5f6368", wraplength=wrap, justify="left", text=(
            "Several parts from one block: jog the cold wire onto a corner of the foam that is left, "
            "pick that corner and press \"This corner is at the wire\". Only this stock is checked: "
            "cut the front (high x, where the wire enters) and the top of the block first. "
            "Positions are the wire's centre."))
        note.pack(anchor="w", fill=tk.X)
        notes.bind("<Configure>", lambda e: note.configure(wraplength=max(150, e.width - 4)))
        self._corner_changed()
        self._show_live_wire()

    def _corner_edge(self) -> AlignEdge | None:
        """The selected corner's long edge in the last preview, or None."""
        if self.geometry is None:
            return None
        key = self.stock_corner.get()
        return next((e for e in alignment_edges(self.geometry) if e.corner == key), None)

    def _corner_changed(self) -> None:
        """Highlight the selected corner in the 3D view and show where it is."""
        edge = self._corner_edge()
        state = ["!disabled"] if edge is not None else ["disabled"]
        for button in self.corner_buttons:
            button.state(state)
        if edge is None:
            self.corner_text.configure(text="Press Preview to see where this corner is.")
            self.view3d.highlight = None
        else:
            l1, l2, l3, l4 = self._letters()
            self.corner_text.configure(text=f"{l1} = {l3} = {edge.x:.3f}\n{l2} = {l4} = {edge.z:.3f}")
            self.view3d.highlight = (edge.x, edge.z, CORNERS[edge.corner][0] + " edge")
        self.view3d.draw()

    def _pick_corner(self, key: str) -> None:
        """A long stock edge was clicked in the 3D view."""
        self.stock_corner.set(key)
        self._corner_changed()

    def _live_towers(self) -> tuple[Point, Point] | None:
        st = self.live
        return None if st is None else tower_positions(st.wpos, self._letters())

    def _show_live_wire(self) -> None:
        """The live wire in the 3D view and in the stock panel."""
        towers = self._live_towers()
        self.view3d.set_live(towers)
        if towers is None:
            self.stock_wire_text.set("not connected: connect on the Machine tab")
            return
        (x1, z1), (x2, z2) = towers
        l1, l2, l3, l4 = self._letters()
        text = f"{l1} {x1:.3f}  {l2} {z1:.3f}\n{l3} {x2:.3f}  {l4} {z2:.3f}"
        if math.hypot(x1 - x2, z1 - z2) > STRAIGHT_WIRE_MM:
            text += "\nthe towers differ: not along a stock edge"
        self.stock_wire_text.set(text)

    def _corner_at_wire(self) -> None:
        """Place the stock so the selected corner is where the wire is now, then preview."""
        towers = self._live_towers()
        if towers is None:
            messagebox.showwarning("No wire position", "Connect on the Machine tab first: the wire "
                                   "position comes from the machine.", parent=self.root)
            return
        (x1, z1), (x2, z2) = towers
        if math.hypot(x1 - x2, z1 - z2) > STRAIGHT_WIRE_MM:
            messagebox.showwarning("Wire not along the edge", (
                f"The towers differ by {x1 - x2:+.2f} mm horizontally and {z1 - z2:+.2f} mm vertically. "
                "Put both towers at the same position, so the wire runs along the stock edge, first."),
                parent=self.root)
            return
        length, height = _number(self.vars["stock_length"].get()), _number(self.vars["stock_height"].get())
        if length is None or height is None or length <= 0 or height <= 0:
            messagebox.showwarning("Stock size", "Enter the stock length and height first.", parent=self.root)
            return
        key = self.stock_corner.get()
        x, z = block_at_corner(key, ((x1 + x2) / 2, (z1 + z2) / 2), length, height)
        profile = self._profile()
        if profile is not None and z < profile.table_z - TABLE_TOLERANCE_MM:
            messagebox.showwarning("Below the table", (
                f"With the {CORNERS[key][0]} corner at the wire, the stock's bottom face would be at "
                f"z = {z:.2f}, below the table (z = {profile.table_z:g}). Pick another corner or "
                "check the stock height."), parent=self.root)
            return
        self.vars["block_x"].set(_fixed(x))
        self.vars["block_z"].set(_fixed(z))
        self.status.set(f"Stock placed with its {CORNERS[key][0]} corner at the wire: "
                        f"back face x = {x:.3f}, bottom face z = {z:.3f}.")
        self.preview()

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
        self._show_live_wire()
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
            self.tabs.select(self.machine_tab)  # type: ignore[no-untyped-call]

    def _profile(self) -> Profile | None:
        """The machine profile in the form (read again when the file changes), or None."""
        path = Path(self.vars["profile"].get().strip())
        try:
            key = (str(path), path.stat().st_mtime)
            if self._profile_cache is None or self._profile_cache[0] != key:
                self._profile_cache = (key, load_profile(path))
        except (OSError, FoamcamError):
            return None
        return self._profile_cache[1]

    def _travel(self) -> dict[str, tuple[float, float]] | None:
        """Each axis letter's (min, max) from the machine profile in the form, or None."""
        profile = self._profile()
        return None if profile is None else {a.letter: (a.min, a.max) for a in profile.axes}

    def _kerf_saved(self, name: str) -> None:
        """A new kerf table was written to the profile: offer it and select it."""
        self._load_kerf_tables()
        self.vars["kerf_table"].set(name)

    def _letters(self) -> tuple[str, str, str, str]:
        """G-code letters of axes 1 to 4: from the preview, else the profile, else X Y Z A."""
        if self.geometry is not None:
            return self.geometry.letters
        profile = self._profile()
        return profile.letters if profile is not None else ("X", "Y", "Z", "A")

    def _go_to_corner(self) -> None:
        edge = self._corner_edge()
        if edge is not None:
            self._go_to_edge(edge)

    def _copy_corner(self) -> None:
        edge = self._corner_edge()
        if edge is not None:
            self._copy_edge(edge)

    def _copy_edge(self, edge: AlignEdge) -> None:
        text = "\n".join(edge.gcode)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.set(f"Copied the G-code for the {edge.name} ({' | '.join(edge.gcode)}). "
                        "Paste it into ioSender's MDI, wire cold.")

    def _build_readout(self, parent: ttk.Frame) -> None:
        """Digital readout of the wire at the slider position, like a machine DRO."""
        dro = tk.Frame(parent, background="#202124", padx=8, pady=4)
        dro.pack(side=tk.BOTTOM, fill=tk.X)
        big = ("Consolas", 12, "bold")  # smaller than the machine's DRO above: this is the preview
        small = ("Consolas", 9)
        self.dro_axes: list[tuple[tk.StringVar, tk.StringVar]] = []
        for i in range(4):
            letter, value = tk.StringVar(value=" "), tk.StringVar(value="   ---.---")
            cell = tk.Frame(dro, background="#202124")
            cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 14))  # 2 x 2: fits a narrow pane
            tk.Label(cell, textvariable=letter, font=big, fg="#8ab4f8", bg="#202124").pack(side=tk.LEFT)
            tk.Label(cell, textvariable=value, font=big, fg="#e8eaed", bg="#202124").pack(side=tk.LEFT)
            self.dro_axes.append((letter, value))
        self.dro_detail = tk.StringVar(value="Press Preview, then scrub or play the cut.")
        tk.Label(dro, textvariable=self.dro_detail, font=small, fg="#bdc1c6", bg="#202124",
                 justify="left", anchor="w").grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

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
        phase = {"entry": "at entry point", "lead_in": "lead-in", "cut": "cutting", "exit": "exit",
                 "channel_exit": "channel exit", "rapid": "channel exit (rapid)"}.get(g.phases[i], g.phases[i])
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

    def preview(self, quiet: bool = False) -> bool:
        """Build the job and run the CAM. Returns True if the cut is valid. `quiet` (after a
        channel edit): on failure keep showing the last good cut and only report in the status."""
        before = (self.plan, self.result, self.geometry, self.preview_values)
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
            if quiet and before[0] is not None:
                self.plan, self.result, self.geometry, self.preview_values = before
                self.status.set("Cannot cut this channel: " + " ".join(str(error).split())[:300]
                                + "  (the view shows the last good cut)")
                self.draw()
                return False
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
            self.tabs.select(self.machine_tab)  # type: ignore[no-untyped-call]
        self.draw()
        self.view3d.show(self.geometry)
        self._update_alignment()
        self.scrubber.configure(to=len(self.geometry.tower_12) - 1)
        self.wire_pos.set(0)
        self._scrub("0")
        self.preview_values = self.values()
        self.machine.refresh()
        save_settings(self.values())
        if self.edit_channels.get():
            self._build_channel_rows()
        self._update_pending()
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
        if self.edit_channels.get():
            self._draw_channels()

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

    # --- section view: drawing the hole channels ---------------------------------------

    def _build_channel_pane(self, body: ttk.Frame) -> None:
        """The pane at the right of the section view while editing channels: one block
        per hole with its own settings, and the Cut button under them."""
        pane = ttk.Frame(body, padding=(6, 4), width=CHANNEL_PANE_WIDTH)
        self.channel_pane = pane
        ttk.Label(pane, text="Hole channels", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(pane, foreground="#5f6368", wraplength=CHANNEL_PANE_WIDTH - 20, justify="left", text=(
            "Drag the round handle along the hole and the square one to turn the channel. A channel may "
            "end in another hole that has its own way out. Nothing is cut until you press Cut.")).pack(
            anchor="w", pady=(0, 4))
        bottom = ttk.Frame(pane)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        ttk.Button(bottom, text="Cut", command=self._cut_channels).pack(side=tk.LEFT)
        self.ch_pending = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.ch_pending, foreground="#e37400", wraplength=CHANNEL_PANE_WIDTH - 70,
                  justify="left").pack(side=tk.LEFT, padx=(8, 0))
        # a scrolling list of hole blocks
        holder = ttk.Frame(pane)
        holder.pack(fill=tk.BOTH, expand=True)
        self.ch_scroll = tk.Canvas(holder, highlightthickness=0, width=CHANNEL_PANE_WIDTH - 30)
        bar = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=self.ch_scroll.yview)
        self.ch_scroll.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.ch_scroll.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.ch_list = ttk.Frame(self.ch_scroll)
        window = self.ch_scroll.create_window(0, 0, anchor="nw", window=self.ch_list)
        self.ch_list.bind("<Configure>", lambda _e: self.ch_scroll.configure(
            scrollregion=self.ch_scroll.bbox("all")))
        self.ch_scroll.bind("<Configure>", lambda e: self.ch_scroll.itemconfigure(window, width=e.width))
        self.ch_selected = tk.StringVar(value="")
        self.ch_rows: dict[str, ChannelRow] = {}
        self.ch_found: dict[str, ChannelPreview] = {}

    def _build_channel_rows(self) -> None:
        """One block per hole of the cut on screen (rebuilt when the holes change)."""
        names = [p.name for p in self._channel_pairs()]
        if list(self.ch_rows) == names:
            for name in names:
                self._show_channel_spec(name, self._channel_specs().get(name, ChannelSpec()))
            return
        for child in self.ch_list.winfo_children():
            child.destroy()
        self.ch_rows = {}
        if not names:
            ttk.Label(self.ch_list, text="Press Preview first: the drawings have no holes yet.",
                      foreground="#5f6368", wraplength=CHANNEL_PANE_WIDTH - 40).pack(anchor="w")
        for name in names:
            self.ch_rows[name] = self._channel_row(name)
            self._show_channel_spec(name, self._channel_specs().get(name, ChannelSpec()))
        if self.ch_selected.get() not in names:
            self.ch_selected.set(names[0] if names else "")
        self._mark_selected()

    def _channel_row(self, name: str) -> "ChannelRow":
        block = tk.LabelFrame(self.ch_list, text=name, padx=6, pady=4, font=("Segoe UI", 9, "bold"))
        block.pack(fill=tk.X, pady=(0, 6), padx=(0, 4))
        block.bind("<Button-1>", lambda _e: self._select_hole(name))
        row = ChannelRow(block, {k: tk.StringVar(value=v) for k, v in (
            ("shape", "auto"), ("angle", "90"), ("offset", "0"), ("entry", "90"), ("radius", "5"))}, {},
            tk.StringVar(value=""))
        shapes = ttk.Frame(block)
        shapes.grid(row=0, column=0, columnspan=4, sticky="w")
        for shape in SHAPES:
            ttk.Radiobutton(shapes, text=shape.capitalize(), value=shape, variable=row.vars["shape"],
                            command=lambda: self._channel_shape_changed(name)).pack(side=tk.LEFT, padx=(0, 8))
        for k, (key, label, lo, hi, step) in enumerate((
                ("angle", "Angle °", -360, 360, 5), ("offset", "Offset mm", -100, 100, 0.5),
                ("entry", "Entry °", -360, 360, 5), ("radius", "Bend mm", 0.5, 200, 0.5))):
            ttk.Label(block, text=label).grid(row=1 + k // 2, column=2 * (k % 2), sticky="w", pady=1)
            spin = ttk.Spinbox(block, textvariable=row.vars[key], from_=lo, to=hi, increment=step, width=6,
                               command=lambda: self._channel_fields_changed(name))
            spin.grid(row=1 + k // 2, column=2 * (k % 2) + 1, sticky="w", padx=(2, 8), pady=1)
            spin.bind("<Return>", lambda _e: self._channel_fields_changed(name))
            spin.bind("<FocusOut>", lambda _e: self._channel_fields_changed(name))
            spin.bind("<FocusIn>", lambda _e: self._select_hole(name))
            row.spins[key] = spin
        ttk.Label(block, textvariable=row.note, foreground="#5f6368", wraplength=CHANNEL_PANE_WIDTH - 60,
                  justify="left").grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Button(block, text="Reset", width=6, command=lambda: self._reset_channel(name)).grid(
            row=3, column=3, sticky="e")
        return row

    def _select_hole(self, name: str) -> None:
        if self.ch_selected.get() != name:
            self.ch_selected.set(name)
            self._mark_selected()
            self._see_row(name)
            self.draw()

    def _see_row(self, name: str) -> None:
        """Scroll the pane so the hole's block is in view."""
        row = self.ch_rows.get(name)
        if row is None:
            return
        self.ch_list.update_idletasks()
        total = max(self.ch_list.winfo_height(), 1)
        top, bottom = self.ch_scroll.yview()
        y0, y1 = row.block.winfo_y() / total, (row.block.winfo_y() + row.block.winfo_height()) / total
        if y0 < top:
            self.ch_scroll.yview_moveto(y0)
        elif y1 > bottom:
            self.ch_scroll.yview_moveto(max(0.0, y1 - (bottom - top)))

    def _mark_selected(self) -> None:
        for name, row in self.ch_rows.items():
            mine = name == self.ch_selected.get()
            row.block.configure(foreground="#8e24aa" if mine else "#202124",
                                highlightbackground="#8e24aa", highlightthickness=2 if mine else 0)

    def _channel_pairs(self) -> tuple[HolePair, ...]:
        return self.plan.generated.holes if self.plan is not None else ()

    def _channel_specs(self) -> dict[str, ChannelSpec]:
        try:
            return specs_from_text(self.vars["channels"].get())
        except DxfError:
            return {}

    def _edit_channels_changed(self) -> None:
        if self.edit_channels.get():
            self.mouse_moves.set(False)
            self.channel_pane.pack(side=tk.RIGHT, fill=tk.Y, before=self.canvas)
            self._build_channel_rows()
            self._update_pending()
            self.status.set("Edit channels: set each hole's channel in the pane at the right, or drag its "
                            "handles; then press Cut.")
        else:
            self.channel_pane.pack_forget()
        self.draw()

    def _channel_spec(self, name: str) -> ChannelSpec:
        """The spec in a hole's fields (the stored one where a field is not a number)."""
        row = self.ch_rows[name]
        old = self._channel_specs().get(name, ChannelSpec())
        values = {}
        for key in ("angle", "offset", "entry", "radius"):
            number = _number(row.vars[key].get())
            values[key] = getattr(old, key) if number is None else number
        return ChannelSpec(row.vars["shape"].get(), values["angle"], values["offset"], values["entry"],
                           max(values["radius"], 0.1))

    def _show_channel_spec(self, name: str, spec: ChannelSpec) -> None:
        row = self.ch_rows.get(name)
        if row is None:
            return
        row.vars["shape"].set(spec.shape)
        for key in ("angle", "offset", "entry", "radius"):
            row.vars[key].set(f"{getattr(spec, key):g}")
        for key, spin in row.spins.items():
            used = (spec.shape == "straight" and key in ("angle", "offset")) or (
                spec.shape == "curved" and key in ("angle", "entry", "radius"))
            spin.state(["!disabled"] if used else ["disabled"])

    def _channel_shape_changed(self, name: str) -> None:
        """A new shape starts from where the automatic channel was: up or down out of the hole."""
        self._select_hole(name)
        shape = self.ch_rows[name].vars["shape"].get()
        old = self._channel_specs().get(name, ChannelSpec())
        pair = next((p for p in self._channel_pairs() if p.name == name), None)
        up = pair is None or pair.slit == "upper"
        if old.shape == "auto" and shape == "straight":
            spec = ChannelSpec("straight", 90.0 if up else 270.0, 0.0)
        elif old.shape == "auto" and shape == "curved":
            spec = ChannelSpec("curved", 90.0 if up else 270.0, 0.0, 45.0 if up else 315.0, 5.0)
        else:
            spec = ChannelSpec(shape, old.angle, old.offset, old.entry, old.radius)
        self._store_channel(name, spec)

    def _channel_fields_changed(self, name: str) -> None:
        if name not in self.ch_rows or self.ch_rows[name].vars["shape"].get() == "auto":
            return
        spec = self._channel_spec(name)
        if spec != self._channel_specs().get(name):
            self._store_channel(name, spec)

    def _reset_channel(self, name: str) -> None:
        self._select_hole(name)
        self._store_channel(name, ChannelSpec())

    def _store_channel(self, name: str, spec: ChannelSpec) -> None:
        """Keep an edited channel and draw it; the cut is only worked out again by Cut."""
        if not name:
            return
        specs = self._channel_specs()
        specs[name] = spec
        self.vars["channels"].set(specs_to_text(specs))
        self._show_channel_spec(name, spec)
        self._update_pending()
        self.draw()

    def _channels_pending(self) -> bool:
        """True if the channels being edited differ from those of the cut on screen."""
        return self.plan is not None and self._channel_specs() != self.plan.request.settings.channels

    def _update_pending(self) -> None:
        self.ch_pending.set("changed, not cut yet: press Cut" if self._channels_pending() else "")

    def _cut_channels(self) -> None:
        """Cut button: build the job and run the CAM with the edited channels."""
        if self.plan is not None:
            self.preview(quiet=True)
        self._update_pending()

    def _shifted(self, points: "list[Point] | tuple[Point, ...]") -> list[Point]:
        assert self.plan is not None
        sx, sz = self.plan.generated.shift
        return [(x + sx, z + sz) for x, z in points]

    def _draw_channels(self) -> None:
        """Each drawn channel at the root and tip faces, with its two handles on the root one."""
        c, plan = self.canvas, self.plan
        self.channel_handles = {}
        if plan is None or not plan.generated.holes:
            return
        found = preview_channels(plan.generated.holes, self._channel_specs(), plan.generated.outers,
                                 plan.request.settings.max_segment_length)
        self.ch_found = found
        for name, row in self.ch_rows.items():
            preview = found.get(name)
            if preview is None:
                row.note.set("automatic: straight up or down")
            elif preview.problem:
                row.note.set("cannot cut: " + preview.problem)
            elif preview.root is not None and preview.root.into:
                row.note.set(f"ends in {preview.root.into}")
            else:
                row.note.set("ends at the outer contour")
        for pair in plan.generated.holes:
            selected = pair.name == self.ch_selected.get()
            cx, cy = self._to_canvas(self._shifted([area_centre(list(pair.root_drawn))]))
            c.create_text(cx, cy, text=pair.name, fill="#8e24aa" if selected else "#5f6368",
                          font=("Segoe UI", 8, "bold" if selected else "normal"))
            preview = found.get(pair.name)
            if preview is None:
                continue
            if preview.root is None or preview.tip is None:
                continue
            for path, colour in ((preview.tip, COLOURS["tip"]), (preview.root, COLOURS["root"])):
                pts = self._shifted([path.entry, *path.points])
                c.create_line(*self._to_canvas(pts), fill="#d93025" if preview.problem else colour,
                              width=3 if selected else 2, dash=(6, 3))
            ex, ey = self._to_canvas(self._shifted([preview.root.entry]))
            ax, ay = self._to_canvas(self._shifted([preview.root.points[-1]]))
            edge = "#d93025" if preview.problem else "#8e24aa"
            c.create_oval(ex - 6, ey - 6, ex + 6, ey + 6, fill="white", outline=edge, width=2)
            c.create_rectangle(ax - 6, ay - 6, ax + 6, ay + 6, fill="white", outline=edge, width=2)
            self.channel_handles[(pair.name, "entry")] = (ex, ey)
            self.channel_handles[(pair.name, "angle")] = (ax, ay)

    def _channel_press(self, event: "tk.Event[tk.Canvas]") -> None:
        for key, (hx, hy) in self.channel_handles.items():
            if (event.x - hx) ** 2 + (event.y - hy) ** 2 <= HANDLE_PIXELS ** 2:
                self._channel_drag = key
                self._select_hole(key[0])
                return
        p = self._to_world(event.x, event.y)
        if p is None:
            return
        for pair in self._channel_pairs():
            if any(inside(p, self._shifted(loop)) for loop in (pair.root_drawn, pair.tip_drawn)):
                self._select_hole(pair.name)
                return

    def _channel_move(self, event: "tk.Event[tk.Canvas]") -> None:
        """Drag a handle: the square turns the channel, the round one slides its entry."""
        if self._channel_drag is None:
            return
        name, handle = self._channel_drag
        pair = next((q for q in self._channel_pairs() if q.name == name), None)
        p = self._to_world(event.x, event.y)
        spec = self._channel_specs().get(name)
        if pair is None or p is None or spec is None:
            return
        cx, cz = self._shifted([area_centre(list(pair.root_drawn))])[0]
        towards = math.degrees(math.atan2(p[1] - cz, p[0] - cx)) % 360
        if handle == "angle":
            spec = replace(spec, angle=round(towards))
        elif spec.shape == "straight":
            d = (math.cos(math.radians(spec.angle)), math.sin(math.radians(spec.angle)))
            spec = replace(spec, offset=round(d[0] * (p[1] - cz) - d[1] * (p[0] - cx), 1))  # + to the left
        else:
            spec = replace(spec, entry=round(towards))
        self._store_channel(name, spec)

    def _channel_release(self) -> None:
        self._channel_drag = None

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
        if self.mouse_moves.get() and self.edit_channels.get():
            self.edit_channels.set(False)
            self._edit_channels_changed()
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
        if self.edit_channels.get():
            self._channel_press(event)
            return
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
        if self.edit_channels.get():
            self._channel_move(event)
            return
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
        if self.edit_channels.get():
            self._channel_release()
            return
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
    parser = argparse.ArgumentParser(prog="foamdxf-gui", description="Foam cutter DXF slicer")
    parser.add_argument("project", nargs="?", type=Path, help="project file to open")
    parser.add_argument("--ui-size", help="UI size, e.g. 0.7 or 70%% (default: View > UI size)")
    args = parser.parse_args(argv)
    root = tk.Tk()
    size = args.ui_size if args.ui_size is not None else load_machine_settings().get(UI_SIZE_KEY, 1.0)
    apply_ui_size(root, parse_ui_size(size))
    window = SlicerWindow(root)
    if args.project is not None:
        project = args.project
        root.after(100, lambda: window.open_project(project))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
