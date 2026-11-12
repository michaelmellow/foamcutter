"""foamsim: independent simulator and pre-cut check for the hot-wire foam cutter.

Reads a finished G-code program, the machine profile and the job file,
reconstructs the cut in the root and tip planes, removes the kerf and compares
the result with the target sections (Brief 5).
"""

from .errors import GCodeError, InputError, SimError
from .job import load_job, parse_job
from .profile import load_profile, parse_profile
from .sim import SimResult, simulate

__all__ = [
    "GCodeError",
    "InputError",
    "SimError",
    "SimResult",
    "load_job",
    "load_profile",
    "parse_job",
    "parse_profile",
    "simulate",
]
