"""The Sync check tab: which part of the root path is cut together with which part of the tip path.

The CAM cuts the root and the tip in lockstep: path point k at the root foam face and path
point k at the tip face are reached at the same moment (job-file.md §10.3). This view draws
both wire paths (kerf compensated, at the foam faces) on top of each other in the section
frame and splits the outline cut into **sections** at every sharp corner of either path
(a notch wall is one section, a smooth curve between corners is one). A section is a run
of moves, so it has a root half and a tip half, cut at the same time; both get the same
colour. Where a feature has a different colour at the root than at the tip, for example a
notch, the two are not cut at the same time and the wire smears between them.

A click selects a section: both halves are outlined, and dashed lines join their start
points and their end points (the wire at the start and the end of the section). The
arrow keys step the selection along the path. "Links" draws the wire at every section
boundary, so with good sync the links join matching corners.
"""

import math
import tkinter as tk
from tkinter import ttk

from .pipeline import PreviewGeometry

Point = tuple[float, float]

# sections cycle through these; neighbours always differ (Tableau 10 without grey)
PALETTE = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
           "#8c564b", "#e377c2", "#bcbd22", "#17becf")
OTHER_COLOUR = "#c4c7c5"  # lead-in, exit, rapids: not part of the outline
TARGET_COLOUR = "#9aa0a6"
LINK_COLOUR = "#80868b"
ROOT_WIDTH, TIP_WIDTH = 4, 2
PICK_PIXELS = 10
CORNER_DEG = 30.0  # a turn of more than this between two moves is a corner
MARGIN = 30
STACK_GAP = 0.6  # space between root and tip, as a share of the taller outline
ZOOM_STEP = 1.2


def cut_moves(g: PreviewGeometry) -> list[int]:
    """Indices k of the moves (path point k−1 to k) that cut the outline."""
    return [k for k in range(1, len(g.phases)) if g.phases[k] == "cut"]


def turn(a: Point, b: Point, c: Point) -> float:
    """Degrees the path turns at b, going a → b → c (0 if a move has no length)."""
    u, v = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
    if u == (0, 0) or v == (0, 0):
        return 0.0
    return abs(math.degrees(math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])))


def sections(g: PreviewGeometry) -> list[list[int]]:
    """The outline cut in sections: runs of consecutive cutting moves, split where either
    path turns by more than CORNER_DEG and where the cut is interrupted (a channel)."""
    out: list[list[int]] = []
    previous: int | None = None
    for k in cut_moves(g):
        corner = previous is None or previous != k - 1 or any(
            turn(path[k - 2], path[k - 1], path[k]) > CORNER_DEG for path in (g.root_cut, g.tip_cut))
        if corner:
            out.append([])
        out[-1].append(k)
        previous = k
    return out


def point_to_segment(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / n))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


