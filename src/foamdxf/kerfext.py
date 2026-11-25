"""Kerf beyond the measured range, for one job (job-file.md §3 `kerf_extension`).

A kerf table holds measured values and the lookup never extrapolates (machine-profile.md
§7). When a cut needs a speed outside the table, Preview shows the table as a graph with
the speed the cut needs, and the user places one extra point beyond the table's end: by
dragging it or typing it. The point is used for this job only and listed in the cut report
as not measured; the machine profile is not changed. In the form it is kept as text, with
the table's name, so picking another table drops it.
"""

import json
import math
import re
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

Point = tuple[float, float]  # (speed mm/min, kerf mm)

OUT_OF_RANGE = re.compile(
    r'kerf table "(?P<name>[^"]*)": speed (?P<speed>[\d.]+) mm/min is outside the table range '
    r"(?P<low>[\d.]+) to (?P<high>[\d.]+) mm/min"
)
MARGIN = 0.10  # the suggested point lies this share beyond the speed the cut needs
ROUND_TO = 5.0  # mm/min
MIN_KERF = 0.05  # mm: the dragged kerf stays above this


@dataclass(frozen=True)
class OutOfRange:
    table: str
    speed: float  # what the cut needs
    low: float  # the range the lookup had (table and any extension)
    high: float
    why: str = ""  # the CAM's explanation, if it gave one

    @property
    def below(self) -> bool:
        return self.speed < self.low


def out_of_range(message: str) -> OutOfRange | None:
    """The first out-of-range kerf lookup in a CAM error message (its wording is fixed by
    machine-profile.md §7), with the CAM's "why:" line after it."""
    match = OUT_OF_RANGE.search(message)
    if match is None:
        return None
    rest = message[match.end():]
    why = next((line.strip()[len("- why: "):] for line in rest.splitlines() if line.strip().startswith("- why: ")), "")
    return OutOfRange(match["name"], float(match["speed"]), float(match["low"]), float(match["high"]), why)


