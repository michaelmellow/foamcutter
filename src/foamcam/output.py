"""Text report and SVG preview of a cut."""

from typing import TYPE_CHECKING

from . import __version__
from .geometry import Point
from .job import Job
from .profile import Profile
from .sections import Outline

if TYPE_CHECKING:
    from .cam import CamResult, Ref


def report(
    job: Job,
    profile: Profile,
    result: "CamResult",
    kerf_used: dict[str, list[float]],
    stretches: list[tuple[str, float, float]],
    kerf_rounds: dict[str, int],
    unmatched_corners: list[str] | None = None,
) -> str:
    from .cam import speed_ratio

    moves = result.moves
    feed = [m for m in moves if m.kind != "rapid"]
    cuts = [m for m in feed if m.kind == "cut"]
    lines = [
        f"foamcam {__version__} cut report",
        "",
        f"Job:          {job.name}",
        f"Profile:      {profile.name}",
        f"Kerf table:   {job.kerf_table}",
        *[f"  + this job only, not measured: {speed:g} mm/min: {kerf:.3f} mm" for speed, kerf in job.kerf_extension],
        f"Cut speed:    {job.cut_speed:g} mm/min at the faster foam face",
        f"Direction:    {job.direction}",
        f"Mirror:       {'yes' if job.mirror else 'no'}",
        "",
        "Foam block (G54, mm)",
        f"  x {job.block.x:.3f} to {job.block.x + job.block.length:.3f}",
        f"  y {min(job.root.y, job.tip.y):.3f} to {max(job.root.y, job.tip.y):.3f} (foam faces: root y = {job.root.y:g}, tip y = {job.tip.y:g})",
        f"  z {job.block.bottom(profile.table_z):.3f} to {job.block.bottom(profile.table_z) + job.block.height:.3f}",
        "",
        "Travel used (G54, mm)",
    ]
    for i, axis in enumerate(profile.axes):
        values = [m.axes[i] for m in moves]
        low, high = min(values), max(values)
        lines.append(
            f"  {axis.label}: {low:9.3f} to {high:9.3f}   limits {axis.min:9.3f} to {axis.max:9.3f}"
            f"   margin {min(low - axis.min, axis.max - high):8.3f}"
        )
    angles = [wire_angle(m.axes, profile.wire_span) for m in moves]
    ratios = [speed_ratio(m) for m in cuts]
    lines += [
        "",
        f"Wire angle:   max {max(angles):.2f} degrees (limit {profile.max_wire_angle:g})",
        f"Speed ratio:  max {max(ratios, default=1.0):.3f} over cutting moves (limit {profile.max_speed_ratio:g})",
    ]
    for kind, label in (("lead_in", "Lead-in"), ("exit", "Exit")):
        for m in feed:
            if m.kind == kind:
                lines.append(f"{label} speed ratio: {speed_ratio(m):.3f} (exempt from the limit: waste foam)")
    if any(f.channel for f in job.root.features) or job.channel_exit:
        from .job import RAPID

        how = (
            "rapid (G0)" if job.channel_exit == RAPID
            else f"{job.channel_exit:g} mm/min" if job.channel_exit else f"cut speed {job.cut_speed:g} mm/min"
        )
        lines.append(f"Channel exits: {how}; channels (not kerf compensated): "
                     + (", ".join(f.name for f in job.root.features if f.channel) or "none, straight slits only"))
    for face in ("root", "tip"):
        used = kerf_used[face]
        if used:
            lines.append(f"Kerf at {face}:  {min(used):.3f} to {max(used):.3f} mm (half applied each side)")
    lines.append("Kerf per stretch of the outer contour (root / tip, mm):")
    lines += [f"  {name}: {k_r:.3f} / {k_t:.3f}" for name, k_r, k_t in stretches]
    matched = sum(name.startswith("corner") for name, _, _ in stretches)
    lines.append(f"Matched corners of the outer contour: {matched}")
    lines += [f"  warning: {note}" for note in unmatched_corners or []]
    lines.append("Kerf lookups from the compensated path: " + ", ".join(f"{w} {n}" for w, n in kerf_rounds.items()))
    faster = sum(max(m.root_length, m.tip_length) for m in feed)
    minutes = result.total_time
    lines += [
        "",
        f"Path length at the faster face: {faster:.1f} mm",
        f"Root face path length:          {sum(m.root_length for m in feed):.1f} mm",
        f"Tip face path length:           {sum(m.tip_length for m in feed):.1f} mm",
        f"Feed moves:                     {len(feed)}",
        f"Total cut time:                 {minutes:.2f} min ({int(minutes // 60)} h {minutes % 60:04.1f} min)",
        "",
    ]
    return "\n".join(lines)


def wire_angle(axes: tuple[float, float, float, float], span: float) -> float:
    import math

    a1, a2, a3, a4 = axes
    return math.degrees(math.atan(math.hypot(a3 - a1, a4 - a2) / span))


def preview_svg(
    job: Job,
    profile: Profile,
    root: Outline,
    tip: Outline,
    comp_r: "dict[Ref, Point]",
    comp_t: "dict[Ref, Point]",
    refs: "list[Ref]",
) -> str:
    """Front view (x right, z up) of block, target sections and wire paths at both faces."""
    block, z0_block = job.block, job.block.bottom(profile.table_z)
    x_entry = block.x + block.length + job.lead_in
    xs = [block.x, x_entry]
    zs = [z0_block, z0_block + block.height]
    shapes: list[tuple[list[Point], str, str, bool]] = []
    for outline, comp, colour in ((root, comp_r, "#1f6fb4"), (tip, comp_t, "#d9730d")):
        target = [outline.place(p) for p in outline.contour]
        shapes.append((target, colour, "", True))
        for feature in outline.features.values():
            shapes.append(([outline.place(p) for p in feature.points], colour, "", feature.kind == "hole"))
        path = [comp[r] for r in refs]
        shapes.append((path, colour, "4 3", False))
        for points, _, _, _ in shapes[-3:]:
            xs += [p[0] for p in points]
            zs += [p[1] for p in points]

    margin = 10.0
    x0, x1 = min(xs) - margin, max(xs) + margin
    z0, z1 = min(zs) - margin, max(zs) + margin
    scale = 900.0 / (x1 - x0)
    width, height = 900.0, (z1 - z0) * scale + 50

    def sx(x: float) -> float:
        return (x - x0) * scale

    def sz(z: float) -> float:
        return (z1 - z) * scale + 40

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="sans-serif" font-size="12">'
        ),
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="8" y="16">{escape(job.name)}: front view, x right, z up.</text>',
        (
            f'<text x="8" y="32">Blue = root (y {job.root.y:g}), orange = tip (y {job.tip.y:g}). '
            "Solid = target section, dashed = wire path after kerf.</text>"
        ),
        (
            f'<rect x="{sx(block.x):.2f}" y="{sz(z0_block + block.height):.2f}" width="{block.length * scale:.2f}" '
            f'height="{block.height * scale:.2f}" fill="#f2efe6" stroke="#999"/>'
        ),
    ]
    for points, colour, dash, closed in shapes:
        coords = " ".join(f"{sx(x):.2f},{sz(z):.2f}" for x, z in points)
        tag = "polygon" if closed else "polyline"
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(f'<{tag} points="{coords}" fill="none" stroke="{colour}" stroke-width="1"{dash_attr}/>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
