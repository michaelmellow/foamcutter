"""3D wireframe preview on a Tkinter canvas: no OpenGL or plotting library needed.

Work frame (machine-profile.md §3): x horizontal across the wire, y along the
wire from the axes 1-2 tower (y = 0) to the axes 3-4 tower (y = wire_span),
z up. Orthographic projection, orbited with the mouse:

  left drag   rotate          wheel        zoom
  right drag  pan             double-click reset the view
"""

import math
import tkinter as tk
from dataclasses import dataclass

from .pipeline import PreviewGeometry

Point3 = tuple[float, float, float]

# Default view: from the operator's side, a little from above and to the side,
# with +x (away from the operator) going into the screen and the wire left-right.
DEFAULT_YAW = 65.0  # degrees about z
DEFAULT_PITCH = 25.0  # degrees above the horizon

COLOURS = {
    "floor": "#dadce0",
    "travel": "#9aa0a6",
    "stock": "#c9a227",
    "root": "#1f6fb4",
    "tip": "#e8710a",
    "surface": "#c5cad1",
    "wire": "#d93025",
    "align": "#188038",
    "text": "#202124",
    "muted": "#5f6368",
}


@dataclass
class Camera:
    yaw: float = DEFAULT_YAW
    pitch: float = DEFAULT_PITCH
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0

    def project(self, p: Point3, centre: Point3, scale: float, width: int, height: int) -> tuple[float, float]:
        """Work-frame point -> canvas pixel (orthographic).

        After the yaw about z, x1 is screen-right and y1 points into the screen
        (right-handed with z up); the pitch tilts the view down by `pitch`.
        """
        x, y, z = p[0] - centre[0], p[1] - centre[1], p[2] - centre[2]
        a, b = math.radians(self.yaw), math.radians(self.pitch)
        x1 = x * math.cos(a) - y * math.sin(a)
        y1 = x * math.sin(a) + y * math.cos(a)
        screen_y = z * math.cos(b) + y1 * math.sin(b)
        s = scale * self.zoom
        return width / 2 + self.pan_x + s * x1, height / 2 + self.pan_y - s * screen_y


