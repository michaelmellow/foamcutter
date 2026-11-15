"""Turn two DXF outlines, the distance between them and the stock size into a
job file (docs/formats/job-file.md).

Placement rules:

- **Along the wire (y).** The two section planes are centred between the
  carriage planes: `root.y = (wire_span - span) / 2`, `tip.y = root.y + span`.
  The root is nearer the left tower; use the job's `mirror` for the other hand.
- **In the section plane (x, z).** Both DXFs are drawn in one coordinate frame,
  so sweep, dihedral and twist are whatever the drawings show. The bounding box
  of both outlines together is centred in the stock (length along x, height
  along z). The stock rests on the table (`z = table_z`).
- **Stock on the table (x).** By default the stock plus the lead-in is centred
  in the travel shared by axes 1 and 3. `block_x` overrides this.

Each outline becomes a `shape.points` list in chord fractions with its leading
edge at (0, 0), `chord` = its x extent, `twist` = 0, `twist_axis` = 0, and
`position` = the placed leading edge. With these values job-file.md §6.1 to
§6.5 reproduce the drawn outline exactly. §6.2 then opens a trailing edge thinner
than `min_te_thickness` (default 0.6 mm, as in the worked examples): a sharp
trailing edge is a corner too sharp for kerf compensation (§10.7 step 8).
"""

from dataclasses import dataclass

from foamcam.profile import Profile

from .errors import DxfError
from .holes import CHANNELS, HolePair, pair_holes
from .outline import DxfOutline, Point


@dataclass(frozen=True)
class Placement:
    span: float  # distance between the two section planes, mm
    stock_length: float  # along x, mm
    stock_height: float  # along z, mm
    block_x: float | None = None  # G54 x of the stock's back face; None = centred in travel
    lead_in: float = 20.0  # mm in front of the stock
    safe_clearance: float = 20.0  # mm above the stock for the safe position


@dataclass(frozen=True)
class CutSettings:
    cut_speed: float = 150.0
    kerf_table: str | None = None  # None = the profile's first table
    direction: str = "upper_first"
    max_segment_length: float = 1.0
    min_te_thickness: float = 0.6  # opens a sharp trailing edge to a face the kerf can follow
    channel: str = "shortest"  # side the wire enters each hole from: "shortest", "upper" or "lower"


@dataclass(frozen=True)
class GeneratedJob:
    text: str
    summary: tuple[str, ...]


