"""Machine profile: loading and validation (docs/formats/machine-profile.md)."""

from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from .errors import CamError
from .fields import Reader, load_yaml, parse_yaml

AXIS_LETTERS = ("X", "Y", "Z", "A", "B", "C", "U", "V", "W")


@dataclass(frozen=True)
class Axis:
    number: int  # 1 to 4
    letter: str
    min: float
    max: float
    max_rate: float
    lead: float

    @property
    def label(self) -> str:
        return f"axis {self.number} ({self.letter})"


@dataclass(frozen=True)
class KerfTable:
    name: str
    description: str
    points: tuple[tuple[float, float], ...]  # (speed mm/min, kerf mm), speeds increasing

    def kerf(self, speed: float) -> float:
        """Kerf at a wire speed, by the lookup rule of machine-profile.md §7."""
        first, last = self.points[0][0], self.points[-1][0]
        if not first <= speed <= last:
            raise CamError(
                f'kerf table "{self.name}": speed {speed:.1f} mm/min is outside the table range '
                f"{first:g} to {last:g} mm/min"
            )
        for (s0, k0), (s1, k1) in pairwise(self.points):
            if s0 <= speed <= s1:
                return k0 + (k1 - k0) * (speed - s0) / (s1 - s0)
        raise AssertionError("unreachable")


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


WHAT = "machine profile"


def load_profile(path: Path | str) -> Profile:
    return parse_profile(load_yaml(Path(path), WHAT))


def load_profile_text(text: str) -> Profile:
    return parse_profile(parse_yaml(text, WHAT))


def parse_profile(data: Any) -> Profile:
    r = Reader()
    top = r.mapping(data, "", {"format", "version", "name", "axes", "wire_span", "limits", "kerf_tables"})
    if "format" in top and top["format"] != "foamcutter-machine-profile":
        r.problem("format", 'must be "foamcutter-machine-profile"')
    r.integer(top, "version", "", equal=1)
    name = r.text(top, "name", "", nonempty=True)
    wire_span = r.number(top, "wire_span", "", gt=0)

    axes: list[Axis] = []
    raw_axes = r.sequence(top, "axes", "")
    if "axes" in top and len(raw_axes) != 4:
        r.problem("axes", "must have exactly 4 entries (axis 1 to axis 4)")
    for i, raw in enumerate(raw_axes[:4]):
        path = f"axes[{i}]"
        entry = r.mapping(raw, path, {"letter", "min", "max", "max_rate", "lead"})
        letter = r.text(entry, "letter", path, choices=AXIS_LETTERS)
        low = r.number(entry, "min", path)
        high = r.number(entry, "max", path)
        if "min" in entry and "max" in entry and not high > low:
            r.problem(f"{path}.max", "must be greater than min")
        axes.append(Axis(i + 1, letter, low, high, r.number(entry, "max_rate", path, gt=0), r.number(entry, "lead", path, gt=0)))
    letters = [a.letter for a in axes if a.letter]
    if len(set(letters)) != len(letters):
        r.problem("axes", "the four axis letters must all be different")

    limits = r.mapping(top.get("limits"), "limits", {"max_wire_angle", "max_speed_ratio"}) if "limits" in top else {}
    max_angle = r.number(limits, "max_wire_angle", "limits", ge=0, lt=90)
    max_ratio = r.number(limits, "max_speed_ratio", "limits", ge=1)

    tables: dict[str, KerfTable] = {}
    for i, raw in enumerate(r.sequence(top, "kerf_tables", "", min_length=1)):
        path = f"kerf_tables[{i}]"
        entry = r.mapping(raw, path, {"name", "description", "points"})
        table_name = r.text(entry, "name", path, nonempty=True)
        description = r.text(entry, "description", path)
        points: list[tuple[float, float]] = []
        for j, raw_point in enumerate(r.sequence(entry, "points", path, min_length=2)):
            point_path = f"{path}.points[{j}]"
            point = r.mapping(raw_point, point_path, {"speed", "kerf"})
            points.append((r.number(point, "speed", point_path, gt=0), r.number(point, "kerf", point_path, gt=0)))
        for j in range(1, len(points)):
            if not points[j][0] > points[j - 1][0]:
                r.problem(f"{path}.points[{j}].speed", "speeds must be strictly increasing")
        if table_name in tables:
            r.problem(f"{path}.name", f'"{table_name}" is used by more than one kerf table')
        if table_name and len(points) >= 2:
            tables[table_name] = KerfTable(table_name, description, tuple(points))

    r.raise_if_problems(WHAT)
    return Profile(name, (axes[0], axes[1], axes[2], axes[3]), wire_span, max_angle, max_ratio, tables)
