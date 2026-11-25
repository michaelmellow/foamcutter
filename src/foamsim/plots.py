"""2D overlay of target against simulated sections, and a 3D animation of the
cut (Brief 5 task 6). Uses matplotlib without a display."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from .geom import Arr
from .sim import SimResult


def _closed(points: Arr) -> Arr:
    return np.vstack([points, points[:1]])


def write_overlay(result: SimResult, path: Path | str) -> None:
    """One row per section: shapes on the left, deviation along the target on
    the right. Operator planes get a row with the wire trace only."""
    rows = len(result.sections) + len(result.plane_traces)
    fig = Figure(figsize=(14, 3.2 * max(rows, 1)))
    FigureCanvasAgg(fig)
    row = 0
    for name, section in result.sections.items():
        target = result.targets[name]
        ax = fig.add_subplot(rows, 2, 2 * row + 1)
        ax.plot(*section.trace.T, color="0.75", lw=0.8, label="wire centre")
        ax.plot(*_closed(target.outer).T, color="black", lw=1.0, label="target")
        for hole in target.holes.values():
            ax.plot(*_closed(hole).T, color="black", lw=1.0)
        for line in target.opens.values():
            ax.plot(*line.T, color="black", lw=1.0)
        first = True
        for element in section.elements.values():
            for chain in element.surface:
                ax.plot(*chain.T, color="tab:red", lw=0.8, ls="--",
                        label="simulated surface" if first else None)
                first = False
            if element.worst is not None and math.isfinite(element.max_deviation):
                ax.plot(*element.worst, "o", color="tab:red", ms=4)
        state = "PASS" if section.passed else "FAIL"
        title = f"{name} (y = {section.plane_y:g}): {state}"
        if section.error:
            title += f"\n{section.error}"
        else:
            title += f", max deviation {section.max_deviation:.4f} mm"
        ax.set_title(title, fontsize=9)
        ax.set_aspect("equal")
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("z (mm)")
        ax.legend(fontsize=7, loc="best")

        dev = fig.add_subplot(rows, 2, 2 * row + 2)
        for key, element in section.elements.items():
            if len(element.profile):
                dev.plot(*element.profile.T, lw=0.8, label=key)
        dev.axhline(result.tolerance, color="tab:red", lw=0.8, ls=":", label="tolerance")
        dev.set_xlabel("distance along target (mm)")
        dev.set_ylabel("target to simulated surface (mm)")
        dev.set_title(f"{name}: deviation along the target", fontsize=9)
        dev.legend(fontsize=7, loc="best")
        row += 1
    for y, points in result.plane_traces.items():
        ax = fig.add_subplot(rows, 2, 2 * row + 1)
        ax.plot(*points.T, color="tab:blue", lw=0.8)
        ax.set_title(f"operator plane y = {y:g}: wire centre", fontsize=9)
        ax.set_aspect("equal")
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("z (mm)")
        row += 1
    fig.tight_layout()
    fig.savefig(str(path), dpi=110)


def write_animation(result: SimResult, path: Path | str, max_frames: int = 150) -> None:
    """Animated GIF of the wire moving through the block."""
    program = result.program
    if program is None:
        raise ValueError("no program to animate")
    span = result.profile.wire_span
    positions = np.array(
        [program.motions[0].start] + [m.end for m in program.motions if m.kind != "dwell"]
    )
    feed_flags = [False] + [m.kind == "feed" for m in program.motions if m.kind != "dwell"]
    step = max(1, math.ceil(len(positions) / max_frames))
    frames = list(range(0, len(positions), step))
    if frames[-1] != len(positions) - 1:
        frames.append(len(positions) - 1)

    fig = Figure(figsize=(7, 5.5))
    FigureCanvasAgg(fig)
    ax: Any = fig.add_subplot(projection="3d")  # mplot3d has no type information
    b = result.job.block
    ys = sorted(t.y for t in result.targets.values())
    _box(ax, (b.x, b.x + b.length), (ys[0], ys[1]), (b.z, b.z + b.height))
    for target in result.targets.values():
        outer = _closed(target.outer)
        ax.plot(outer[:, 0], np.full(len(outer), target.y), outer[:, 1], color="black", lw=0.6)
    xs = np.concatenate([positions[:, 0], positions[:, 2]])
    zs = np.concatenate([positions[:, 1], positions[:, 3]])
    ax.set_xlim(float(min(xs.min(), b.x)), float(max(xs.max(), b.x + b.length)))
    ax.set_ylim(0.0, span)
    ax.set_zlim(0.0, float(max(zs.max(), b.height)))
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_zlabel("z")
    wire = ax.plot([], [], [], color="tab:orange", lw=1.5)[0]
    paths = {
        name: ax.plot([], [], [], color="tab:red", lw=0.8)[0] for name in result.targets
    }
    title = ax.set_title("")

    def draw(frame: int) -> list[Any]:
        a = positions[frame]
        wire.set_data_3d([a[0], a[2]], [0.0, span], [a[1], a[3]])
        upto = positions[: frame + 1][np.array(feed_flags[: frame + 1])]
        for name, artist in paths.items():
            y = result.targets[name].y
            s = y / span
            px = upto[:, 0] + s * (upto[:, 2] - upto[:, 0])
            pz = upto[:, 1] + s * (upto[:, 3] - upto[:, 1])
            artist.set_data_3d(px, np.full(len(px), y), pz)
        title.set_text(f"{result.job.name}: move {frame} of {len(positions) - 1}")
        return [wire, *paths.values(), title]

    animation = FuncAnimation(fig, draw, frames=frames, blit=False)
    animation.save(str(path), writer=PillowWriter(fps=15))


def _box(ax: Any, xr: tuple[float, float], yr: tuple[float, float],
         zr: tuple[float, float]) -> None:
    for y in yr:
        for z in zr:
            ax.plot(list(xr), [y, y], [z, z], color="tab:blue", lw=0.5)
        for x in xr:
            ax.plot([x, x], [y, y], list(zr), color="tab:blue", lw=0.5)
    for x in xr:
        for z in zr:
            ax.plot([x, x], list(yr), [z, z], color="tab:blue", lw=0.5)
