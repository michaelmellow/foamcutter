"""Picking the root and tip faces of a STEP model on a 3D view (step.py).

The face list in the form names faces by number, size and position, which is
easy to get wrong on a model with many flat faces. This window draws the model
(its edges in grey, every flat face filled and numbered) and lets the operator
click the root and the tip face:

  click a face   set it as the root or the tip (as chosen above the view;
                 after the root, the next click sets the tip)
  left drag      rotate            right drag   pan
  wheel          zoom              double-click reset the view

The view uses the model's own axes (z up), with the 3D preview's camera.
"""

import itertools
import math
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from .errors import DxfError
from .step import StepFace, StepModel, Vec, sections
from .view3d import Camera

CLICK_PIXELS = 4  # a press and release closer than this is a click, not a drag
NEAR_PIXELS = 8  # a click this close to a face's edge picks it when it hits no face
LOOP_TOL = 0.01  # mm: edge ends closer than this belong to the same loop

COLOURS = {
    "wire": "#9aa0a6",
    "face": "#dfe3e8",
    "edge": "#5f6368",
    "root": "#1f6fb4",
    "root_fill": "#bcd4ea",
    "tip": "#e8710a",
    "tip_fill": "#f8d3b0",
    "hover": "#8e24aa",
    "hover_fill": "#e8d2ee",
    "text": "#202124",
    "muted": "#5f6368",
}


def face_loops(face: StepFace) -> list[list[Vec]]:
    """The face's boundary edges joined end to end into closed loops, longest first."""
    todo = [list(e) for e in face.edges if len(e) >= 2]
    loops: list[list[Vec]] = []
    while todo:
        loop = todo.pop(0)
        grown = True
        while grown and math.dist(loop[0], loop[-1]) > LOOP_TOL:
            grown = False
            for k, e in enumerate(todo):
                if math.dist(loop[-1], e[0]) <= LOOP_TOL:
                    loop += e[1:]
                elif math.dist(loop[-1], e[-1]) <= LOOP_TOL:
                    loop += e[-2::-1]
                elif math.dist(loop[0], e[-1]) <= LOOP_TOL:
                    loop = e[:-1] + loop
                elif math.dist(loop[0], e[0]) <= LOOP_TOL:
                    loop = e[:0:-1] + loop
                else:
                    continue
                todo.pop(k)
                grown = True
                break
        loops.append(loop)
    loops.sort(key=lambda lp: -sum(math.dist(a, b) for a, b in itertools.pairwise(lp)))
    return loops


def inside_2d(p: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    """Even-odd test of a screen point against a projected loop."""
    x, y = p
    hit = False
    n = len(polygon)
    for i in range(n):
        (xa, ya), (xb, yb) = polygon[i], polygon[(i + 1) % n]
        if (ya > y) != (yb > y) and x < xa + (y - ya) * (xb - xa) / (yb - ya):
            hit = not hit
    return hit


def segment_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    ex, ey = b[0] - a[0], b[1] - a[1]
    length2 = ex * ex + ey * ey
    f = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ex + (p[1] - a[1]) * ey) / length2))
    return math.dist(p, (a[0] + f * ex, a[1] + f * ey))


