"""Fitting the GUI on small or highly scaled screens.

With Windows display scaling at 200 % a 4K or 1440p screen gives the window only half its
pixels, and the form is taller than that. Two remedies, both in this module:

- **UI size** (`UI_SIZES`): a factor on Tk's font scaling, chosen in View > UI size (kept
  in the machine settings file) or with `--ui-size`. Fonts and the widgets measured in
  them shrink; it takes effect at the next start, since Tk does not re-measure fonts that
  are already in use.
- **ScrollArea**: the whole window scrolls when it is smaller than the content needs, so
  nothing is out of reach whatever the size.
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

UI_SIZES = (1.0, 0.9, 0.8, 0.7, 0.6)
UI_SIZE_KEY = "ui_size"  # machine settings file
SCREEN_MARGIN = (40, 90)  # px left free around the window at start (taskbar, title bar)
WINDOW_MIN = (640, 420)
RECHECK_MS = 500  # how often the scroll area looks whether the content's size changed


def parse_ui_size(value: object) -> float:
    """A stored or typed UI size; 1.0 if it is not a sensible one."""
    try:
        size = float(str(value).rstrip("%")) / (100 if str(value).endswith("%") else 1)
    except ValueError:
        return 1.0
    return size if 0.4 <= size <= 1.5 else 1.0


def apply_ui_size(root: tk.Tk, size: float) -> None:
    """Scale every font given in points (all of them here) by `size`. Before building widgets."""
    if size != 1.0:
        root.tk.call("tk", "scaling", float(root.tk.call("tk", "scaling")) * size)


def fit_to_screen(root: tk.Tk, width: int, height: int) -> None:
    """Open at `width` x `height`, or smaller when the screen is smaller."""
    w = max(WINDOW_MIN[0], min(width, root.winfo_screenwidth() - SCREEN_MARGIN[0]))
    h = max(WINDOW_MIN[1], min(height, root.winfo_screenheight() - SCREEN_MARGIN[1]))
    root.geometry(f"{w}x{h}")
    root.minsize(*WINDOW_MIN)


class ScrollArea(ttk.Frame):
    """A frame whose `body` is at least `need()` (width, height) in size; when the area is
    smaller than that, scroll bars appear. Larger, the body simply fills it."""

    def __init__(self, parent: tk.Misc, need: Callable[[], tuple[int, int]]) -> None:
        super().__init__(parent)
        self.need = need
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.xbar = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.ybar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.xbar.set, yscrollcommand=self.ybar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.body = ttk.Frame(self.canvas)
        # unsized until the area is on screen, as an unmapped window's widgets would be
        self._item = self.canvas.create_window(0, 0, anchor="nw", window=self.body, width=1, height=1)
        self._size: tuple[int, int, bool, bool] | None = None
        self._job: str | None = None
        self.canvas.bind("<Configure>", lambda _e: self.relayout())
        self.bind("<Destroy>", self._stop, add="+")
        self._job = self.after(RECHECK_MS, self._recheck)

    def relayout(self) -> None:
        """Size the body to the area or to what it needs, whichever is larger in each direction."""
        if not self.winfo_ismapped():
            return
        need_w, need_h = self.need()
        # the area without bars; a bar takes room from the other direction
        area_w, area_h = self.winfo_width(), self.winfo_height()
        bar_w, bar_h = self.ybar.winfo_reqwidth(), self.xbar.winfo_reqheight()
        show_y = need_h > area_h
        show_x = need_w > area_w - (bar_w if show_y else 0)
        show_y = need_h > area_h - (bar_h if show_x else 0)
        width = max(need_w, area_w - (bar_w if show_y else 0))
        height = max(need_h, area_h - (bar_h if show_x else 0))
        if self._size == (width, height, show_x, show_y):
            return
        self._size = (width, height, show_x, show_y)
        self.canvas.itemconfigure(self._item, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        for bar, show, row, column, sticky in ((self.ybar, show_y, 0, 1, "ns"), (self.xbar, show_x, 1, 0, "ew")):
            if show:
                bar.grid(row=row, column=column, sticky=sticky)
            else:
                bar.grid_remove()
        if not show_x:
            self.canvas.xview_moveto(0)
        if not show_y:
            self.canvas.yview_moveto(0)

    def wheel_scrolls(self, widget: tk.Misc) -> None:
        """The mouse wheel over `widget` or its children scrolls the area up and down (for
        panes with no wheel use of their own, such as the form)."""
        widget.bind("<MouseWheel>", self._wheel, add="+")
        for child in widget.winfo_children():
            self.wheel_scrolls(child)

    def _wheel(self, event: "tk.Event[tk.Misc]") -> None:
        if self._size is not None and self._size[3]:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def scrollbars(self) -> tuple[bool, bool]:
        """(horizontal, vertical) bar shown."""
        return bool(self.xbar.grid_info()), bool(self.ybar.grid_info())

    def _recheck(self) -> None:
        # what the content needs changes with the form (e.g. DXF or STEP rows)
        self.relayout()
        self._job = self.after(RECHECK_MS, self._recheck)

    def _stop(self, event: "tk.Event[tk.Misc]") -> None:
        if event.widget is self and self._job is not None:
            self.after_cancel(self._job)
            self._job = None