def extension_from_text(text: str, table: str | None) -> tuple[Point, ...]:
    """The points kept in the form for `table`; none if they belong to another table."""
    if not text.strip() or table is None:
        return ()
    try:
        data = json.loads(text)
        if data.get("table") != table:
            return ()
        return tuple(sorted((float(s), float(k)) for s, k in data["points"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        return ()


def extension_to_text(table: str, points: tuple[Point, ...]) -> str:
    return json.dumps({"table": table, "points": [list(p) for p in sorted(points)]}) if points else ""


def extrapolate(points: list[Point], speed: float) -> float:
    """Kerf at `speed` on the straight line through the two table points nearest that end."""
    pts = sorted(points)
    (s0, k0), (s1, k1) = (pts[0], pts[1]) if speed < pts[0][0] else (pts[-2], pts[-1])
    return k0 + (k1 - k0) * (speed - s0) / (s1 - s0)


def suggested(points: list[Point], need: OutOfRange) -> Point:
    """A point a little beyond the speed the cut needs, on the extrapolated line."""
    if need.below:
        speed = max(ROUND_TO, math.floor(need.speed * (1 - MARGIN) / ROUND_TO + 1e-9) * ROUND_TO)
    else:
        speed = math.ceil(need.speed * (1 + MARGIN) / ROUND_TO - 1e-9) * ROUND_TO
    return speed, round(max(MIN_KERF, extrapolate(points, speed)), 3)


def merged(existing: tuple[Point, ...], point: Point, table_low: float) -> tuple[Point, ...]:
    """The job's extension with `point` added; it replaces an earlier one on the same side."""
    below = point[0] < table_low
    kept = [p for p in existing if (p[0] < table_low) != below]
    return tuple(sorted([*kept, point]))


class ExtensionDialog:
    """Modal: the table as a graph, the speed the cut needs, and one draggable extra point.
    `result` is the chosen (speed, kerf), or None if cancelled."""

    W, H, PAD = 520, 300, 46

    def __init__(self, parent: tk.Misc, table_points: list[Point], extension: tuple[Point, ...],
                 need: OutOfRange) -> None:
        self.table = sorted(table_points)
        self.extension = [p for p in extension]
        self.need = need
        self.result: Point | None = None
        speed, kerf = suggested(self.table + self.extension, need)
        self.speed = tk.StringVar(value=f"{speed:g}")
        self.kerf = tk.StringVar(value=f"{kerf:g}")
        self._dragging = False

        top = self.top = tk.Toplevel(parent)
        top.title("Speed outside the kerf table")
        top.transient(parent.winfo_toplevel())
        frame = ttk.Frame(top, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        side = "below" if need.below else "above"
        ttk.Label(frame, wraplength=self.W, justify="left", text=(
            f'Kerf table "{need.table}" goes from {need.low:g} to {need.high:g} mm/min. This cut needs '
            f"{need.speed:.0f} mm/min, {side} it." + (f"\n\nWhy: {need.why}." if need.why else ""))).pack(anchor="w")
        self.canvas = tk.Canvas(frame, width=self.W, height=self.H, background="white", highlightthickness=0)
        self.canvas.pack(pady=8)
        row = ttk.Frame(frame)
        row.pack(anchor="w")
        ttk.Label(row, text="Extra point: speed").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.speed, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Label(row, text="mm/min, kerf").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.kerf, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Label(row, text="mm").pack(side=tk.LEFT)
        self.note = tk.StringVar()
        ttk.Label(frame, textvariable=self.note, foreground="#b3261e").pack(anchor="w", pady=(4, 0))
        ttk.Label(frame, foreground="#5f6368", wraplength=self.W, justify="left", text=(
            "Drag the orange point, or type it. It starts on the straight line through the two "
            "measured points nearest that end. It is NOT measured: it is used for this job only and "
            "listed in the cut report; the machine profile is not changed.")).pack(anchor="w", pady=(4, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e", pady=(8, 0))
        ttk.Button(buttons, text="Use for this job", command=self.accept).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Cancel", command=self.top.destroy).pack(side=tk.LEFT)
        for var in (self.speed, self.kerf):
            var.trace_add("write", lambda *_: self.draw())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_dragging", False))
        top.bind("<Return>", lambda _e: self.accept())
        top.bind("<Escape>", lambda _e: self.top.destroy())
        self.draw()

    # --- the chosen point ---------------------------------------------------------------------

    def chosen(self) -> Point | str:
        """The typed point, or why it cannot be used."""
        try:
            speed = float(self.speed.get().replace(",", "."))
            kerf = float(self.kerf.get().replace(",", "."))
        except ValueError:
            return "enter the speed and the kerf as numbers"
        low, high = self.table[0][0], self.table[-1][0]
        if kerf <= 0 or speed <= 0:
            return "the speed and the kerf must be more than 0"
        if low <= speed <= high:
            return f"the speed must lie outside the measured range {low:g} to {high:g} mm/min"
        if self.need.below and speed > self.need.speed or not self.need.below and speed < self.need.speed:
            return f"the point must reach {self.need.speed:.0f} mm/min, the speed this cut needs"
        return speed, kerf

    def accept(self) -> None:
        point = self.chosen()
        if isinstance(point, str):
            self.note.set(point.capitalize() + ".")
            return
        self.result = point
        self.top.destroy()

    def wait(self) -> Point | None:
        self.top.grab_set()
        self.top.wait_window()
        return self.result

    # --- the graph ----------------------------------------------------------------------------

    def _ranges(self) -> tuple[float, float, float, float]:
        point = self.chosen()
        speeds = [s for s, _ in self.table + self.extension] + [self.need.speed]
        kerfs = [k for _, k in self.table + self.extension]
        if not isinstance(point, str):
            speeds.append(point[0])
            kerfs.append(point[1])
        s0, s1 = min(speeds), max(speeds)
        pad = 0.08 * (s1 - s0 or 1)
        return max(0.0, s0 - pad), s1 + pad, 0.0, max(kerfs) * 1.25

    def _xy(self, speed: float, kerf: float) -> tuple[float, float]:
        s0, s1, k0, k1 = self._ranges()
        x = self.PAD + (speed - s0) / (s1 - s0) * (self.W - 2 * self.PAD)
        y = self.H - self.PAD + (k0 - kerf) / (k1 - k0) * (self.H - 2 * self.PAD)
        return x, y

    def _value(self, x: float, y: float) -> Point:
        s0, s1, k0, k1 = self._ranges()
        speed = s0 + (x - self.PAD) / (self.W - 2 * self.PAD) * (s1 - s0)
        kerf = k0 + (self.H - self.PAD - y) / (self.H - 2 * self.PAD) * (k1 - k0)
        return speed, kerf

    def draw(self) -> None:
        c = self.canvas
        c.delete("all")
        s0, s1, k0, k1 = self._ranges()
        left, bottom = self.PAD, self.H - self.PAD
        c.create_line(left, bottom, self.W - self.PAD / 2, bottom, fill="#5f6368")
        c.create_line(left, bottom, left, self.PAD / 2, fill="#5f6368")
        c.create_text(self.W - self.PAD / 2, bottom + 26, anchor="e", text="speed, mm/min", fill="#5f6368")
        c.create_text(8, self.PAD / 2 - 6, anchor="w", text="kerf, mm", fill="#5f6368")
        for i in range(5):
            s = s0 + (s1 - s0) * i / 4
            k = k0 + (k1 - k0) * i / 4
            x, _ = self._xy(s, k0)
            _, y = self._xy(s0, k)
            c.create_text(x, bottom + 12, text=f"{s:.0f}", fill="#5f6368")
            c.create_text(left - 6, y, anchor="e", text=f"{k:.1f}", fill="#5f6368")
        # the measured range, shaded
        xa, _ = self._xy(self.table[0][0], 0)
        xb, _ = self._xy(self.table[-1][0], 0)
        c.create_rectangle(xa, self.PAD / 2, xb, bottom, fill="#e8f0fe", outline="")
        c.create_text((xa + xb) / 2, self.PAD / 2 + 8, text="measured", fill="#1a73e8")
        # the speed the cut needs
        xn, _ = self._xy(self.need.speed, 0)
        c.create_line(xn, self.PAD / 2, xn, bottom, fill="#d93025", dash=(5, 3))
        c.create_text(xn, self.PAD / 2 - 4, text=f"needed {self.need.speed:.0f}", fill="#d93025")
        pts = [self._xy(s, k) for s, k in self.table]
        c.create_line(*[v for p in pts for v in p], fill="#1a73e8", width=2)
        for x, y in pts:
            c.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#1a73e8", outline="")
        for s, k in self.extension:  # this job's earlier extra points
            x, y = self._xy(s, k)
            c.create_oval(x - 4, y - 4, x + 4, y + 4, outline="#e37400", width=2)
        point = self.chosen()
        if not isinstance(point, str):
            end = self.table[0] if point[0] < self.table[0][0] else self.table[-1]
            (xe, ye), (xp, yp) = self._xy(*end), self._xy(*point)
            c.create_line(xe, ye, xp, yp, fill="#e37400", width=2, dash=(6, 3))
            c.create_oval(xp - 7, yp - 7, xp + 7, yp + 7, fill="#e37400", outline="black", tags="handle")
            low_side = point[0] < self.table[0][0]  # label on the side away from the y axis / edge
            c.create_text(xp + (12 if low_side else -12), yp - 12, anchor="w" if low_side else "e",
                          text=f"{point[0]:g} mm/min, {point[1]:.3f} mm", fill="#e37400")
            self.note.set("")
        else:
            self.note.set(point.capitalize() + ".")

    def _press(self, event: "tk.Event[tk.Canvas]") -> None:
        self._dragging = bool(self.canvas.find_withtag("current")) and "handle" in self.canvas.gettags("current")
        if not self._dragging:  # a click elsewhere on the graph moves the point there
            self._dragging = True
            self._drag(event)

    def _drag(self, event: "tk.Event[tk.Canvas]") -> None:
        if not self._dragging:
            return
        speed, kerf = self._value(event.x, event.y)
        self.speed.set(f"{round(speed / ROUND_TO) * ROUND_TO:g}")
        self.kerf.set(f"{max(MIN_KERF, round(kerf, 3)):g}")
