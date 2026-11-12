"""Job file: reading and field validation (job file format sections 1 to 5, 8).

Geometric rules that need the machine profile or the target contours are
checked in `target.build_targets`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .dat import read_dat
from .errors import InputError
from .fields import Checker

TOP_KEYS = (
    "format", "version", "name", "kerf_table", "cut_speed", "direction",
    "max_segment_length", "min_te_thickness", "lead_in", "safe_position",
    "mirror", "block", "root", "tip",
)
SECTION_KEYS = ("shape", "chord", "twist", "twist_axis", "position")


@dataclass(frozen=True)
class Feature:
    name: str
    kind: str  # "hole" or "open"
    slit: str  # "upper" or "lower"
    points: tuple[tuple[float, float], ...]  # mm, untwisted section frame


@dataclass(frozen=True)
class Section:
    label: str  # "root" or "tip"
    raw: tuple[tuple[float, float], ...]  # shape points as read
    chord: float
    twist: float
    twist_axis: float
    x: float
    y: float
    z: float
    features: tuple[Feature, ...]


@dataclass(frozen=True)
class Block:
    x: float
    length: float
    height: float


@dataclass(frozen=True)
class Job:
    name: str
    kerf_table: str
    cut_speed: float
    direction: str
    min_te_thickness: float
    lead_in: float
    safe_position: tuple[float, float, float, float]
    mirror: bool
    block: Block
    root: Section
    tip: Section

    @property
    def sections(self) -> tuple[Section, Section]:
        return (self.root, self.tip)


def load_job(path: Path | str) -> Job:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InputError(f"{path}: cannot read job file: {exc}") from exc
    try:
        return parse_job(data, path.parent)
    except InputError as exc:
        raise InputError([f"{path}: {m}" for m in exc.messages]) from exc


def parse_job(data: Any, base_dir: Path) -> Job:
    """Validate `data` (the loaded YAML). `.dat` paths are relative to `base_dir`."""
    c = Checker()
    top = c.mapping(data, "", TOP_KEYS)
    if top is None:
        c.raise_if_any()
        raise AssertionError  # unreachable
    fmt = c.text(top, "format", "")
    if fmt is not None and fmt != "foamcutter-job":
        c.error("format", 'must be "foamcutter-job"')
    version = c.integer(top, "version", "")
    if version is not None and version != 1:
        c.error("version", "must be 1")
    name = c.text(top, "name", "")
    kerf_table = c.text(top, "kerf_table", "")
    cut_speed = c.number(top, "cut_speed", "", lambda v: v > 0, "> 0")
    direction = c.text(top, "direction", "", choices=("upper_first", "lower_first"))
    c.number(top, "max_segment_length", "", lambda v: v > 0, "> 0")
    min_te = c.number(top, "min_te_thickness", "", lambda v: v >= 0, ">= 0")
    lead_in = c.number(top, "lead_in", "", lambda v: v > 0, "> 0")
    mirror = c.boolean(top, "mirror", "")

    safe: list[float] = []
    raw_safe = c.items(top, "safe_position", "")
    if raw_safe is not None:
        if len(raw_safe) != 4:
            c.error("safe_position", "must hold 4 numbers")
        for i, v in enumerate(raw_safe):
            n = c.value_number(v, f"safe_position[{i}]")
            if n is not None:
                safe.append(n)

    block = None
    b = c.mapping(top.get("block"), "block", ("x", "length", "height")) if "block" in top else None
    bx = c.number(b, "x", "block")
    bl = c.number(b, "length", "block", lambda v: v > 0, "> 0")
    bh = c.number(b, "height", "block", lambda v: v > 0, "> 0")
    if bx is not None and bl is not None and bh is not None:
        block = Block(bx, bl, bh)

    sections = {
        label: _parse_section(c, top.get(label), label, base_dir)
        for label in ("root", "tip")
        if label in top
    }
    root, tip = sections.get("root"), sections.get("tip")
    root_y, tip_y = _plane_y(top, "root"), _plane_y(top, "tip")
    if root_y is not None and root_y == tip_y:
        c.error("tip.position.y", "must differ from root.position.y")
    if root is not None and tip is not None:
        _match_features(c, root, tip)

    c.raise_if_any()
    assert name is not None and kerf_table is not None and cut_speed is not None
    assert direction is not None and min_te is not None and lead_in is not None
    assert mirror is not None and block is not None and root is not None and tip is not None
    return Job(
        name=name,
        kerf_table=kerf_table,
        cut_speed=cut_speed,
        direction=direction,
        min_te_thickness=min_te,
        lead_in=lead_in,
        safe_position=(safe[0], safe[1], safe[2], safe[3]),
        mirror=mirror,
        block=block,
        root=root,
        tip=tip,
    )


def _parse_section(c: Checker, data: Any, label: str, base_dir: Path) -> Section | None:
    m = c.mapping(data, label, SECTION_KEYS, ("features",))
    if m is None:
        return None
    errors_before = len(c.errors)
    raw: list[tuple[float, float]] = []
    shape = c.mapping(m.get("shape"), f"{label}.shape", (), ("dat", "points")) \
        if "shape" in m else None
    if shape is not None:
        if ("dat" in shape) == ("points" in shape):
            c.error(f"{label}.shape", "must hold exactly one of dat or points")
        elif "dat" in shape:
            path = c.text(shape, "dat", f"{label}.shape")
            if path is not None:
                try:
                    raw = read_dat(base_dir / path)
                except InputError as exc:
                    for msg in exc.messages:
                        c.error(f"{label}.shape.dat", msg)
        else:
            pts = _pairs(c, shape.get("points"), f"{label}.shape.points")
            if pts is not None:
                if len(pts) < 3:
                    c.error(f"{label}.shape.points", "must hold at least 3 points")
                raw = pts
    chord = c.number(m, "chord", label, lambda v: v > 0, "> 0")
    twist = c.number(m, "twist", label, lambda v: -45 <= v <= 45, "between -45 and 45")
    axis = c.number(m, "twist_axis", label, lambda v: 0 <= v <= 1, "between 0 and 1")
    pos = c.mapping(m.get("position"), f"{label}.position", ("x", "y", "z")) \
        if "position" in m else None
    x = c.number(pos, "x", f"{label}.position")
    y = c.number(pos, "y", f"{label}.position")
    z = c.number(pos, "z", f"{label}.position")

    features: list[Feature] = []
    raw_features = c.items(m, "features", label)
    for i, f in enumerate(raw_features or []):
        p = f"{label}.features[{i}]"
        fm = c.mapping(f, p, ("name", "kind", "slit", "points"))
        fname = c.text(fm, "name", p)
        kind = c.text(fm, "kind", p, choices=("hole", "open"))
        slit = c.text(fm, "slit", p, choices=("upper", "lower"))
        fpts = _pairs(c, fm.get("points") if fm else None, f"{p}.points")
        if fname is not None and any(g.name == fname for g in features):
            c.error(f"{p}.name", f'"{fname}" is not unique in {label}')
        if kind is not None and fpts is not None:
            need = 3 if kind == "hole" else 2
            if len(fpts) < need:
                c.error(f"{p}.points", f"a {kind} needs at least {need} points")
        if None not in (fname, kind, slit, fpts):
            features.append(Feature(str(fname), str(kind), str(slit), tuple(fpts or ())))

    if len(c.errors) > errors_before:
        return None
    assert chord is not None and twist is not None and axis is not None
    assert x is not None and y is not None and z is not None
    return Section(label, tuple(raw), chord, twist, axis, x, y, z, tuple(features))


def _plane_y(top: dict[str, Any], label: str) -> Any:
    section = top.get(label)
    position = section.get("position") if isinstance(section, dict) else None
    return position.get("y") if isinstance(position, dict) else None


def _pairs(c: Checker, value: Any, path: str) -> list[tuple[float, float]] | None:
    if not isinstance(value, list):
        c.error(path, "must be a list of [u, v] pairs")
        return None
    out: list[tuple[float, float]] = []
    for i, pair in enumerate(value):
        if not isinstance(pair, list) or len(pair) != 2:
            c.error(f"{path}[{i}]", "must be a [u, v] pair")
            return None
        u = c.value_number(pair[0], f"{path}[{i}][0]")
        v = c.value_number(pair[1], f"{path}[{i}][1]")
        if u is None or v is None:
            return None
        out.append((u, v))
    return out


def _match_features(c: Checker, root: Section, tip: Section) -> None:
    by_name = {f.name: f for f in tip.features}
    for i, f in enumerate(root.features):
        g = by_name.get(f.name)
        p = f"root.features[{i}]"
        if g is None:
            c.error(f"{p}.name", f'"{f.name}" does not appear in tip')
            continue
        if f.kind != g.kind:
            c.error(f"{p}.kind", f'differs from the tip feature "{f.name}"')
        if f.slit != g.slit:
            c.error(f"{p}.slit", f'differs from the tip feature "{f.name}"')
        if len(f.points) != len(g.points):
            c.error(f"{p}.points", f'count differs from the tip feature "{f.name}"')
    names = {f.name for f in root.features}
    for i, g in enumerate(tip.features):
        if g.name not in names:
            c.error(f"tip.features[{i}].name", f'"{g.name}" does not appear in root')