class View3D:
    """Draws a PreviewGeometry in 3D on `canvas`, with the wire at `wire_index`."""

    def __init__(self, canvas: tk.Canvas) -> None:
        self.canvas = canvas
        self.camera = Camera()
        self.geometry: PreviewGeometry | None = None
        self.wire_index = 0
        self.highlight: tuple[float, float, str] | None = None  # (x, z, name) of a stock edge to show
        self._drag: tuple[int, int] | None = None
        canvas.bind("<Configure>", lambda _e: self.draw())
        canvas.bind("<ButtonPress-1>", self._press)
        canvas.bind("<B1-Motion>", self._rotate)
        canvas.bind("<ButtonPress-3>", self._press)
        canvas.bind("<B3-Motion>", self._pan)
        canvas.bind("<MouseWheel>", self._wheel)  # Windows, macOS
        canvas.bind("<Button-4>", lambda _e: self._zoom(1.15))  # Linux
        canvas.bind("<Button-5>", lambda _e: self._zoom(1 / 1.15))
        canvas.bind("<Double-Button-1>", lambda _e: self.reset())

    # --- state ------------------------------------------------------------------------------

    def show(self, geometry: PreviewGeometry | None) -> None:
        self.geometry = geometry
        self.wire_index = 0
        self.highlight = None
        self.draw()

    def set_wire(self, index: int) -> None:
        if self.geometry is None:
            return
        self.wire_index = max(0, min(index, len(self.geometry.tower_12) - 1))
        self.draw()

    def reset(self) -> None:
        self.camera = Camera()
        self.draw()

    # --- geometry ---------------------------------------------------------------------------

    def _frame(self) -> tuple[Point3, float]:
        """Centre of the cut (stock and carriage paths, tower to tower) and the
        scale that fits it at zoom 1 in any orientation. The travel windows may
        reach outside the view."""
        g = self.geometry
        assert g is not None
        xs = [p[0] for p in g.tower_12 + g.tower_34] + [g.stock[0], g.stock[2]]
        zs = [p[1] for p in g.tower_12 + g.tower_34] + [g.stock[1], g.stock[3]]
        lo = (min(xs), 0.0, min(zs))
        hi = (max(xs), g.wire_span, max(zs))
        centre = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
        diagonal = math.dist(lo, hi)
        w, h = self._size()
        return centre, 0.92 * min(w, h) / max(diagonal, 1e-6)

    def _size(self) -> tuple[int, int]:
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        return (w, h) if w > 50 and h > 50 else (800, 600)

    def _line(self, points: list[Point3], colour: str, width: float = 1.0,
              dash: tuple[int, ...] = (), closed: bool = False) -> None:
        if len(points) < 2:
            return
        centre, scale = self._frame()
        w, h = self._size()
        pts = points + points[:1] if closed else points
        flat: list[float] = []
        for p in pts:
            flat += self.camera.project(p, centre, scale, w, h)
        self.canvas.create_line(*flat, fill=colour, width=width, dash=dash)

    def _text(self, p: Point3, text: str, colour: str, anchor: str = "w") -> None:
        centre, scale = self._frame()
        w, h = self._size()
        x, y = self.camera.project(p, centre, scale, w, h)
        item = self.canvas.create_text(x, y, text=text, fill=colour, anchor=anchor)  # type: ignore[call-overload]
        box = self.canvas.bbox(item)
        if box:
            back = self.canvas.create_rectangle(box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1,
                                                fill="white", outline="")
            self.canvas.tag_lower(back, item)

    # --- drawing ----------------------------------------------------------------------------

    def draw(self) -> None:
        c = self.canvas
        c.delete("all")
        g = self.geometry
        if g is None:
            c.create_text(20, 20, anchor="nw", fill=COLOURS["muted"],
                          text="No preview yet. Fill in the form and press Preview.")
            return
        W = g.wire_span
        tx0, tz0, tx1, tz1 = g.travel
        sx0, sz0, sx1, sz1 = g.stock

        # table / floor under the stock, and each tower's travel window
        self._line([(tx0, 0, sz0), (tx1, 0, sz0), (tx1, W, sz0), (tx0, W, sz0)], COLOURS["floor"], closed=True)
        l1, l2, l3, l4 = g.letters
        labels: list[tuple[Point3, str]] = []
        for y, label in ((0.0, f"{l1}/{l2} tower (axes 1-2)"), (W, f"{l3}/{l4} tower (axes 3-4)")):
            self._line([(tx0, y, tz0), (tx1, y, tz0), (tx1, y, tz1), (tx0, y, tz1)],
                       COLOURS["travel"], dash=(4, 3), closed=True)
            labels.append(((tx0, y, max(z for _, z in g.tower_12 + g.tower_34) + 15), label))

        # ruled surface the wire sweeps: every k-th wire line, faint
        n = len(g.tower_12)
        step = max(1, n // 60)
        for i in range(0, n, step):
            a, b = g.tower_12[i], g.tower_34[i]
            self._line([(a[0], 0.0, a[1]), (b[0], W, b[1])], COLOURS["surface"])

        # stock box between the foam faces
        ya, yb = g.root_y, g.tip_y
        for y in (ya, yb):
            self._line([(sx0, y, sz0), (sx1, y, sz0), (sx1, y, sz1), (sx0, y, sz1)], COLOURS["stock"], closed=True)
        for x, z in ((sx0, sz0), (sx1, sz0), (sx1, sz1), (sx0, sz1)):
            self._line([(x, ya, z), (x, yb, z)], COLOURS["stock"])

        # carriage paths at the tower planes, cut and outline at the foam faces
        self._line([(x, 0.0, z) for x, z in g.tower_12], COLOURS["root"], dash=(5, 3))
        self._line([(x, W, z) for x, z in g.tower_34], COLOURS["tip"], dash=(5, 3))
        self._line([(x, ya, z) for x, z in g.root_cut], COLOURS["root"], width=2)
        self._line([(x, yb, z) for x, z in g.tip_cut], COLOURS["tip"], width=2)
        self._line([(x, ya, z) for x, z in g.root_target], COLOURS["text"], closed=True)
        self._line([(x, yb, z) for x, z in g.tip_target], COLOURS["text"], closed=True)
        for y, holes in ((ya, g.root_holes), (yb, g.tip_holes)):
            for hole in holes:
                self._line([(x, y, z) for x, z in hole], COLOURS["text"], closed=True)

        # the wire at the scrubbed position
        i = self.wire_index
        a, b = g.tower_12[i], g.tower_34[i]
        self._line([(a[0], 0.0, a[1]), (b[0], W, b[1])], COLOURS["wire"], width=2.5)

        if self.highlight is not None:
            hx, hz, name = self.highlight
            self._line([(hx, 0.0, hz), (hx, W, hz)], COLOURS["align"], width=3)
            labels.append(((hx, W / 2, hz + 8), (f"wire on the {name}: {g.letters[0]}={g.letters[2]}={hx:.3f}, "
                                                 f"{g.letters[1]}={g.letters[3]}={hz:.3f}")))
        for where, label in labels:
            self._text(where, label, COLOURS["text"])
        self._axes_triad()
        self._legend()

    def _axes_triad(self) -> None:
        _, h = self._size()
        ox, oy, length = 50.0, h - 50.0, 30.0
        for vector, label in (((1.0, 0.0, 0.0), "x"), ((0.0, 1.0, 0.0), "y"), ((0.0, 0.0, 1.0), "z")):
            px, py = self.camera.project(vector, (0.0, 0.0, 0.0), length, 0, 0)
            self.canvas.create_line(ox, oy, ox + px, oy + py, fill=COLOURS["text"], arrow=tk.LAST)
            self.canvas.create_text(ox + 1.25 * px, oy + 1.25 * py, text=label, fill=COLOURS["text"])

    def _legend(self) -> None:
        g = self.geometry
        assert g is not None
        l1, l2, l3, l4 = g.letters
        items = [
            (COLOURS["root"], (), 2, "root face cut"),
            (COLOURS["tip"], (), 2, "tip face cut"),
            (COLOURS["root"], (5, 3), 1, f"{l1}/{l2} carriage"),
            (COLOURS["tip"], (5, 3), 1, f"{l3}/{l4} carriage"),
            (COLOURS["stock"], (), 1, "stock"),
            (COLOURS["wire"], (), 2.5, "wire"),
        ]
        for k, (colour, dash, width, label) in enumerate(items):
            y = 14 + 16 * k
            self.canvas.create_line(12, y, 40, y, fill=colour, width=width, dash=dash)
            self.canvas.create_text(46, y, text=label, anchor="w", fill=COLOURS["text"])
        self.canvas.create_text(12, 14 + 16 * len(items) + 4, anchor="nw", fill=COLOURS["muted"],
                                text="drag: rotate   right-drag: pan   wheel: zoom   double-click: reset")

    # --- mouse ------------------------------------------------------------------------------

    def _press(self, event: "tk.Event[tk.Canvas]") -> None:
        self._drag = (event.x, event.y)

    def _rotate(self, event: "tk.Event[tk.Canvas]") -> None:
        if self._drag is None:
            return
        dx, dy = event.x - self._drag[0], event.y - self._drag[1]
        self._drag = (event.x, event.y)
        self.camera.yaw = (self.camera.yaw - 0.4 * dx) % 360
        self.camera.pitch = max(-89.0, min(89.0, self.camera.pitch + 0.4 * dy))
        self.draw()

    def _pan(self, event: "tk.Event[tk.Canvas]") -> None:
        if self._drag is None:
            return
        self.camera.pan_x += event.x - self._drag[0]
        self.camera.pan_y += event.y - self._drag[1]
        self._drag = (event.x, event.y)
        self.draw()

    def _wheel(self, event: "tk.Event[tk.Canvas]") -> None:
        self._zoom(1.15 if event.delta > 0 else 1 / 1.15)

    def _zoom(self, factor: float) -> None:
        self.camera.zoom = max(0.2, min(20.0, self.camera.zoom * factor))
        self.draw()
