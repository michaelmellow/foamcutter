"""foamcam: CAM core for a 4-axis hot-wire foam cutter (Brief 3).

Formats: docs/formats/machine-profile.md and docs/formats/job-file.md.
"""

__version__ = "0.1.0"

from .cam import CamResult, generate
from .errors import CamError, FoamcamError, ValidationError
from .job import Job, load_job, load_job_text
from .profile import Profile, load_profile, load_profile_text

__all__ = [
    "CamError",
    "CamResult",
    "FoamcamError",
    "Job",
    "Profile",
    "ValidationError",
    "__version__",
    "generate",
    "load_job",
    "load_job_text",
    "load_profile",
    "load_profile_text",
]
