"""Slicer projects: every form value plus a copy of each input file, in one file.

A project (`.foamproj`, JSON) records the values exactly as typed and, for the
root DXF, the tip DXF, the machine profile and the STEP model, the original path, a SHA-256
and the file's bytes (base64). Opening a project uses the original file when
it is still there and unchanged. Otherwise the saved copy is written to a
folder next to the project (`<project name>_files/`) and used instead, so a
project always reproduces the job it was saved with, also on another PC.
"""

import base64
import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import DxfError

FORMAT = "foamdxf-project"
VERSION = 1
SUFFIX = ".foamproj"
FILE_KEYS = ("root", "tip", "profile", "step")


@dataclass(frozen=True)
class OpenedProject:
    values: dict[str, str]
    notes: tuple[str, ...]  # what was restored from the saved copies, and why


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_project(path: Path, values: dict[str, str]) -> None:
    """Write `values` and copies of the files they name. Raises DxfError if a file cannot be read."""
    files: dict[str, Any] = {}
    for key in FILE_KEYS:
        name = values.get(key, "").strip()
        if not name:
            continue
        source = Path(name)
        try:
            data = source.read_bytes()
        except OSError as error:
            raise DxfError(f"cannot save the project: {key} file {source}: {error}") from None
        files[key] = {
            "path": str(source.resolve()),
            "name": source.name,
            "sha256": _sha256(data),
            "content": base64.b64encode(data).decode("ascii"),
        }
    document = {
        "format": FORMAT,
        "version": VERSION,
        "saved": dt.datetime.now().isoformat(timespec="seconds"),
        "values": {k: str(v) for k, v in values.items()},
        "files": files,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(document, indent=1), encoding="utf-8")
    tmp.replace(path)  # never leave a half-written project behind


def open_project(path: Path) -> OpenedProject:
    """Read a project. Restores missing or changed input files from the saved copies."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DxfError(f"cannot open project {path.name}: {error}") from None
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise DxfError(f"{path.name} is not a slicer project")
    if document.get("version") != VERSION:
        raise DxfError(f"{path.name}: project version {document.get('version')} is not supported")

    values = {str(k): str(v) for k, v in dict(document.get("values", {})).items()}
    notes: list[str] = []
    copies = path.parent / f"{path.stem}_files"
    for key, entry in dict(document.get("files", {})).items():
        if key not in FILE_KEYS:
            continue
        try:
            data = base64.b64decode(entry["content"])
            name, digest, original = str(entry["name"]), str(entry["sha256"]), Path(entry["path"])
        except (KeyError, ValueError, TypeError):
            raise DxfError(f"{path.name}: the saved copy of the {key} file is damaged") from None
        if _sha256(data) != digest:
            raise DxfError(f"{path.name}: the saved copy of the {key} file is damaged")
        if original.is_file() and _sha256(original.read_bytes()) == digest:
            values[key] = str(original)
            continue
        reason = "has changed since the project was saved" if original.is_file() else "is missing"
        restored = copies / name
        if not (restored.is_file() and _sha256(restored.read_bytes()) == digest):
            copies.mkdir(parents=True, exist_ok=True)
            if restored.exists():  # a different file with the same name: do not overwrite it
                restored = copies / f"{restored.stem}-{digest[:8]}{restored.suffix}"
            restored.write_bytes(data)
        values[key] = str(restored)
        notes.append(f"{key}: {original} {reason}; using the copy saved in the project ({restored})")
    return OpenedProject(values, tuple(notes))
