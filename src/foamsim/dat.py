"""Airfoil .dat files, Selig and Lednicer layouts (job file format section 7)."""

from __future__ import annotations

from pathlib import Path

from .errors import InputError


def read_dat(path: Path | str) -> list[tuple[float, float]]:
    """Return the contour in Selig order, as read (before job section 6.1)."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"{path}: cannot read airfoil file: {exc}") from exc
    # (line number, numbers) for every non-blank line after the name.
    rows: list[tuple[int, list[str]]] = []
    lines = text.splitlines()
    name_seen = False
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        if not name_seen:
            name_seen = True
            continue
        rows.append((number, line.split()))
    if not rows:
        raise InputError(f"{path}: no coordinates")

    values: list[tuple[int, float, float]] = []
    for number, words in rows:
        if len(words) != 2:
            raise InputError(f"{path}: line {number} does not hold exactly two numbers")
        try:
            values.append((number, float(words[0]), float(words[1])))
        except ValueError:
            raise InputError(f"{path}: line {number} does not hold exactly two numbers") from None

    _, first_a, first_b = values[0]
    if first_a > 1.5 and first_b > 1.5:
        return _lednicer(path, values)
    return [(u, v) for _, u, v in values]


def _lednicer(path: Path, values: list[tuple[int, float, float]]) -> list[tuple[float, float]]:
    n_upper, n_lower = values[0][1], values[0][2]
    if n_upper != int(n_upper) or n_lower != int(n_lower):
        raise InputError(f"{path}: line {values[0][0]}: point counts must be whole numbers")
    nu, nl = int(n_upper), int(n_lower)
    points = [(u, v) for _, u, v in values[1:]]
    if len(points) != nu + nl:
        raise InputError(
            f"{path}: header says {nu} + {nl} points but the file holds {len(points)}"
        )
    upper = points[:nu]
    lower = points[nu:]
    result = list(reversed(upper))
    if lower and lower[0] == result[-1]:
        lower = lower[1:]
    return result + lower