def build_job(
    root: DxfOutline,
    tip: DxfOutline,
    profile: Profile,
    placement: Placement,
    settings: CutSettings,
    name: str | None = None,
) -> GeneratedJob:
    problems: list[str] = []
    span = placement.span
    if not 0 < span < profile.wire_span:
        problems.append(
            f"span {span:g} mm must be more than 0 and less than the wire span {profile.wire_span:g} mm"
        )
    if placement.stock_length <= 0 or placement.stock_height <= 0:
        problems.append("stock length and height must be more than 0")
    if placement.lead_in <= 0:
        problems.append("lead-in must be more than 0")
    if settings.direction not in ("upper_first", "lower_first"):
        problems.append('direction must be "upper_first" or "lower_first"')
    kerf_table = settings.kerf_table or next(iter(profile.kerf_tables))
    if kerf_table not in profile.kerf_tables:
        problems.append(
            f'kerf table "{kerf_table}" is not in the profile (has: {", ".join(profile.kerf_tables)})'
        )

    every = root.points + tip.points
    xmin, xmax = min(p[0] for p in every), max(p[0] for p in every)
    zmin, zmax = min(p[1] for p in every), max(p[1] for p in every)
    width, height = xmax - xmin, zmax - zmin
    if width > placement.stock_length + 1e-9 or height > placement.stock_height + 1e-9:
        problems.append(
            f"the outlines need {width:.1f} x {height:.1f} mm (length x height) but the stock is "
            f"{placement.stock_length:g} x {placement.stock_height:g} mm"
        )
    holes: list[HolePair] = []
    if settings.channel not in CHANNELS:
        problems.append(f'hole channel must be one of {", ".join(CHANNELS)}')
    else:
        try:
            holes = pair_holes(root.points, root.holes, tip.points, tip.holes,
                               settings.channel, settings.max_segment_length)
        except DxfError as error:
            problems.append(str(error))
    if problems:
        raise DxfError("cannot build the job:\n" + "\n".join(f"  - {p}" for p in problems))

    a1, a3 = profile.axes[0], profile.axes[2]
    if placement.block_x is None:
        lo, hi = max(a1.min, a3.min), min(a1.max, a3.max)
        block_x = (lo + hi) / 2 - (placement.stock_length + placement.lead_in) / 2
    else:
        block_x = placement.block_x
    dx = block_x + (placement.stock_length - width) / 2 - xmin
    dz = profile.table_z + (placement.stock_height - height) / 2 - zmin

    y_root = (profile.wire_span - span) / 2
    y_tip = y_root + span
    x_entry = block_x + placement.stock_length + placement.lead_in
    z_safe = profile.table_z + placement.stock_height + placement.safe_clearance

    title = name or f"{root.source} to {tip.source}, span {span:g} mm"
    lines = [
        f"# Generated by foamdxf from {root.source} (root) and {tip.source} (tip).",
        "# Regenerate from the source files instead of editing the shapes by hand.",
        (
            f"# Stock {placement.stock_length:g} x {placement.stock_height:g} mm, back face at x = {_num(block_x)}, "
            f"on the table at z = {_num(profile.table_z)}."
        ),
        f"# Section planes y = {_num(y_root)} and y = {_num(y_tip)}, centred between the towers.",
        'format: "foamcutter-job"',
        "version: 1",
        f"name: {_quote(title)}",
        f"kerf_table: {_quote(kerf_table)}",
        f"cut_speed: {_num(settings.cut_speed)}",
        f"direction: {_quote(settings.direction)}",
        f"max_segment_length: {_num(settings.max_segment_length)}",
        f"min_te_thickness: {_num(settings.min_te_thickness)}",
        f"lead_in: {_num(placement.lead_in)}",
        f"safe_position: [{_num(x_entry)}, {_num(z_safe)}, {_num(x_entry)}, {_num(z_safe)}]",
        "mirror: false",
        (
            f"block: {{ x: {_num(block_x)}, length: {_num(placement.stock_length)}, "
            f"height: {_num(placement.stock_height)} }}"
        ),
    ]
    lines += _section("root", root, dx, dz, y_root, [(h.name, h.slit, h.root) for h in holes])
    lines += _section("tip", tip, dx, dz, y_tip, [(h.name, h.slit, h.tip) for h in holes])

    summary = (
        f"root {root.source}: chord {root.chord:.1f} mm; tip {tip.source}: chord {tip.chord:.1f} mm",
        (
            f"outlines {width:.1f} x {height:.1f} mm, centred in stock "
            f"{placement.stock_length:g} x {placement.stock_height:g} mm"
        ),
        (
            f"stock back face x = {block_x:.1f}, front face x = {block_x + placement.stock_length:.1f}, "
            f"entry x = {x_entry:.1f}"
        ),
        (
            f"section planes y = {y_root:.1f} (root) and {y_tip:.1f} (tip), span {span:g} mm, "
            f"wire span {profile.wire_span:g} mm"
        ),
        f"safe position [{x_entry:.1f}, {z_safe:.1f}, {x_entry:.1f}, {z_safe:.1f}]",
    ) + tuple(
        f"{h.name}: {len(h.root)} points, channel from the {h.slit} side, "
        f"{h.channel_lengths[0]:.1f} mm (root) / {h.channel_lengths[1]:.1f} mm (tip) long"
        for h in holes
    )
    return GeneratedJob("\n".join(lines) + "\n", summary)


def _section(
    label: str, outline: DxfOutline, dx: float, dz: float, y: float, features: list[tuple[str, str, tuple[Point, ...]]]
) -> list[str]:
    le = outline.points[outline.le_index]
    chord = outline.chord
    shape: list[Point] = [((x - le[0]) / chord, (z - le[1]) / chord) for x, z in outline.points]
    out = [f"{label}:", "  shape:", "    points:"]
    out += [f"      - [{u:.9f}, {v:.9f}]" for u, v in shape]
    out += [
        f"  chord: {_num(chord)}",
        "  twist: 0.0",
        "  twist_axis: 0.0",
        f"  position: {{ x: {_num(le[0] + dx)}, y: {_num(y)}, z: {_num(le[1] + dz)} }}",
    ]
    if features:  # §8: points in mm, origin at the leading edge
        out.append("  features:")
        for name, slit, pts in features:
            out += [f"    - name: {_quote(name)}", '      kind: "hole"', f"      slit: {_quote(slit)}", "      points:"]
            out += [f"        - [{x - le[0]:.6f}, {z - le[1]:.6f}]" for x, z in pts]
    return out


def _num(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return text if "." in text else text + ".0"


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
