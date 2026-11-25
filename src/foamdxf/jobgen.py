"""Turn two DXF outlines, the distance between them and the stock size into a
job file (docs/formats/job-file.md).

Placement rules:

- **Along the wire (y).** The two section planes are centred between the
  carriage planes: `root.y = (wire_span - span) / 2`, `tip.y = root.y + span`,
  unless `root_y` places the root face (e.g. nearer a tower, so a big taper
  needs less travel). The root is nearer the axes 1-2 tower (y = 0); use the
  job's `mirror` for the other hand.
- **In the section plane (x, z).** Both DXFs are drawn in one coordinate frame,
  so sweep, dihedral and twist are whatever the drawings show. The bounding box
  of both outlines together is centred in the stock (length along x, height
  along z). The stock rests on the table (`z = table_z`) unless `block_z` puts
  its bottom face elsewhere, e.g. on a part of a larger block (job-file.md §5).
- **Stock on the table (x).** By default the stock plus the lead-in is centred
  in the travel shared by axes 1 and 3. `block_x` overrides this.

Each outline becomes a `shape.points` list in chord fractions with its leading
edge at (0, 0), `chord` = its x extent, `twist` = 0, `twist_axis` = 0, and
`position` = the placed leading edge. With these values job-file.md §6.1 to
§6.5 reproduce the drawn outline exactly. §6.2 then opens a trailing edge thinner
than `min_te_thickness` (default 0.6 mm, as in the worked examples): a sharp
trailing edge is a corner too sharp for kerf compensation (§10.7 step 8).
"""

from dataclasses import dataclass, field

from foamcam.profile import Profile

from .errors import DxfError
from .holes import CHANNELS, ChannelSpec, HolePair, pair_holes
from .outline import DxfOutline, Point


@dataclass(frozen=True)
class Placement:
    span: float  # distance between the two section planes, mm
    stock_length: float  # along x, mm
    stock_height: float  # along z, mm
    block_x: float | None = None  # G54 x of the stock's back face; None = centred in travel
    lead_in: float = 20.0  # mm in front of the stock
    safe_clearance: float = 20.0  # mm above the stock for the safe position
    block_z: float | None = None  # G54 z of the stock's bottom face; None = on the table
    root_y: float | None = None  # y of the root face; None = both faces centred between the towers