class SyncView(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.geometry: PreviewGeometry | None = None
        self.sections: list[list[int]] = []
        self.section_of: dict[int, int] = {}  # move k -> its section's number
        self.selected: int | None = None  # section number
        self.zoom = 1.0
        self.shift = (0.0, 0.0)
        self._pan_from = (0, 0)

        bar = ttk.Frame(self, padding=(4, 2))
        bar.pack(fill=tk.X)
        self.links = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Links", variable=self.links, command=self.draw).pack(side=tk.LEFT)
        self.overlay = tk.BooleanVar(value=False)  # off: root above, tip below (a column)
        ttk.Checkbutton(bar, text="Overlay", variable=self.overlay, command=self.fit).pack(
            side=tk.LEFT, padx=(10, 0))
        self.targets = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Drawn outlines", variable=self.targets, command=self.draw).pack(
            side=tk.LEFT, padx=(10, 0))
        ttk.Button(bar, text="Fit view", command=self.fit).pack(side=tk.LEFT, padx=8)
        self.note = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.note).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(self, foreground="#5f6368", padding=(4, 0), text=(
            "root above, tip below (thick: root, thin: tip) · same colour = cut at the same time · dashed: the wire at "
            "section ends · click a section: select it · ←/→: next · wheel zoom · right-drag pan")).pack(anchor="w")
        self.canvas = tk.Canvas(self, background="white", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        c = self.canvas
        c.bind("<Configure>", lambda _e: self.draw())
        c.bind("<ButtonPress-1>", self._click)
        c.bind("<MouseWheel>", lambda e: self._zoom_at(e.x, e.y, ZOOM_STEP if e.delta > 0 else 1 / ZOOM_STEP))
        c.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, ZOOM_STEP))
        c.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / ZOOM_STEP))
        c.bind("<ButtonPress-3>", self._pan_start)
        c.bind("<B3-Motion>", self._pan_move)
        c.bind("<Double-Button-1>", lambda _e: self.fit())
        c.bind("<Left>", lambda _e: self.step(-1))
        c.bind("<Right>", lambda _e: self.step(1))

    # --- data ---------------------------------------------------------------------------------

    def show(self, geometry: PreviewGeometry | None) -> None:
        self.geometry = geometry
        self.sections = sections(geometry) if geometry is not None else []
        self.section_of = {k: n for n, moves in enumerate(self.sections) for k in moves}
        self.selected = None
        self.note.set("")
        self.fit()

    def colour(self, k: int) -> str:
        """The colour of move k: its section's, the same at the root and the tip."""
        n = self.section_of.get(k)
        return OTHER_COLOUR if n is None else PALETTE[n % len(PALETTE)]

    def ends(self, n: int) -> tuple[int, int]:
        """Path points at the start and the end of section n."""
        moves = self.sections[n]
        return moves[0] - 1, moves[-1]

    def select(self, n: int | None) -> None:
        self.selected = n
        g = self.geometry
        if g is None or n is None:
            self.note.set("")
        else:
            a, b = self.ends(n)

            def length(path: list[Point]) -> float:
                return sum(math.dist(path[k - 1], path[k]) for k in self.sections[n])

            self.note.set(
                f"section {n + 1} of {len(self.sections)}: root {length(g.root_cut):.2f} mm, "
                f"tip {length(g.tip_cut):.2f} mm · wire offset at start "
                f"{math.dist(g.root_cut[a], g.tip_cut[a]):.2f} mm, at end "
                f"{math.dist(g.root_cut[b], g.tip_cut[b]):.2f} mm")
        self.draw()

    def step(self, by: int) -> None:
        if not self.sections:
            return
        self.select(0 if self.selected is None else (self.selected + by) % len(self.sections))

    def section_at(self, x: float, y: float) -> int | None:
        """The section nearest canvas (x, y) on either path, within PICK_PIXELS."""
        g = self.geometry
        if g is None:
            return None
        best: tuple[float, int] | None = None
        for path in (g.root_cut, self.tip(g.tip_cut)):
            pts = self._to_canvas(path)
            for k, n in self.section_of.items():
                d = point_to_segment((x, y), pts[k - 1], pts[k])
                if d <= PICK_PIXELS and (best is None or d < best[0]):
                    best = (d, n)
        return None if best is None else best[1]

    # --- view ---------------------------------------------------------------------------------

    def _outline(self, path: list[Point]) -> list[Point]:
        return [path[k] for k in self.section_of] or path

    def tip_drop(self) -> float:
        """How far down the tip is drawn: 0 in the overlay, else below the root with a gap of
        STACK_GAP times the taller outline."""
        g = self.geometry
        if g is None or self.overlay.get():
            return 0.0
        root_z = [p[1] for p in self._outline(g.root_cut)]
        tip_z = [p[1] for p in self._outline(g.tip_cut)]
        gap = STACK_GAP * max(max(root_z) - min(root_z), max(tip_z) - min(tip_z), 1.0)
        return max(tip_z) - min(root_z) + gap

    def tip(self, pts: list[Point]) -> list[Point]:
        """Tip-face points where they are drawn."""
        drop = self.tip_drop()
        return [(x, z - drop) for x, z in pts]

    def _box(self) -> tuple[float, float, float, float]:
        g = self.geometry
        assert g is not None
        pts = self._outline(g.root_cut) + self.tip(self._outline(g.tip_cut))
        xs, zs = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(zs), max(xs), max(zs)

    def _transform(self) -> tuple[float, float, float]:
        """(scale, x offset, y offset): canvas x = ox − scale·x (+x away from the operator runs
        left, as in the section view), canvas y = oy − scale·z."""
        w, h = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 150)
        x0, z0, x1, z1 = self._box()
        scale = min((w - 2 * MARGIN) / max(x1 - x0, 1e-6), (h - 2 * MARGIN) / max(z1 - z0, 1e-6)) * self.zoom
        ox = w / 2 + scale * (x0 + x1) / 2 + self.shift[0]
        oy = h / 2 + scale * (z0 + z1) / 2 + self.shift[1]
        return scale, ox, oy

    def _to_canvas(self, pts: list[Point]) -> list[Point]:
        s, ox, oy = self._transform()
        return [(ox - s * x, oy - s * z) for x, z in pts]

    def fit(self) -> None:
        self.zoom, self.shift = 1.0, (0.0, 0.0)
        self.draw()

    def _zoom_at(self, x: float, y: float, factor: float) -> None:
        if self.geometry is None:
            return
        s, ox, oy = self._transform()
        wx, wz = (ox - x) / s, (oy - y) / s  # the world point under the cursor stays there
        self.zoom *= factor
        s2, ox2, oy2 = self._transform()
        self.shift = (self.shift[0] + x - (ox2 - s2 * wx), self.shift[1] + y - (oy2 - s2 * wz))
        self.draw()

    def _pan_start(self, event: "tk.Event[tk.Canvas]") -> None:
        self._pan_from = (event.x, event.y)

    def _pan_move(self, event: "tk.Event[tk.Canvas]") -> None:
        dx, dy = event.x - self._pan_from[0], event.y - self._pan_from[1]
        self._pan_from = (event.x, event.y)
        self.shift = (self.shift[0] + dx, self.shift[1] + dy)
        self.draw()

    def _click(self, event: "tk.Event[tk.Canvas]") -> None:
        self.canvas.focus_set()  # for the arrow keys
        self.select(self.section_at(event.x, event.y))

    # --- drawing ------------------------------------------------------------------------------

    def draw(self) -> None:
        c = self.canvas
        c.delete("all")
        g = self.geometry
        if g is None:
            c.create_text(20, 20, anchor="nw", fill="#5f6368", text="No preview yet. Press Preview.")
            return
        if self.targets.get():
            for target in (g.root_target, self.tip(g.tip_target)):
                pts = self._to_canvas(target + target[:1])
                c.create_line(*[v for p in pts for v in p], fill=TARGET_COLOUR, width=1)
        root, tip = self._to_canvas(g.root_cut), self._to_canvas(self.tip(g.tip_cut))
        if not self.overlay.get():  # name the two rows above their highest point
            for pts, name in ((root, "root face"), (tip, "tip face")):
                top = min(self._outline(pts), key=lambda p: p[1])
                c.create_text(top[0], top[1] - 8, anchor="s", fill="#5f6368", text=name)
        if self.links.get():
            for n in range(len(self.sections)):
                a = self.ends(n)[0]
                c.create_line(*root[a], *tip[a], fill=LINK_COLOUR, dash=(3, 3))
        for pts, width in ((root, ROOT_WIDTH), (tip, TIP_WIDTH)):
            for k in range(1, len(pts)):
                c.create_line(*pts[k - 1], *pts[k], fill=self.colour(k), width=width, capstyle="round")
        chosen = self.selected
        if chosen is not None:
            moves = self.sections[chosen]
            for pts, width in ((root, ROOT_WIDTH), (tip, TIP_WIDTH)):
                line = [v for p in pts[moves[0] - 1 : moves[-1] + 1] for v in p]
                c.create_line(*line, fill="black", width=width + 4, capstyle="round", joinstyle="round")
                c.create_line(*line, fill=self.colour(moves[0]), width=width, capstyle="round",
                              joinstyle="round")
            for k in self.ends(chosen):
                c.create_line(*root[k], *tip[k], fill="black", width=2, dash=(6, 4))
        c.create_text(8, c.winfo_height() - 6, anchor="sw", fill="#5f6368",
                      text=f"{len(self.sections)} sections of the outline")
