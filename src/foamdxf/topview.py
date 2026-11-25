"""The stock seen from above (x, y), placed by one of nine points.

Machine tab, controls, "Top view": the shared x travel and the two tower planes (axes
1-2 at y = 0, axes 3-4 at y = wire_span), the stock of the last Preview between its
foam faces, and nine points on it (corners, edge midpoints, centre). The chosen point's
position is given as distances from the machine corner at the operator end of the
travel (smallest x) on the axes 3-4 tower's plane. Typing new distances and pressing
Place moves the stock (Back face x and Root face y) and previews again: putting the
stock nearer a tower shortens the wire beyond the far foam face, which helps a big taper
or sweep stay in the travel.
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from .pipeline import PreviewGeometry

# the nine points: (x fraction from the operator side, y fraction from the axes 3-4 tower side)
FRACTIONS = (0.0, 0.5, 1.0)
POINTS = [f"{fx:g}-{fy:g}" for fx in FRACTIONS for fy in FRACTIONS]
DEFAULT_POINT = "0-0"  # the stock corner nearest the reference corner
PAD = 34  # px round the drawing
HIT_PX = 10  # a click this near a point picks it

COLOURS = {
    "travel": "#9aa0a6",
    "tower": "#5f6368",
    "stock": "#fff4c2",
    "stock_edge": "#c9a227",
    "point": "#ffffff",
    "chosen": "#d93025",
    "ref": "#188038",
    "text": "#202124",
    "muted": "#5f6368",
}


def _text(value: float) -> str:
    """A distance for an entry: to 0.001 mm, so Place without an edit leaves the stock put."""
    return f"{value:.3f}".rstrip("0").rstrip(".")


def fractions(key: str) -> tuple[float, float]:
    fx, fy = key.split("-")
    return float(fx), float(fy)


def point_position(g: PreviewGeometry, key: str) -> tuple[float, float]:
    """(x, y) of point `key` on the stock of `g`."""
    fx, fy = fractions(key)
    x0, _, x1, _ = g.stock
    y_lo, y_hi = sorted((g.root_y, g.tip_y))
    return x0 + fx * (x1 - x0), y_hi - fy * (y_hi - y_lo)


def distances(g: PreviewGeometry, key: str) -> tuple[float, float]:
    """(dx, dy) of point `key` from the reference corner: dx from the travel's smallest x
    (away from the operator), dy from the axes 3-4 tower plane (toward the other tower)."""
    x, y = point_position(g, key)
    return x - g.travel[0], g.wire_span - y


def placement(g: PreviewGeometry, key: str, dx: float, dy: float) -> tuple[float, float]:
    """(back face x, root face y) that put point `key` at (dx, dy) from the reference corner,
    with the stock length and span of `g`."""
    fx, fy = fractions(key)
    length = g.stock[2] - g.stock[0]
    span = abs(g.tip_y - g.root_y)
    y_hi = g.wire_span - dy + fy * span
    root_y = y_hi - span if g.root_y <= g.tip_y else y_hi
    return g.travel[0] + dx - fx * length, root_y


class TopView(ttk.Frame):
    """`on_place(back face x, root face y)` sets the form and previews again;
    `on_centre()` blanks both, back to the default (centred) placement."""

    def __init__(self, parent: tk.Misc, on_place: Callable[[float, float], None],
                 on_centre: Callable[[], None]) -> None:
        super().__init__(parent, padding=8)
        self.on_place = on_place
        self.on_centre = on_centre
        self.geometry: PreviewGeometry | None = None
        self.letters = ("1", "2", "3", "4")
        self.point = tk.StringVar(value=DEFAULT_POINT)
        self.dx = tk.StringVar(value="")
        self.dy = tk.StringVar(value="")

        self.canvas = tk.Canvas(self, background="white", highlightthickness=0, width=300, height=200)
        self.canvas.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        form = ttk.Frame(self)
        form.grid(row=0, column=1, sticky="nw", padx=(10, 0))
        self.dx_label = ttk.Label(form, text="x from the operator end")
        self.dx_label.grid(row=0, column=0, sticky="w")
        self.dx_entry = ttk.Entry(form, textvariable=self.dx, width=9)
        self.dx_entry.grid(row=0, column=1, padx=4)
        ttk.Label(form, text="mm").grid(row=0, column=2, sticky="w")
        self.dy_label = ttk.Label(form, text="y from the tower")
        self.dy_label.grid(row=1, column=0, sticky="w")
        self.dy_entry = ttk.Entry(form, textvariable=self.dy, width=9)
        self.dy_entry.grid(row=1, column=1, padx=4)
        ttk.Label(form, text="mm").grid(row=1, column=2, sticky="w")
        buttons = ttk.Frame(form)
        buttons.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.place_button = ttk.Button(buttons, text="Place", command=self.place_stock)
        self.place_button.pack(side=tk.LEFT)
        ttk.Button(buttons, text="Centre", command=self.on_centre).pack(side=tk.LEFT, padx=(6, 0))
        self.note = ttk.Label(form, foreground=COLOURS["muted"], justify="left", wraplength=210)
        self.note.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        for entry in (self.dx_entry, self.dy_entry):
            entry.bind("<Return>", lambda _e: self.place_stock())
        self.canvas.bind("<Configure>", lambda _e: self.draw())
        self.canvas.bind("<ButtonPress-1>", self._click)
        self.show(None)

    # --- state -------------------------------------------------------------------------------

    def show(self, geometry: PreviewGeometry | None, letters: tuple[str, str, str, str] | None = None) -> None:
        self.geometry = geometry
        if letters is not None:
            self.letters = letters
        _, _, l3, l4 = self.letters
        self.dx_label.configure(text="x from the operator end")
        self.dy_label.configure(text=f"y from the {l3}/{l4} tower")
        self._fill()
        self.draw()

    def _fill(self) -> None:
        g = self.geometry
        state = ["!disabled"] if g is not None else ["disabled"]
        for widget in (self.dx_entry, self.dy_entry, self.place_button):
            widget.state(state)
        if g is None:
            self.dx.set("")
            self.dy.set("")
            self.note.configure(text="Press Preview to see the stock from above.")
            return
        dx, dy = distances(g, self.point.get())
        self.dx.set(_text(dx))
        self.dy.set(_text(dy))
        _, _, l3, l4 = self.letters
        self.note.configure(text=(
            f"Click a point on the stock, type where it goes, Place. From the green corner: "
            f"x {g.travel[0]:g} on the {l3}/{l4} tower. Nearer a tower: less travel for a big taper."))

    def select(self, key: str) -> None:
        self.point.set(key)
        self._fill()
        self.draw()

    def place_stock(self) -> None:
        g = self.geometry
        if g is None:
            return
        try:
            dx, dy = float(self.dx.get().replace(",", ".")), float(self.dy.get().replace(",", "."))
        except ValueError:
            self.note.configure(text="Type both distances as numbers (mm).")
            return
        self.on_place(*placement(g, self.point.get(), dx, dy))

    # --- drawing -----------------------------------------------------------------------------

    def _frame(self) -> tuple[float, float, float, float, float] | None:
        """(scale, x0, x extent, canvas width, canvas height): x runs up, y runs left as in the 3D view."""
        g = self.geometry
        if g is None:
            return None
        w, h = max(self.canvas.winfo_width(), 100), max(self.canvas.winfo_height(), 100)
        x_lo = min(g.travel[0], g.stock[0])
        x_hi = max(g.travel[2], g.stock[2])
        scale = min((w - 2 * PAD) / g.wire_span, (h - 2 * PAD) / max(x_hi - x_lo, 1e-6))
        return scale, x_lo, x_hi - x_lo, w, h

    def to_canvas(self, x: float, y: float) -> tuple[float, float]:
        frame = self._frame()
        assert frame is not None
        scale, x0, extent, w, h = frame
        g = self.geometry
        assert g is not None
        left = (w - g.wire_span * scale) / 2
        bottom = (h + extent * scale) / 2  # centred both ways
        return left + (g.wire_span - y) * scale, bottom - (x - x0) * scale

    def draw(self) -> None:
        c = self.canvas
        c.delete("all")
        g = self.geometry
        if g is None:
            c.create_text(10, 10, anchor="nw", fill=COLOURS["muted"], text="No preview yet.")
            return
        l1, l2, l3, l4 = self.letters
        W = g.wire_span
        tx0, _, tx1, _ = g.travel
        # the shared x travel between the towers
        c.create_rectangle(*self.to_canvas(tx0, 0), *self.to_canvas(tx1, W), outline=COLOURS["travel"], dash=(4, 3))
        for y, label in ((0.0, f"{l1}/{l2}\ntower"), (W, f"{l3}/{l4}\ntower")):
            ax, ay = self.to_canvas(tx0, y)
            bx, by = self.to_canvas(tx1, y)
            c.create_line(ax, ay, bx, by, fill=COLOURS["tower"], width=4)
            if y != 0.0:  # the axes 3-4 tower, on the left
                c.create_text(ax - 4, (ay + by) / 2, text=label, anchor="e", fill=COLOURS["text"], justify="center")
            else:
                c.create_text(ax + 4, (ay + by) / 2, text=label, anchor="w", fill=COLOURS["text"], justify="center")
        # the stock between its foam faces
        sx0, _, sx1, _ = g.stock
        c.create_rectangle(*self.to_canvas(sx0, g.root_y), *self.to_canvas(sx1, g.tip_y),
                           fill=COLOURS["stock"], outline=COLOURS["stock_edge"], width=2)
        for y, label in ((g.root_y, "root"), (g.tip_y, "tip")):
            px, py = self.to_canvas(sx1, y)
            c.create_text(px, py - 2, text=label, anchor="s", fill=COLOURS["muted"])
        # reference corner and the chosen point's distances from it
        rx, ry = self.to_canvas(tx0, W)
        c.create_oval(rx - 5, ry - 5, rx + 5, ry + 5, fill=COLOURS["ref"], outline="")
        px, py = point_position(g, self.point.get())
        cx, cy = self.to_canvas(px, py)
        c.create_line(rx, ry, rx, cy, fill=COLOURS["ref"], dash=(3, 2))
        c.create_line(rx, cy, cx, cy, fill=COLOURS["ref"], dash=(3, 2))
        for key in POINTS:
            kx, ky = self.to_canvas(*point_position(g, key))
            chosen = key == self.point.get()
            r = 5 if chosen else 4
            c.create_oval(kx - r, ky - r, kx + r, ky + r, outline=COLOURS["chosen"] if chosen else COLOURS["text"],
                          fill=COLOURS["chosen"] if chosen else COLOURS["point"], tags=("point", key))
        c.create_text(self.canvas.winfo_width() / 2, self.canvas.winfo_height() - 4, anchor="s",
                      text="operator (smallest x) this side", fill=COLOURS["muted"])

    def _click(self, event: "tk.Event[tk.Canvas]") -> None:
        g = self.geometry
        if g is None:
            return
        best, best_d = None, float(HIT_PX)
        for key in POINTS:
            kx, ky = self.to_canvas(*point_position(g, key))
            d = ((kx - event.x) ** 2 + (ky - event.y) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = key, d
        if best is not None:
            self.select(best)
