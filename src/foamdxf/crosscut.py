"""A straight cut across the whole table at one height, e.g. to slice a block into layers
or to skim its top before placing parts in it (the Machine tab's Cut tab).

Two small absolute programs, both towers always at the same position (straight wire):

1. **to the start**, wire cold: rapid to the operator-side end of the shared horizontal
   travel of axes 1 and 3 (their homed side, machine-profile.md §3.2) at the wire's
   current height, then straight up or down to the cut height;
2. **the cut**, wire hot: one feed move to the far end of that travel at the cut speed
   (G94 mm/min: with a straight wire this is the wire's speed at the foam).

The height is the wire's centre in work coordinates (G54): the kerf takes half its width
off each side of that line. The heat is switched by hand; the controller does not do it.
"""

from dataclasses import dataclass

from foamcam.profile import Profile

AT_START_MM = 0.05  # both towers this close to the start: the wire is there and straight


@dataclass(frozen=True)
class CrossCut:
    start_x: float  # axes 1 and 3: operator side
    end_x: float  # axes 1 and 3: far side
    z: float  # axes 2 and 4
    speed: float  # mm/min

    @property
    def minutes(self) -> float:
        return abs(self.end_x - self.start_x) / self.speed


def plan_cross_cut(profile: Profile, z: float, speed: float) -> CrossCut:
    """The cut across the shared travel at height `z` and `speed`. Raises ValueError."""
    a1, a2, a3, a4 = profile.axes
    x_lo, x_hi = max(a1.min, a3.min), min(a1.max, a3.max)
    z_lo, z_hi = max(a2.min, a4.min), min(a2.max, a4.max)
    if not z_lo <= z <= z_hi:
        raise ValueError(f"height {z:g} mm is outside the vertical travel {z_lo:g} to {z_hi:g} mm")
    top = min(a1.max_rate, a3.max_rate)
    if not 0 < speed <= top:
        raise ValueError(f"speed must be more than 0 and at most {top:g} mm/min (the horizontal axes' max rate)")
    if x_hi <= x_lo:
        raise ValueError("the horizontal axes have no travel in common")
    return CrossCut(x_lo, x_hi, z, speed)


def start_program(letters: tuple[str, ...], cut: CrossCut) -> str:
    """Wire cold: across to the operator side at the current height, then to the cut height."""
    l1, l2, l3, l4 = letters[:4]
    return "\n".join([
        f"(cross cut: wire to the start, operator side, z {cut.z:g})",
        "G21",
        "G90",
        f"G0 {l1}{cut.start_x:.3f} {l3}{cut.start_x:.3f}",
        f"G0 {l2}{cut.z:.3f} {l4}{cut.z:.3f}",
    ]) + "\n"


def cut_program(letters: tuple[str, ...], cut: CrossCut) -> str:
    """Wire hot: straight across to the far side at the cut speed."""
    l1, l2, l3, l4 = letters[:4]
    return "\n".join([
        f"(cross cut at z {cut.z:g}, {cut.speed:g} mm/min)",
        "G21",
        "G90",
        "G94",
        f"G1 {l1}{cut.end_x:.3f} {l3}{cut.end_x:.3f} {l2}{cut.z:.3f} {l4}{cut.z:.3f} F{cut.speed:g}",
    ]) + "\n"


def away_from_start(wpos: dict[str, float], letters: tuple[str, ...], cut: CrossCut) -> str:
    """"" if both towers are at the cut's start, else where the wire is instead."""
    l1, l2, l3, l4 = letters[:4]
    off = max(abs(wpos.get(l1, 0.0) - cut.start_x), abs(wpos.get(l3, 0.0) - cut.start_x),
              abs(wpos.get(l2, 0.0) - cut.z), abs(wpos.get(l4, 0.0) - cut.z))
    if off <= AT_START_MM:
        return ""
    return (f"the wire is at {l1}{wpos.get(l1, 0.0):.2f} {l2}{wpos.get(l2, 0.0):.2f} "
            f"{l3}{wpos.get(l3, 0.0):.2f} {l4}{wpos.get(l4, 0.0):.2f}, not at the start "
            f"({l1}={l3}={cut.start_x:g}, {l2}={l4}={cut.z:g})")
