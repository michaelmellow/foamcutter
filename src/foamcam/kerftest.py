"""Kerf test-cut generator.

Cuts one horizontal slot per speed into the front face of a block, with the
wire parallel to y (both towers at the same position). The operator measures
the height of each slot (its width across the direction of travel) and enters
speed and kerf in the machine profile's kerf table.

Each slot: rapid to the entry point in front of the block, feed straight in to
the slot depth at the test speed, rapid back out along the slot. The rapid
retract passes through the already-open slot. Whether it widens the slot
measurably has not been tested; measure near the closed end of the slot.
"""

from dataclasses import dataclass

from . import __version__
from .cam import axis_value, comment, inverse_time, segment_hits_box
from .errors import CamError
from .profile import Profile


@dataclass(frozen=True)
class KerfTest:
    block_x: float  # G54 x of the block's back face
    block_length: float  # along x
    block_height: float  # along z, block resting on the table (z = table_z of the profile)
    depth: float  # slot depth from the front face, mm
    speeds: tuple[float, ...]  # mm/min, one slot each, bottom slot first
    spacing: float  # vertical distance between slots, mm
    margin: float  # distance of the lowest and highest slot from bottom and top, mm
    lead_in: float  # clearance in front of the block, mm
    safe_position: tuple[float, float, float, float]


def kerf_test_gcode(test: KerfTest, profile: Profile) -> str:
    problems: list[str] = []
    front = test.block_x + test.block_length
    x_entry = front + test.lead_in
    x_end = front - test.depth
    if not test.speeds:
        problems.append("give at least one speed")
    if any(s <= 0 for s in test.speeds):
        problems.append("every speed must be greater than 0")
    if not 0 < test.depth < test.block_length:
        problems.append("the slot depth must be greater than 0 and less than the block length")
    if test.lead_in <= 0 or test.spacing <= 0 or test.margin <= 0:
        problems.append("lead-in, spacing and margin must be greater than 0")
    heights = [profile.table_z + test.margin + i * test.spacing for i in range(len(test.speeds))]
    if heights and heights[-1] > profile.table_z + test.block_height - test.margin:
        problems.append(
            f"{len(test.speeds)} slots {test.spacing:g} mm apart with {test.margin:g} mm margins "
            f"need a block at least {heights[-1] - profile.table_z + test.margin:g} mm high"
        )
    if problems:
        raise CamError(problems)

    letters = profile.letters

    def words(x: float, z: float) -> str:
        return " ".join(f"{letter}{axis_value(v)}" for letter, v in zip(letters, (x, z, x, z)))

    positions: list[tuple[float, float]] = []
    lines = [
        comment(f"foamcam {__version__} kerf test"),
        comment(f"profile: {profile.name}"),
        comment("wire parallel to y; one horizontal slot per speed, bottom slot first"),
    ]
    for i, (z, speed) in enumerate(zip(heights, test.speeds), start=1):
        lines.append(comment(f"slot {i}: z = {z:g} mm, speed {speed:g} mm/min"))
    lines.append(comment("measure each slot's height near its closed end: that is the kerf at its speed"))
    lines += ["G21", "G54", "G90", "G93", "G0 " + " ".join(f"{l}{axis_value(v)}" for l, v in zip(letters, test.safe_position))]
    for z, speed in zip(heights, test.speeds):
        duration = (x_entry - x_end) / speed
        for axis in (profile.axes[0], profile.axes[2]):
            if speed > axis.max_rate:
                problems.append(f"speed {speed:g} mm/min is above the max_rate of {axis.label}")
        lines.append(f"G0 {words(x_entry, z)}")
        lines.append(f"G1 {words(x_end, z)} F{inverse_time(duration)}")
        lines.append(f"G0 {words(x_entry, z)}")
        positions += [(x_entry, z), (x_end, z)]
    lines.append("G0 " + " ".join(f"{l}{axis_value(v)}" for l, v in zip(letters, test.safe_position)))
    lines.append("G94")

    for x, z in positions:
        for axis, value in zip(profile.axes, (x, z, x, z)):
            if not axis.min <= value <= axis.max:
                problems.append(f"{axis.label} would go to {value:.4f}, outside {axis.min:g} to {axis.max:g}")
    for axis, value in zip(profile.axes, test.safe_position):
        if not axis.min <= value <= axis.max:
            problems.append(f"safe position: {axis.label} = {value:g} is outside {axis.min:g} to {axis.max:g}")
    s = test.safe_position
    for a, b in (((s[0], s[1]), (x_entry, heights[0])), ((x_entry, heights[-1]), (s[0], s[1]))):
        if s[0] != s[2] or s[1] != s[3]:
            problems.append("the safe position must have axis 1 = axis 3 and axis 2 = axis 4 for the kerf test")
            break
        if segment_hits_box(a, b, (test.block_x, profile.table_z), (front, profile.table_z + test.block_height)):
            problems.append("a rapid move between the safe position and the slots passes through the block")
    if problems:
        raise CamError(sorted(set(problems)))
    return "\n".join(lines) + "\n"
