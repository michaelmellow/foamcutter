"""Tests for fitting the GUI on small or highly scaled screens (uiscale.py)."""

import tkinter as tk
from collections.abc import Iterator

import pytest

from foamdxf.uiscale import (
    WINDOW_MIN,
    ScrollArea,
    apply_ui_size,
    fit_to_screen,
    parse_ui_size,
)


@pytest.fixture
def root() -> Iterator[tk.Tk]:
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tkinter")
    yield r
    r.destroy()


@pytest.mark.parametrize(("value", "size"), [
    (0.7, 0.7), ("0.8", 0.8), ("70%", 0.7), ("1", 1.0),
    ("big", 1.0), (0.1, 1.0), (5, 1.0), (None, 1.0),
])
def test_ui_size_is_parsed_or_falls_back_to_full_size(value: object, size: float) -> None:
    assert parse_ui_size(value) == pytest.approx(size)


def test_ui_size_scales_the_fonts(root: tk.Tk) -> None:
    before = float(root.tk.call("tk", "scaling"))
    apply_ui_size(root, 0.6)
    assert float(root.tk.call("tk", "scaling")) == pytest.approx(before * 0.6, rel=0.01)  # Tk rounds it to whole dots per inch


def test_window_opens_no_larger_than_the_screen(root: tk.Tk) -> None:
    fit_to_screen(root, 100_000, 100_000)
    root.update_idletasks()
    w, h = (int(v) for v in root.geometry().split("+")[0].split("x"))
    assert w <= root.winfo_screenwidth() and h <= root.winfo_screenheight()
    assert (w, h) >= WINDOW_MIN
    fit_to_screen(root, 700, 450)
    root.update_idletasks()
    assert root.geometry().startswith("700x450")


def test_scroll_bars_appear_only_when_the_window_is_too_small(root: tk.Tk) -> None:
    need = [(500, 400)]
    area = ScrollArea(root, lambda: need[0])
    area.pack(fill=tk.BOTH, expand=True)
    root.geometry("800x600")
    root.update()
    area.relayout()
    assert area.scrollbars() == (False, False)
    assert area.body.winfo_width() >= 500 and area.body.winfo_height() >= 400  # fills the window

    need[0] = (500, 900)  # taller than the window: up and down only
    area.relayout()
    root.update()
    assert area.scrollbars() == (False, True)
    assert area.body.winfo_height() == 900

    need[0] = (1500, 900)
    area.relayout()
    root.update()
    assert area.scrollbars() == (True, True)
    assert (area.body.winfo_width(), area.body.winfo_height()) == (1500, 900)
