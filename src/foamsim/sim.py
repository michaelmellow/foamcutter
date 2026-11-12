"""Run the simulation: parse, trace the wire, compare, check (Brief 5 tasks 2 to 7)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .compare import SectionResult, compare_section
from .errors import GCodeError, InputError
from .gcode import Motion, Program, parse_gcode
from .geom import Arr, inside
from .job import Job
from .profile import Profile
from .target import SectionTarget, build_targets
from .wire import sweep_hits_block, trace, wire_angle, wire_in_box

DEFAULT_TOLERANCE = 0.05  # mm, Brief 5 acceptance


@dataclass
class Check:
    name: str
    passed: bool
    messages: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class SimResult:
    passed: bool
    tolerance: float
    profile: Profile
    job: Job
    targets: dict[str, SectionTarget]
    error: str | None = None
    program: Program | None = None
    sections: dict[str, SectionResult] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    feed_time: float = 0.0
    dwell_time: float = 0.0
    rapid_time_estimate: float = 0.0
    face_speeds: dict[str, tuple[float, float]] = field(default_factory=dict)
    lead_ratios: tuple[float, float] = (math.nan, math.nan)
    plane_traces: dict[float, Arr] = field(default_factory=dict)

    @property
    def total_time(self) -> float:
        return self.feed_time + self.dwell_time + self.rapid_time_estimate

    @property
    def failed_sections(self) -> list[str]:
        return [n for n, s in self.sections.items() if not s.passed]

    def summary(self) -> str:
        lines = [f"RESULT: {'PASS' if self.passed else 'FAIL'} (tolerance {self.tolerance} mm)"]
        if self.error:
            lines.append(f"error: {self.error}")
        for name, s in self.sections.items():
            state = "pass" if s.passed else "FAIL"
            if s.error:
                lines.append(f"section {name} (y = {s.plane_y:g}): FAIL: {s.error}")
                continue
            lines.append(
                f"section {name} (y = {s.plane_y:g}): {state}, max deviation "
                f"{s.max_deviation:.4f} mm"
            )
            for key, e in s.elements.items():
                where = f" at ({e.worst[0]:.3f}, {e.worst[1]:.3f})" if e.worst else ""
                lines.append(
                    f"  {key}: {e.max_deviation:.4f} mm ({e.worst_kind}{where}), "
                    f"{e.edges} edges, inside-corner rounding {e.inside_corner_rounding:.3f} mm"
                )
            lines += [f"  problem: {p}" for p in s.problems]
        for c in self.checks:
            lines.append(f"check {c.name}: {'pass' if c.passed else 'FAIL'}")
            lines += [f"  {m}" for m in c.messages[:20]]
            if len(c.messages) > 20:
                lines.append(f"  ... {len(c.messages) - 20} more")
            lines += [f"  note: {n}" for n in c.notes]
        for name, (lo, hi) in self.face_speeds.items():
            lines.append(f"wire speed at {name} face: {lo:.1f} to {hi:.1f} mm/min")
        lines.append(
            f"lead-in / exit speed ratio: {self.lead_ratios[0]:.3f} / {self.lead_ratios[1]:.3f}"
        )
        lines.append(
            f"time: feed {self.feed_time:.3f} min, dwell {self.dwell_time:.3f} min, rapids "
            f"about {self.rapid_time_estimate:.3f} min, total about {self.total_time:.3f} min"
        )
        lines += [f"warning: {w}" for w in self.warnings]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        def num(v: float) -> float | str:
            return v if math.isfinite(v) else str(v)

        sections: dict[str, Any] = {}
        for name, s in self.sections.items():
            sections[name] = {
                "plane_y": s.plane_y,
                "passed": s.passed,
                "max_deviation_mm": num(s.max_deviation),
                "error": s.error,
                "problems": s.problems,
                "elements": {
                    key: {
                        "passed": e.passed,
                        "max_deviation_mm": num(e.max_deviation),
                        "worst_point_xz": list(e.worst) if e.worst else None,
                        "worst_kind": e.worst_kind,
                        "inside_corner_rounding_mm": e.inside_corner_rounding,
                        "edges": e.edges,
                    }
                    for key, e in s.elements.items()
                },
            }
        return {
            "format": "foamsim-report",
            "version": 1,
            "result": "pass" if self.passed else "fail",
            "tolerance_mm": self.tolerance,
            "job": self.job.name,
            "profile": self.profile.name,
            "error": self.error,
            "failed_sections": self.failed_sections,
            "sections": sections,
            "checks": [
                {"name": c.name, "passed": c.passed, "messages": c.messages, "notes": c.notes}
                for c in self.checks
            ],
            "face_speeds_mm_min": {
                n: {"min": lo, "max": hi} for n, (lo, hi) in self.face_speeds.items()
            },
            "lead_in_exit_speed_ratio": [num(r) for r in self.lead_ratios],
            "time": {
                "feed_min": self.feed_time,
                "dwell_min": self.dwell_time,
                "rapid_estimate_min": self.rapid_time_estimate,
                "total_estimate_min": self.total_time,
            },
            "planes": {
                f"{y:g}": [[float(x), float(z)] for x, z in pts]
                for y, pts in self.plane_traces.items()
            },
            "warnings": self.warnings,
        }


def _ratio(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    if hi == 0:
        return 1.0
    return math.inf if lo == 0 else hi / lo


def simulate(
    gcode: str,
    profile: Profile,
    job: Job,
    planes: tuple[float, ...] = (),
    tolerance: float = DEFAULT_TOLERANCE,
) -> SimResult:
    """Simulate `gcode` for `job` on `profile`.

    Raises InputError for an invalid job, profile or plane. A program that
    cannot be read gives a failed result with `error` set."""
    targets = build_targets(job, profile)
    span = profile.wire_span
    for y in planes:
        if not 0 <= y <= span:
            raise InputError(f"plane y = {y:g} lies outside the carriage planes 0 to {span:g}")
    result = SimResult(False, tolerance, profile, job, targets)
    try:
        program = parse_gcode(gcode, profile.letters)
    except GCodeError as exc:
        result.error = str(exc)
        return result
    result.program = program
    feeds = [m for m in program.motions if m.kind == "feed"]
    if len(feeds) < 3:
        result.error = "the program has fewer than 3 feed moves (lead-in, cut, exit)"
        return result

    positions = np.array([feeds[0].start] + [m.end for m in feeds], dtype=np.float64)
    durations = [m.duration or 0.0 for m in feeds]
    lines = [m.line for m in feeds]
    table = profile.kerf_tables[job.kerf_table]

    traces: dict[str, Arr] = {}
    for name, target in targets.items():
        traces[name] = trace(positions, span, target.y)
        result.sections[name] = compare_section(
            target, traces[name], lines, durations, table, tolerance
        )
    for y in planes:
        result.plane_traces[y] = trace(positions, span, y)

    # Face distances per feed move.
    dist = {n: np.hypot(*np.diff(t, axis=0).T) for n, t in traces.items()}
    contour = range(1, len(feeds) - 1)
    for n in targets:
        speeds = [dist[n][i] / durations[i] for i in contour if durations[i] > 0]
        result.face_speeds[n] = (min(speeds), max(speeds)) if speeds else (0.0, 0.0)
    d_root, d_tip = dist["root"], dist["tip"]
    result.lead_ratios = (_ratio(d_root[0], d_tip[0]), _ratio(d_root[-1], d_tip[-1]))

    result.checks = [
        _soft_limits(program.motions, profile),
        _wire_angle(program.motions, profile),
        _axis_rate(feeds, profile),
        _speed_ratio(feeds, d_root, d_tip, profile),
        _cut_speed(feeds, d_root, d_tip, job),
        _start_end(feeds, program.motions, profile, job, targets),
        _rapids(program.motions, profile, job, targets),
        _lead_in_exit(feeds, traces, targets, table.max_kerf),
    ]

    result.feed_time = sum(durations)
    result.dwell_time = sum(m.duration or 0.0 for m in program.motions if m.kind == "dwell")
    result.rapid_time_estimate = sum(
        max(abs(e - s) / a.max_rate for s, e, a in zip(m.start, m.end, profile.axes, strict=True))
        for m in program.motions
        if m.kind == "rapid" and m.start_known
    )
    if program.final_feed_mode == "G93":
        result.warnings.append("the program ends in G93 inverse-time mode; G94 is expected")
    if program.motions and not program.motions[0].start_known:
        result.warnings.append(
            f"line {program.motions[0].line}: the move to the first position starts from "
            "wherever the wire is; the operator must make sure that path is clear "
            "(job file section 10.6)"
        )

    result.passed = (
        all(s.passed for s in result.sections.values()) and all(c.passed for c in result.checks)
    )
    return result


def _axis_name(i: int, profile: Profile) -> str:
    return f"axis {i + 1} ({profile.axes[i].letter})"


def _soft_limits(motions: list[Motion], profile: Profile) -> Check:
    check = Check("soft_limits", True)
    for m in motions:
        if m.kind == "dwell":
            continue
        for i, (v, a) in enumerate(zip(m.end, profile.axes, strict=True)):
            if v < a.min:
                check.messages.append(
                    f"line {m.line}: {_axis_name(i, profile)} at {v:.4f} is below min "
                    f"{a.min:g} by {a.min - v:.3f} mm"
                )
            elif v > a.max:
                check.messages.append(
                    f"line {m.line}: {_axis_name(i, profile)} at {v:.4f} is above max "
                    f"{a.max:g} by {v - a.max:.3f} mm"
                )
    check.passed = not check.messages
    return check


def _wire_angle(motions: list[Motion], profile: Profile) -> Check:
    check = Check("wire_angle", True)
    for m in motions:
        if m.kind == "dwell":
            continue
        angle = wire_angle(m.end, profile.wire_span)
        if angle > profile.max_wire_angle:
            check.messages.append(
                f"line {m.line}: wire angle {angle:.3f} deg exceeds max_wire_angle "
                f"{profile.max_wire_angle:g}"
            )
    check.passed = not check.messages
    return check


def _axis_rate(feeds: list[Motion], profile: Profile) -> Check:
    check = Check("axis_rate", True)
    for m in feeds:
        t = m.duration or 0.0
        for i, (s, e, a) in enumerate(zip(m.start, m.end, profile.axes, strict=True)):
            if s == e:
                continue
            rate = abs(e - s) / t if t > 0 else math.inf
            if rate > a.max_rate * (1 + 1e-9):
                check.messages.append(
                    f"line {m.line}: {_axis_name(i, profile)} moves at {rate:.1f} mm/min, "
                    f"above max_rate {a.max_rate:g}"
                )
    check.passed = not check.messages
    return check


def _speed_ratio(feeds: list[Motion], d_root: Arr, d_tip: Arr, profile: Profile) -> Check:
    check = Check(
        "speed_ratio", True,
        notes=["the lead-in and exit (first and last feed moves) are exempt (job file 10.5)"],
    )
    for i in range(1, len(feeds) - 1):
        ratio = _ratio(float(d_root[i]), float(d_tip[i]))
        if ratio > profile.max_speed_ratio * (1 + 1e-9):
            check.messages.append(
                f"line {feeds[i].line}: foam-face speed ratio {ratio:.3f} exceeds "
                f"max_speed_ratio {profile.max_speed_ratio:g}"
            )
    check.passed = not check.messages
    return check


def _cut_speed(feeds: list[Motion], d_root: Arr, d_tip: Arr, job: Job) -> Check:
    check = Check(
        "cut_speed", True,
        notes=["tolerance 0.001 mm + 0.1 % of the move length, for 4-decimal positions"],
    )
    for i, m in enumerate(feeds):
        t = m.duration or 0.0
        fast = max(float(d_root[i]), float(d_tip[i]))
        if fast == 0:
            continue
        if abs(fast - job.cut_speed * t) > 1e-3 + 1e-3 * fast:
            check.messages.append(
                f"line {m.line}: faster foam face moves at {fast / t:.2f} mm/min, "
                f"job cut_speed is {job.cut_speed:g}"
            )
    check.passed = not check.messages
    return check


def _block_box(job: Job, targets: dict[str, SectionTarget]) -> tuple[float, ...]:
    b = job.block
    ys = [t.y for t in targets.values()]
    return (min(ys), max(ys), b.x, b.x + b.length, 0.0, b.height)


def _start_end(
    feeds: list[Motion],
    motions: list[Motion],
    profile: Profile,
    job: Job,
    targets: dict[str, SectionTarget],
) -> Check:
    check = Check("start_end_outside", True)
    box = _block_box(job, targets)
    span = profile.wire_span
    for label, line, axes in (
        ("start of the cut", feeds[0].line, feeds[0].start),
        ("end of the cut", feeds[-1].line, feeds[-1].end),
        ("end of the program", motions[-1].line, motions[-1].end),
    ):
        if wire_in_box(axes, span, *box):
            check.messages.append(f"line {line}: the wire is inside the block at the {label}")
    check.passed = not check.messages
    return check


def _rapids(
    motions: list[Motion], profile: Profile, job: Job, targets: dict[str, SectionTarget]
) -> Check:
    check = Check(
        "rapids_clear", True,
        notes=[
            (
                "G0 assumed to move in a straight line in axis space; tested on 201 planes "
                "between the foam faces"
            )
        ],
    )
    y0, y1, *rect = _block_box(job, targets)
    for m in motions:
        if m.kind != "rapid" or not m.start_known:
            continue
        hit = sweep_hits_block(
            m.start, m.end, profile.wire_span, y0, y1, (rect[0], rect[1], rect[2], rect[3])
        )
        if hit is not None:
            check.messages.append(f"line {m.line}: rapid move passes through the block (at y = "
                                  f"{hit:.1f})")
    check.passed = not check.messages
    return check


def _lead_in_exit(
    feeds: list[Motion], traces: dict[str, Arr], targets: dict[str, SectionTarget],
    margin: float,
) -> Check:
    """The lead-in and exit wire centre must stay out of the target part,
    except within `margin` of the contour start S."""
    check = Check(
        "lead_in_exit_clear", True,
        notes=[f"wire centre tested, ignoring the last {margin:g} mm at the contour start"],
    )
    for name, t in traces.items():
        target = targets[name]
        for label, i, s, e in (("lead-in", 0, t[0], t[1]), ("exit", len(feeds) - 1, t[-1], t[-2])):
            # s: outside end (entry point), e: contour end (S)
            length = float(np.hypot(*(e - s)))
            if length <= margin:
                continue
            n = max(2, math.ceil((length - margin) / 0.05))
            frac = np.linspace(0.0, (length - margin) / length, n)
            pts = s + frac[:, None] * (e - s)
            part = inside(pts, target.outer)
            for hole in target.holes.values():
                part &= ~inside(pts, hole)
            if bool(np.any(part)):
                check.messages.append(
                    f"line {feeds[i].line}: the {label} passes through the part in section {name}"
                )
    check.passed = not check.messages
    return check