class FacePicker:
    """A window showing `model`; `on_done(root, tip)` gets the two face numbers on OK."""

    def __init__(self, master: tk.Misc, model: StepModel, root: int | None, tip: int | None,
                 on_done: Callable[[int, int], None]) -> None:
        self.model = model
        self.on_done = on_done
        self.loops = {f.tag: face_loops(f) for f in model.faces}
        self.picked: dict[str, int | None] = {"root": root, "tip": tip}
        self.hover: int | None = None
        self.camera = Camera(yaw=35.0, pitch=25.0)
        self._drag: tuple[int, int] | None = None
        self._moved = False
        self.screen: dict[int, list[tuple[float, float]]] = {}  # face tag -> projected outer loop
        self.depth: dict[int, float] = {}

        win = self.window = tk.Toplevel(master)
        win.title(f"Pick the root and tip faces: {model.path.name}")
        win.geometry("900x680")
        win.transient(master.winfo_toplevel())
        top = ttk.Frame(win, padding=(8, 6))
        top.pack(fill=tk.X)
        ttk.Label(top, text="Click sets the").pack(side=tk.LEFT)
        self.mode = tk.StringVar(value="root" if root is None or tip is not None else "tip")
        for value, text in (("root", "root face"), ("tip", "tip face")):
            ttk.Radiobutton(top, text=text, value=value, variable=self.mode).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(top, text="Suggest", command=self.suggest).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Button(top, text="Swap", command=self.swap).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top, text="Cancel", command=win.destroy).pack(side=tk.RIGHT)
        self.ok = ttk.Button(top, text="OK", command=self.accept)
        self.ok.pack(side=tk.RIGHT, padx=(0, 4))

        self.canvas = tk.Canvas(win, background="white", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.info = tk.StringVar()
        ttk.Label(win, textvariable=self.info, padding=(8, 4), wraplength=860, justify="left").pack(fill=tk.X)
        c = self.canvas
        c.bind("<Configure>", lambda _e: self.draw())
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._rotate)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<ButtonPress-3>", self._press)
        c.bind("<B3-Motion>", self._pan)
        c.bind("<Motion>", self._motion)
        c.bind("<MouseWheel>", lambda e: self._zoom(1.15 if e.delta > 0 else 1 / 1.15))
        c.bind("<Button-4>", lambda _e: self._zoom(1.15))
        c.bind("<Button-5>", lambda _e: self._zoom(1 / 1.15))
        c.bind("<Double-Button-1>", lambda _e: self._reset())
        win.bind("<Return>", lambda _e: self.accept())
        win.bind("<Escape>", lambda _e: win.destroy())
        self.update_info()

    # --- choosing ---------------------------------------------------------------------------

    def set_face(self, tag: int) -> None:
        """A click on face `tag`: set it as the root or tip, as the mode says."""
        which = self.mode.get()
        self.picked[which] = tag
        if which == "root":
            self.mode.set("tip")
        self.update_info()
        self.draw()

    def suggest(self) -> None:
        pair = self.model.suggest()
        if pair is not None:
            self.picked = {"root": pair[0], "tip": pair[1]}
            self.update_info()
            self.draw()

    def swap(self) -> None:
        self.picked = {"root": self.picked["tip"], "tip": self.picked["root"]}
        self.update_info()
        self.draw()

    def accept(self) -> None:
        root, tip = self.picked["root"], self.picked["tip"]
        if root is None or tip is None or root == tip:
            return
        self.on_done(root, tip)
        self.window.destroy()

    def update_info(self) -> None:
        root, tip = self.picked["root"], self.picked["tip"]
        lines = [f"Root: {self.model.face(root).label() if root is not None else 'not picked'}",
                 f"Tip: {self.model.face(tip).label() if tip is not None else 'not picked'}"]
        ok = False
        if root is not None and tip is not None:
            try:
                cut = sections(self.model, root, tip, 0.0)
                lines.append(f"Span {cut.span:.3f} mm, chords {cut.root.chord:.1f} (root) and "
                             f"{cut.tip.chord:.1f} mm (tip).")
                lines += [w.split(": ", 1)[-1] for w in cut.warnings]  # without the file name
                ok = True
            except DxfError as error:
                lines.append("Cannot cut between these faces: " + str(error))
        self.info.set("\n".join(lines))
        self.ok.state(["!disabled"] if ok else ["disabled"])

    # --- drawing ----------------------------------------------------------------------------

    def _frame(self) -> tuple[Vec, float, int, int]:
        lo, hi = self.model.bbox
        centre = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        w, h = (w, h) if w > 50 and h > 50 else (900, 600)
        return centre, 0.85 * min(w, h) / max(math.dist(lo, hi), 1e-6), w, h

    def project(self, p: Vec) -> tuple[float, float]:
        centre, scale, w, h = self._frame()
        return self.camera.project(p, centre, scale, w, h)

    def _depth(self, p: Vec) -> float:
        """Distance into the screen (larger is farther), matching Camera.project."""
        a, b = math.radians(self.camera.yaw), math.radians(self.camera.pitch)
        y1 = p[0] * math.sin(a) + p[1] * math.cos(a)
        return y1 * math.cos(b) - p[2] * math.sin(b)

    def draw(self) -> None:
        c = self.canvas
        c.delete("all")
        for edge in self.model.wire:
            flat = [v for p in edge for v in self.project(p)]
            if len(flat) >= 4:
                c.create_line(*flat, fill=COLOURS["wire"])
        self.screen = {}
        self.depth = {f.tag: self._depth(f.centre) for f in self.model.faces}
        for face in sorted(self.model.faces, key=lambda f: -self.depth[f.tag]):  # farthest first
            loops = self.loops[face.tag]
            if not loops:
                continue
            role = ("root" if face.tag == self.picked["root"] else "tip" if face.tag == self.picked["tip"]
                    else "hover" if face.tag == self.hover else "")
            fill = COLOURS[f"{role}_fill"] if role else COLOURS["face"]
            line = COLOURS[role] if role else COLOURS["edge"]
            outer = [self.project(p) for p in loops[0]]
            self.screen[face.tag] = outer
            c.create_polygon(*[v for p in outer for v in p], fill=fill, outline=line, width=2 if role else 1,
                             stipple="" if role else "gray50")
            for hole in loops[1:]:
                c.create_line(*[v for p in hole for v in self.project(p)], fill=line, width=2 if role else 1)
            x, y = self.project(face.centre)
            text = f"{face.tag}" + (" ROOT" if role == "root" else " TIP" if role == "tip" else "")
            c.create_text(x, y, text=text, fill=line if role else COLOURS["text"],
                          font=("Segoe UI", 9, "bold" if role else "normal"))
        c.create_text(10, 10, anchor="nw", fill=COLOURS["muted"],
                      text="click a face: set it   drag: rotate   right-drag: pan   wheel: zoom   "
                           "double-click: reset view")

    # --- mouse ------------------------------------------------------------------------------

    def face_at(self, x: float, y: float) -> int | None:
        """The front-most face under a screen point, else the face with an edge nearest to it."""
        hits = [tag for tag, outer in self.screen.items() if len(outer) >= 3 and inside_2d((x, y), outer)]
        if hits:
            return min(hits, key=lambda tag: self.depth[tag])
        best: tuple[float, int] | None = None
        for tag, outer in self.screen.items():
            for a, b in zip(outer, outer[1:] + outer[:1]):
                d = segment_distance((x, y), a, b)
                if d <= NEAR_PIXELS and (best is None or d < best[0]):
                    best = (d, tag)
        return None if best is None else best[1]

    def click(self, x: float, y: float) -> None:
        tag = self.face_at(x, y)
        if tag is not None:
            self.set_face(tag)

    def _press(self, event: "tk.Event[tk.Canvas]") -> None:
        self._drag = (event.x, event.y)
        self._start = (event.x, event.y)
        self._moved = False

    def _rotate(self, event: "tk.Event[tk.Canvas]") -> None:
        if self._drag is None:
            return
        if math.dist((event.x, event.y), self._start) > CLICK_PIXELS:
            self._moved = True
        dx, dy = event.x - self._drag[0], event.y - self._drag[1]
        self._drag = (event.x, event.y)
        if self._moved:
            self.camera.yaw = (self.camera.yaw - 0.4 * dx) % 360
            self.camera.pitch = max(-89.0, min(89.0, self.camera.pitch + 0.4 * dy))
            self.draw()

    def _release(self, event: "tk.Event[tk.Canvas]") -> None:
        if self._drag is not None and not self._moved:
            self.click(event.x, event.y)
        self._drag = None

    def _pan(self, event: "tk.Event[tk.Canvas]") -> None:
        if self._drag is None:
            return
        self.camera.pan_x += event.x - self._drag[0]
        self.camera.pan_y += event.y - self._drag[1]
        self._drag = (event.x, event.y)
        self.draw()

    def _motion(self, event: "tk.Event[tk.Canvas]") -> None:
        tag = self.face_at(event.x, event.y)
        if tag != self.hover:
            self.hover = tag
            self.draw()

    def _zoom(self, factor: float) -> None:
        self.camera.zoom = max(0.2, min(20.0, self.camera.zoom * factor))
        self.draw()

    def _reset(self) -> None:
        self.camera = Camera(yaw=35.0, pitch=25.0)
        self.draw()
