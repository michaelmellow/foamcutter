"""Machine profile: reading, validation and kerf lookup.

Format: docs/formats/machine-profile.md.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import InputError
from .fields import Checker

AXIS_LETTERS = ("X", "Y", "Z", "A", "B", "C", "U", "V", "W")


@dataclass(frozen=True)
class Axis:
    letter: str
    min: float
    max: float
    max_rate: float
    lead: float


@dataclass(frozen=True)
class KerfTable:
    name: str
    description: str
    points: tuple[tuple[float, float], ...]  # (speed mm/min, kerf mm)

    @property
    def max_kerf(self) -> float:
        return max(k for _, k in self.points)


@dataclass(frozen=True)
class Profile:
    name: str
    axes: tuple[Axis, Axis, Axis, Axis]
    wire_span: float
    max_wire_angle: float
    max_speed_ratio: float
    kerf_tables: dict[str, KerfTable]

    @property
    def letters(self) -> tuple[str, str, str, str]:
        a = self.axes
        return (a[0].letter, a[1].letter, a[2].letter, a[3].letter)


def load_profile(path: Path | str) -> Profile:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InputError(f"{path}: cannot read machine profile: {exc}") from exc
    try:
        return parse_profile(data)
    except InputError as exc:
        raise InputError([f"{path}: {m}" for m in exc.messages]) from exc


def parse_profile(data: Any) -> Profile:
    c = Checker()
    top = c.mapping(
        data,
        "",
        ("format", "version", "name", "axes", "wire_span", "limits", "kerf_tables"),
    )
    if top is None:
        c.raise_if_any()
        raise AssertionError  # unreachable
    fmt = c.text(top, "format", "")
    if fmt is not None and fmt != "foamcutter-machine-profile":
        c.error("format", 'must be "foamcutter-machine-profile"')
    version = c.integer(top, "version", "")
    if version is not None and version != 1:
        c.error("version", "must be 1")
    name = c.text(top, "name", "")
    span = c.number(top, "wire_span", "", lambda v: v > 0, "> 0")

    axes: list[Axis] = []
    raw_axes = c.items(top, "axes", "")
    if raw_axes is not None:
        if len(raw_axes) != 4:
            c.error("axes", "must have exactly 4 entries")
        for i, entry in enumerate(raw_axes[:4]):
            p = f"axes[{i}]"
            m = c.mapping(entry, p, ("letter", "min", "max", "max_rate", "lead"))
            letter = c.text(m, "letter", p, choices=AXIS_LETTERS)
            lo = c.number(m, "min", p)
            hi = c.number(m, "max", p)
            if lo is not None and hi is not None and not hi > lo:
                c.error(f"{p}.max", "must be > min")
                hi = None
            rate = c.number(m, "max_rate", p, lambda v: v > 0, "> 0")
            lead = c.number(m, "lead", p, lambda v: v > 0, "> 0")
            if None not in (letter, lo, hi, rate, lead):
                axes.append(Axis(str(letter), float(lo or 0), float(hi or 0), float(rate or 0),
                                 float(lead or 0)))
        letters = [a.get("letter") for a in raw_axes if isinstance(a, dict)]
        for i, letter in enumerate(letters):
            if letter in letters[:i]:
                c.error(f"axes[{i}].letter", f'"{letter}" is used by another axis')

    limits = c.mapping(top.get("limits"), "limits", ("max_wire_angle", "max_speed_ratio")) \
        if "limits" in top else None
    angle = c.number(limits, "max_wire_angle", "limits", lambda v: 0 <= v < 90, ">= 0 and < 90")
    ratio = c.number(limits, "max_speed_ratio", "limits", lambda v: v >= 1, ">= 1")

    tables: dict[str, KerfTable] = {}
    raw_tables = c.items(top, "kerf_tables", "")
    if raw_tables is not None:
        if not raw_tables:
            c.error("kerf_tables", "must have at least one table")
        for i, entry in enumerate(raw_tables):
            table = _parse_table(c, entry, f"kerf_tables[{i}]")
            if table is not None:
                if table.name in tables:
                    c.error(f"kerf_tables[{i}].name", f'"{table.name}" is not unique')
                tables[table.name] = table

    c.raise_if_any()
    assert name is not None and span is not None and angle is not None and ratio is not None
    return Profile(
        name=name,
        axes=(axes[0], axes[1], axes[2], axes[3]),
        wire_span=span,
        max_wire_angle=angle,
        max_speed_ratio=ratio,
        kerf_tables=tables,
    )


def _parse_table(c: Checker, entry: Any, p: str) -> KerfTable | None:
    m = c.mapping(entry, p, ("name", "description", "points"))
    name = c.text(m, "name", p)
    description = c.text(m, "description", p, allow_empty=True)
    raw = c.items(m, "points", p)
    points: list[tuple[float, float]] = []
    ok = True
    if raw is not None:
        if len(raw) < 2:
            c.error(f"{p}.points", "must have at least 2 entries")
            ok = False
        for j, point in enumerate(raw):
            pp = f"{p}.points[{j}]"
            pm = c.mapping(point, pp, ("speed", "kerf"))
            speed = c.number(pm, "speed", pp, lambda v: v > 0, "> 0")
            kerf = c.number(pm, "kerf", pp, lambda v: v > 0, "> 0")
            if speed is None or kerf is None:
                ok = False
                continue
            if points and not speed > points[-1][0]:
                c.error(f"{pp}.speed", "speeds must be strictly increasing down the list")
                ok = False
            points.append((speed, kerf))
    if name is None or description is None or raw is None or not ok:
        return None
    return KerfTable(name, description, tuple(points))


def kerf_lookup(table: KerfTable, speed: float) -> float:
    """Kerf at `speed` by machine profile section 7. No extrapolation."""
    pts = table.points
    for s, k in pts:
        if speed == s:
            return k
    for (s0, k0), (s1, k1) in itertools.pairwise(pts):
        if s0 < speed < s1:
            return k0 + (k1 - k0) * (speed - s0) / (s1 - s0)
    raise InputError(
        f'kerf table "{table.name}": speed {speed:.1f} mm/min is outside the table range '
        f"{pts[0][0]:.1f} to {pts[-1][0]:.1f} mm/min"
    )