@dataclass(frozen=True)
class CutSettings:
    cut_speed: float = 150.0
    kerf_table: str | None = None  # None = the profile's first table
    direction: str = "upper_first"
    max_segment_length: float = 1.0
    min_te_thickness: float = 0.6  # opens a sharp trailing edge to a face the kerf can follow
    channel: str = "shortest"  # side the wire enters each hole from: "shortest", "upper" or "lower"
    channels: dict[str, ChannelSpec] = field(default_factory=dict)  # drawn channels by hole name
    # how the wire leaves a hole's channel (job-file.md §10.4): "rapid", "cut" (at cut_speed,
    # field left out) or a speed in mm/min
    channel_exit: str | float = "rapid"
    # (speed, kerf) for this job only, outside the kerf table's measured range (job-file.md §3)
    kerf_extension: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class GeneratedJob:
    text: str
    summary: tuple[str, ...]
    holes: tuple[HolePair, ...] = ()  # in the drawing frame
    shift: tuple[float, float] = (0.0, 0.0)  # drawing frame + shift = machine (work) frame
    outers: tuple[tuple[Point, ...], tuple[Point, ...]] = ((), ())  # root, tip outer contours as drawn


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
                               settings.channel, settings.max_segment_length, settings.channels)
        except DxfError as error:
            problems.append(str(error))
    exit_line = channel_exit_line(settings.channel_exit, problems)
    if problems:
        raise DxfError("cannot build the job:\n" + "\n".join(f"  - {p}" for p in problems))

    a1, a3 = profile.axes[0], profile.axes[2]
    if placement.block_x is None:
        lo, hi = max(a1.min, a3.min), min(a1.max, a3.max)
        block_x = (lo + hi) / 2 - (placement.stock_length + placement.lead_in) / 2
    else:
        block_x = placement.block_x
    dx = block_x + (placement.stock_length - width) / 2 - xmin
    z0 = profile.table_z if placement.block_z is None else placement.block_z
    dz = z0 + (placement.stock_height - height) / 2 - zmin

    y_root = (profile.wire_span - span) / 2 if placement.root_y is None else placement.root_y
    y_tip = y_root + span
    x_entry = block_x + placement.stock_length + placement.lead_in
    z_safe = z0 + placement.stock_height + placement.safe_clearance

    title = name or f"{root.source} to {tip.source}, span {span:g} mm"
    lines = [
        f"# Generated by foamdxf from {root.source} (root) and {tip.source} (tip).",
        "# Regenerate from the source files instead of editing the shapes by hand.",
        (
            f"# Stock {placement.stock_length:g} x {placement.stock_height:g} mm, back face at x = {_num(block_x)}, "
            + (f"on the table at z = {_num(profile.table_z)}." if placement.block_z is None
             else f"bottom face at z = {_num(z0)} (table at z = {_num(profile.table_z)}).")
        ),
        f"# Section planes y = {_num(y_root)} and y = {_num(y_tip)}"
        + (", centred between the towers." if placement.root_y is None else "."),
        'format: "foamcutter-job"',
        "version: 1",
        f"name: {_quote(title)}",
        f"kerf_table: {_quote(kerf_table)}",
        *(["kerf_extension:  # this job only, not measured"] if settings.kerf_extension else []),
        *[f"  - {{ speed: {_num(s)}, kerf: {_num(k)} }}" for s, k in settings.kerf_extension],
        f"cut_speed: {_num(settings.cut_speed)}",
        f"direction: {_quote(settings.direction)}",
        f"max_segment_length: {_num(settings.max_segment_length)}",
        f"min_te_thickness: {_num(settings.min_te_thickness)}",
        f"lead_in: {_num(placement.lead_in)}",
        f"safe_position: [{_num(x_entry)}, {_num(z_safe)}, {_num(x_entry)}, {_num(z_safe)}]",
        "mirror: false",
        *exit_line,
        (
            f"block: {{ x: {_num(block_x)}, length: {_num(placement.stock_length)}, "
            f"height: {_num(placement.stock_height)}"
            + ("" if placement.block_z is None else f", z: {_num(placement.block_z)}") + " }"
        ),
    ]
    lines += _section("root", root, dx, dz, y_root, [(h.name, h.slit, h.root, h.root_channel) for h in holes])
    lines += _section("tip", tip, dx, dz, y_tip, [(h.name, h.slit, h.tip, h.tip_channel) for h in holes])

    summary = (
        f"root {root.source}: chord {root.chord:.1f} mm; tip {tip.source}: chord {tip.chord:.1f} mm",
        (
            f"outlines {width:.1f} x {height:.1f} mm, centred in stock "
            f"{placement.stock_length:g} x {placement.stock_height:g} mm"
        ),
        (
            f"stock back face x = {block_x:.1f}, front face x = {block_x + placement.stock_length:.1f}, "
            f"entry x = {x_entry:.1f}"
            + ("" if placement.block_z is None else f", bottom face z = {z0:.1f}")
        ),
        (
            f"section planes y = {y_root:.1f} (root) and {y_tip:.1f} (tip), span {span:g} mm, "
            f"wire span {profile.wire_span:g} mm"
        ),
        f"safe position [{x_entry:.1f}, {z_safe:.1f}, {x_entry:.1f}, {z_safe:.1f}]",
    ) + tuple(
        f"{h.name}: {len(h.root)} points, {_channel_kind(h)} "
        + (f"into {h.into} (cut from inside it), " if h.into else f"from the {h.slit} side, ") +
        f"{h.channel_lengths[0]:.1f} mm (root) / {h.channel_lengths[1]:.1f} mm (tip) long"
        for h in holes
    ) + ((f"channel exit: {_exit_text(settings.channel_exit, settings.cut_speed)}",) if holes else ()) + tuple(
        f"kerf table extended for this job (not measured): {s:g} mm/min, {k:.3f} mm"
        for s, k in settings.kerf_extension
    )
    return GeneratedJob("\n".join(lines) + "\n", summary, tuple(holes), (dx, dz), (root.points, tip.points))


def channel_exit_line(value: str | float, problems: list[str]) -> list[str]:
    """The job's channel_exit line (§3): none for "cut", which is the CAM's default."""
    if value == "rapid":
        return ['channel_exit: "rapid"']
    if value == "cut":
        return []
    if isinstance(value, str):
        problems.append('channel exit must be "rapid", "cut" or a speed in mm/min')
        return []
    if not value > 0:
        problems.append("channel exit speed must be more than 0")
        return []
    return [f"channel_exit: {_num(value)}"]


def _channel_kind(h: HolePair) -> str:
    if h.spec.shape == "straight":
        return f"straight channel at {h.spec.angle:g} degrees"
    if h.spec.shape == "curved":
        return f"curved channel ({len(h.root_channel) - 1} bend points) out at {h.spec.angle:g} degrees"
    return "channel"


def _exit_text(value: str | float, cut_speed: float) -> str:
    if value == "rapid":
        return "rapid (G0) out of each channel once the hole is cut"
    if value == "cut":
        return f"at the cut speed ({cut_speed:g} mm/min)"
    return f"at {value:g} mm/min"


def _section(
    label: str,
    outline: DxfOutline,
    dx: float,
    dz: float,
    y: float,
    features: list[tuple[str, str, tuple[Point, ...], tuple[Point, ...]]],
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
        for name, slit, pts, channel in features:
            out += [f"    - name: {_quote(name)}", '      kind: "hole"', f"      slit: {_quote(slit)}", "      points:"]
            out += [f"        - [{x - le[0]:.6f}, {z - le[1]:.6f}]" for x, z in pts]
            if channel:
                out.append("      channel:")
                out += [f"        - [{x - le[0]:.6f}, {z - le[1]:.6f}]" for x, z in channel]
    return out


def _num(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return text if "." in text else text + ".0"


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
