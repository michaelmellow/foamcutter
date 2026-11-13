"""Skip every test in tests/dxf/ when the DXF libraries are not installed.

The libraries are the ones declared in the "dxf" dependency group of
pyproject.toml, so nothing here needs changing when the group changes.
"""

import re
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PYPROJECT = HERE.parent.parent / "pyproject.toml"


def missing_dxf_libraries() -> list[str]:
    with PYPROJECT.open("rb") as f:
        groups = tomllib.load(f)["project"]["optional-dependencies"]
    missing = []
    for requirement in groups.get("dxf", []):
        match = re.match(r"[A-Za-z0-9._-]+", requirement)
        if match is None:
            continue
        name = match.group(0)
        try:
            version(name)
        except PackageNotFoundError:
            missing.append(name)
    return missing


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    missing = missing_dxf_libraries()
    if not missing:
        return
    skip = pytest.mark.skip(reason="DXF libraries not installed: " + ", ".join(missing))
    for item in items:
        if item.path.is_relative_to(HERE):
            item.add_marker(skip)
