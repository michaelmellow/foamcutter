"""Job file: loading and structural validation (docs/formats/job-file.md §1 to §5, §8).

Rules that need geometry or the machine profile are checked in `sections` and
`cam`, which report through the same ValidationError.
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dat import Point, read_dat
from .errors import ValidationError
from .fields import Reader, load_yaml, parse_yaml

WHAT = "job file"
RAPID = math.inf  # Job.channel_exit for channel_exit: "rapid"


@dataclass(frozen=True)
class Feature:
    name: str
    kind: str  # "hole" or "open"
    slit: str  # "upper" or "lower"
    points: tuple[Point, ...]  # mm, untwisted section frame
    channel: tuple[Point, ...] = ()  # §8 channel: the slit's path from points[0] outward; () = straight


@dataclass(frozen=True)
class Section:
    label: str  # "root" or "tip"
    shape: tuple[Point, ...]  # raw contour, chord fractions, as read
    chord: float
    twist: float  # degrees, positive = leading edge up
    twist_axis: float  # chord fraction
    x: float
    y: float
    z: float
    features: tuple[Feature, ...]


@dataclass(frozen=True)
class Block:
    x: float
    length: float
    height: float
    z: float | None = None  # bottom face; None = on the table (§5)

    def bottom(self, table_z: float) -> float:
        """G54 z of the bottom face: `z`, else the table top."""
        return table_z if self.z is None else self.z


@dataclass(frozen=True)
class Job:
    name: str
    kerf_table: str
    cut_speed: float
    direction: str
    max_segment_length: float
    min_te_thickness: float
    lead_in: float
    safe_position: tuple[float, float, float, float]
    mirror: bool
    block: Block
    root: Section
    tip: Section
    channel_exit: float = 0.0  # §10.4: 0 = at cut_speed, RAPID = G0, else mm/min at the faster face
    kerf_extension: tuple[tuple[float, float], ...] = ()  # §3: (speed, kerf), this job only, not measured


TOP_FIELDS = {
    "format", "version", "name", "kerf_table", "cut_speed", "direction", "max_segment_length",
    "min_te_thickness", "lead_in", "safe_position", "mirror", "block", "root", "tip",
}


def load_job(path: Path | str) -> Job:
    path = Path(path)
    return parse_job(load_yaml(path, WHAT), path.parent)


def load_job_text(text: str, base_dir: Path | str) -> Job:
    return parse_job(parse_yaml(text, WHAT), Path(base_dir))


def parse_job(data: Any, base_dir: Path) -> Job:
    r = Reader()
    top = r.mapping(data, "", TOP_FIELDS, frozenset({"channel_exit", "kerf_extension"}))
    if "format" in top and top["format"] != "foamcutter-job":
        r.problem("format", 'must be "foamcutter-job"')
    r.integer(top, "version", "", equal=1)

    safe: list[float] = []
    raw_safe = r.sequence(top, "safe_position", "")
    if "safe_position" in top and len(raw_safe) != 4:
        r.problem("safe_position", "must have exactly 4 numbers (axis 1 to axis 4)")
    for i, value in enumerate(raw_safe[:4]):
        safe.append(r.number_value(value, f"safe_position[{i}]"))
    safe += [0.0] * (4 - len(safe))

    block_data = r.mapping(top.get("block"), "block", {"x", "length", "height"}, frozenset({"z"})) if "block" in top else {}
    block = Block(
        r.number(block_data, "x", "block"),
        r.number(block_data, "length", "block", gt=0),
        r.number(block_data, "height", "block", gt=0),
        r.number(block_data, "z", "block") if "z" in block_data else None,
    )

    root = read_section(r, top.get("root"), "root", base_dir) if "root" in top else None
    tip = read_section(r, top.get("tip"), "tip", base_dir) if "tip" in top else None
    if root is not None and tip is not None:
        check_section_pair(r, root, tip)

    job = Job(
        name=r.text(top, "name", "", nonempty=True),
        kerf_table=r.text(top, "kerf_table", "", nonempty=True),
        cut_speed=r.number(top, "cut_speed", "", gt=0),
        direction=r.text(top, "direction", "", choices=("upper_first", "lower_first")),
        max_segment_length=r.number(top, "max_segment_length", "", gt=0),
        min_te_thickness=r.number(top, "min_te_thickness", "", ge=0),
        lead_in=r.number(top, "lead_in", "", gt=0),
        safe_position=(safe[0], safe[1], safe[2], safe[3]),
        mirror=r.boolean(top, "mirror", ""),
        block=block,
        root=root if root is not None else empty_section("root"),
        tip=tip if tip is not None else empty_section("tip"),
        channel_exit=read_channel_exit(r, top),
        kerf_extension=read_kerf_extension(r, top),
    )
    r.raise_if_problems(WHAT)
    return job


def read_kerf_extension(r: Reader, top: dict[str, Any]) -> tuple[tuple[float, float], ...]:
    """§3 kerf_extension: (speed, kerf) pairs, both > 0, speeds distinct. Whether they lie
    outside the table's range needs the profile: checked by the CAM's validation."""
    if "kerf_extension" not in top:
        return ()
    points: list[tuple[float, float]] = []
    for i, item in enumerate(r.sequence(top, "kerf_extension", "")):
        where = f"kerf_extension[{i}]"
        entry = r.mapping(item, where, {"speed", "kerf"})
        if "speed" in entry and "kerf" in entry:
            points.append((r.number(entry, "speed", where, gt=0), r.number(entry, "kerf", where, gt=0)))
    speeds = [s for s, _ in points]
    if len(set(speeds)) != len(speeds):
        r.problem("kerf_extension", "two entries have the same speed")
    return tuple(sorted(points))


def read_channel_exit(r: Reader, top: dict[str, Any]) -> float:
    """§3 channel_exit: "rapid", a speed > 0, or absent (0: at cut_speed)."""
    if "channel_exit" not in top:
        return 0.0
    value = top["channel_exit"]
    if value == "rapid":
        return RAPID
    if isinstance(value, str):
        r.problem("channel_exit", 'must be "rapid" or a number greater than 0')
        return 0.0
    return r.number_value(value, "channel_exit", gt=0)


def read_section(r: Reader, data: Any, label: str, base_dir: Path) -> Section:
    s = r.mapping(data, label, {"shape", "chord", "twist", "twist_axis", "position"}, frozenset({"features"}))

    shape: list[Point] = []
    if "shape" in s:
        shape_data = r.mapping(s["shape"], f"{label}.shape", set(), frozenset({"dat", "points"}))
        if len(shape_data) != 1:
            r.problem(f"{label}.shape", 'must have exactly one of "dat" or "points"')
        elif "dat" in shape_data:
            dat = r.text(shape_data, "dat", f"{label}.shape", nonempty=True)
            if dat:
                try:
                    shape = read_dat(base_dir / dat)
                except ValidationError as error:
                    for problem in error.problems:
                        r.problem(f"{label}.shape.dat", problem)
        elif "points" in shape_data:
            shape = r.points(shape_data, "points", f"{label}.shape", min_length=3)

    position = r.mapping(s.get("position"), f"{label}.position", {"x", "y", "z"}) if "position" in s else {}

    features: list[Feature] = []
    names: set[str] = set()
    for i, raw in enumerate(r.sequence(s, "features", label)):
        path = f"{label}.features[{i}]"
        f = r.mapping(raw, path, {"name", "kind", "slit", "points"}, frozenset({"channel"}))
        name = r.text(f, "name", path, nonempty=True)
        kind = r.text(f, "kind", path, choices=("hole", "open"))
        slit = r.text(f, "slit", path, choices=("upper", "lower"))
        points = r.points(f, "points", path, min_length=3 if kind == "hole" else 2)
        channel = r.points(f, "channel", path, min_length=1) if "channel" in f else []
        if name in names:
            r.problem(f"{path}.name", f'"{name}" is used by more than one feature')
        names.add(name)
        features.append(Feature(name, kind, slit, tuple(points), tuple(channel)))

    return Section(
        label=label,
        shape=tuple(shape),
        chord=r.number(s, "chord", label, gt=0),
        twist=r.number(s, "twist", label, ge=-45, le=45),
        twist_axis=r.number(s, "twist_axis", label, ge=0, le=1),
        x=r.number(position, "x", f"{label}.position"),
        y=r.number(position, "y", f"{label}.position"),
        z=r.number(position, "z", f"{label}.position"),
        features=tuple(features),
    )


def check_section_pair(r: Reader, root: Section, tip: Section) -> None:
    if root.y == tip.y:
        r.problem("tip.position.y", "must differ from root.position.y")
    root_features = {f.name: f for f in root.features}
    tip_features = {f.name: f for f in tip.features}
    for name in sorted(root_features.keys() ^ tip_features.keys()):
        missing = "tip" if name in root_features else "root"
        r.problem(f"{missing}.features", f'feature "{name}" must be listed in both sections')
    for name in sorted(root_features.keys() & tip_features.keys()):
        a, b = root_features[name], tip_features[name]
        if a.kind != b.kind:
            r.problem("tip.features", f'feature "{name}": kind differs from the root')
        if a.slit != b.slit:
            r.problem("tip.features", f'feature "{name}": slit differs from the root')
        if len(a.points) != len(b.points):
            r.problem("tip.features", f'feature "{name}": {len(b.points)} points at the tip, {len(a.points)} at the root')
        if len(a.channel) != len(b.channel):
            r.problem(
                "tip.features",
                f'feature "{name}": {len(b.channel)} channel points at the tip, {len(a.channel)} at the root',
            )


def empty_section(label: str) -> Section:
    return Section(label, (), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ())
