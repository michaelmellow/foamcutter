"""Desktop window for the DXF front end: python -m foamdxf --gui [PROJECT.foamproj]

Pick the root and tip DXFs and the machine profile, enter the span and stock
size, press Preview to see the cut, then Generate to write the job, G-code,
report and SVG. File > Save project keeps every value and a copy of each input
file in one .foamproj file (see project.py). Uses Tkinter only (part of Python), so it needs no extra
packages. The last values are remembered in ~/.foamdxf-gui.json.
"""

import functools
import json
import math
import os
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
from .jobgen import CutSettings, Placement
from .outline import Point
from .pipeline import (
    AlignEdge,
    Plan,
    PreviewGeometry,
    Request,
    alignment_edges,
    make_job,
    preview_geometry,
    run_cam,
    write_files,
)
from .project import SUFFIX
from .project import open_project as read_project
from .project import save_project as write_project
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
    ("stock_height", "Stock height", "40", "mm"),
]
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

    labels = {key: label for key, label, _, _ in FILE_FIELDS + PART_FIELDS + CUT_FIELDS}
    paths = {key: Path(text(key, labels[key])) for key in ("root", "tip", "profile")}
    for key, path in paths.items():
        if not path.is_file():
            raise FormError(f"{labels[key]}: file not found ({path})")
    placement = Placement(
        span=req("span", labels["span"]),
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
    )
    return Request(
        root=paths["root"],
        tip=paths["tip"],
        profile=paths["profile"],
        placement=placement,
        settings=settings,
        name=values.get("name", "").strip() or None,
        simplify=req("simplify", labels["simplify"]),
    )


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
        self.saved_values: dict[str, str] = {}

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
        self.saved_values = self.values()
        for var in self.vars.values():
            var.trace_add("write", lambda *_: self._update_title())
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
        for key, var in self.vars.items():
            var.set(values.get(key, DEFAULTS.get(key, "")))
        self._load_kerf_tables()  # a blank or unknown kerf table becomes the profile's first
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
        stem = Path(self.vars["root"].get()).stem or "project"
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
        for row, (key, label, _, kind) in enumerate(FILE_FIELDS):
            ttk.Label(files, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(files, textvariable=self.vars[key], width=34).grid(row=row, column=1, sticky="we", padx=4)
            ttk.Button(files, text="…", width=3, command=self._browse(key, kind)).grid(row=row, column=2)
        files.columnconfigure(1, weight=1)

        part = ttk.LabelFrame(form, text="Part and stock", padding=6)
        part.pack(fill=tk.X, pady=(8, 0))
        self._number_rows(part, PART_FIELDS)

        cut = ttk.LabelFrame(form, text="Cut", padding=6)
        cut.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(cut, text="Kerf table").grid(row=0, column=0, sticky="w", pady=2)
        self.kerf_box = ttk.Combobox(cut, textvariable=self.vars["kerf_table"], state="readonly", width=22)
        self.kerf_box.grid(row=0, column=1, columnspan=2, sticky="we", padx=4)
        ttk.Label(cut, text="Direction").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Combobox(cut, textvariable=self.vars["direction"], state="readonly", width=22,
                     values=("upper_first", "lower_first")).grid(row=1, column=1, columnspan=2, sticky="we", padx=4)
        self._number_rows(cut, CUT_FIELDS, first_row=2)

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
            ttk.Entry(parent, textvariable=self.vars[key], width=10).grid(row=row, column=1, sticky="w", padx=4)
            ttk.Label(parent, text=unit, foreground="#5f6368").grid(row=row, column=2, sticky="w")

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

        self.canvas = tk.Canvas(tabs, background="white", highlightthickness=0)
        tabs.add(self.canvas, text="Section")
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
        ttk.Label(frame, text=intro, justify="left").grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 12))
        if g is None:
            ttk.Label(frame, text="Press Preview first.", foreground="#5f6368").grid(row=1, column=0, sticky="w")
            return
        l1, l2, l3, l4 = g.letters
        headers = ("Edge", f"{l1} = {l3}", f"{l2} = {l4}", "", "")
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
        self.align_text = tk.Text(frame, height=4, width=60, font=("Consolas", 10))
        self.align_text.grid(row=7, column=0, columnspan=5, sticky="we", pady=(12, 0))
        self.align_text.insert("1.0", "Copy G-code puts the three lines for that edge here and on the clipboard.")

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
                types = [("DXF drawing", "*.dxf")] if kind == "dxf" else [("Machine profile", "*.yaml")]
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

    def _transform(self) -> tuple[float, float, float, float] | None:
        """(scale, x offset, y offset, world y max) to fit the travel area in the canvas."""
        g = self.geometry
        if g is None:
            return None
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 200 or h < 200:  # not laid out yet
            w, h = max(w, 800), max(h, 600)
        x0, z0, x1, z1 = g.travel
        margin = 40
        scale = min((w - 2 * margin) / max(x1 - x0, 1e-6), (h - 2 * margin - 60) / max(z1 - z0, 1e-6))
        ox = (w - scale * (x1 - x0)) / 2 - scale * x0
        oy = (h - scale * (z1 - z0)) / 2 + 30 + scale * z1
        return scale, ox, oy, z1

    def _to_canvas(self, points: list[Point]) -> list[float]:
        t = self._transform()
        assert t is not None
        scale, ox, oy, _ = t
        out: list[float] = []
        for x, z in points:
            out += [ox + scale * x, oy - scale * z]
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
        for target in (g.root_target, g.tip_target):
            c.create_line(*self._to_canvas(target + target[:1]), fill=COLOURS["target"], width=1)

        # axis labels at the travel corners
        bl = self._to_canvas([(x0, z0)])
        tr = self._to_canvas([(x1, z1)])
        c.create_text(bl[0], bl[1] + 12, text=f"x {x0:g}", anchor="w", fill="#5f6368")
        c.create_text(tr[0], bl[1] + 12, text=f"x {x1:g}  (+x away from operator)", anchor="e", fill="#5f6368")
        c.create_text(bl[0] - 6, tr[1], text=f"z {z1:g}", anchor="e", fill="#5f6368")
        c.create_text(bl[0] - 6, bl[1], text=f"z {z0:g}", anchor="e", fill="#5f6368")

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
            y = 14 + 16 * i
            c.create_line(12, y, 40, y, fill=colour, width=width, dash=dash or ())
            c.create_text(46, y, text=label, anchor="w", fill="#202124")

    def _show_position(self, event: "tk.Event[tk.Canvas]") -> None:
        t = self._transform()
        if t is None:
            return
        scale, ox, oy, _ = t
        x, z = (event.x - ox) / scale, (oy - event.y) / scale
        if self.result is not None:
            self.status.set(f"x {x:7.1f}  z {z:7.1f} mm      cut time {self.result.total_time:.2f} min")


def main(argv: list[str] | None = None) -> int:
    root = tk.Tk()
    window = SlicerWindow(root)
    if argv:
        root.after(100, lambda: window.open_project(Path(argv[0])))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
