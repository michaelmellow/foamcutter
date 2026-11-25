"""The simulator reads a job's kerf_extension (job-file.md §3) on its own."""

from pathlib import Path

import pytest
import yaml

from foamsim.errors import InputError
from foamsim.job import parse_job

REPO = Path(__file__).resolve().parents[2]
JOB = REPO / "examples" / "jobs" / "tapered-spar.yaml"


def job_data(extension: object) -> dict[str, object]:
    data: dict[str, object] = yaml.safe_load(JOB.read_text(encoding="utf-8"))
    data["kerf_extension"] = extension
    return data


def test_extension_points_are_read_sorted() -> None:
    job = parse_job(job_data([{"speed": 700.0, "kerf": 0.5}, {"speed": 20.0, "kerf": 2.4}]), JOB.parent)
    assert job.kerf_extension == ((20.0, 2.4), (700.0, 0.5))


def test_extension_needs_distinct_positive_speeds() -> None:
    with pytest.raises(InputError, match="same speed"):
        parse_job(job_data([{"speed": 700.0, "kerf": 0.5}, {"speed": 700.0, "kerf": 0.6}]), JOB.parent)
    with pytest.raises(InputError, match="kerf_extension"):
        parse_job(job_data([{"speed": 700.0, "kerf": 0}]), JOB.parent)
