"""Airfoil coordinate files in Selig and Lednicer layout (job-file.md §7)."""

from pathlib import Path

from .errors import ValidationError

Point = tuple[float, float]


def read_dat(path: Path) -> list[Point]:
    """Return the points of a .dat file in Selig order, as written (not normalised)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ValidationError("airfoil file", [f"{path}: cannot be read ({error.strerror})"]) from None
    return parse_dat(text, str(path))


def parse_dat(text: str, name: str) -> list[Point]:
    lines = [(number, line.split()) for number, line in enumerate(text.splitlines(), start=1)]
    content = [(number, words) for number, words in lines if words]
    if len(content) < 2:
        raise ValidationError("airfoil file", [f"{name}: no coordinates"])
    body = content[1:]  # the first non-blank line is the name

    def pair(number: int, words: list[str]) -> Point:
        if len(words) != 2:
            raise ValidationError("airfoil file", [f"{name} line {number}: expected two numbers"])
        try:
            return float(words[0]), float(words[1])
        except ValueError:
            raise ValidationError("airfoil file", [f"{name} line {number}: expected two numbers"]) from None

    first = pair(*body[0])
    if first[0] > 1.5 and first[1] > 1.5:
        # Lednicer: counts, then upper LE->TE, then lower LE->TE.
        n_upper, n_lower = round(first[0]), round(first[1])
        points = [pair(number, words) for number, words in body[1:]]
        if n_upper < 2 or n_lower < 2 or len(points) != n_upper + n_lower:
            raise ValidationError(
                "airfoil file",
                [f"{name}: Lednicer header says {n_upper} + {n_lower} points, file has {len(points)}"],
            )
        upper = points[:n_upper]
        lower = points[n_upper:]
        selig = list(reversed(upper))
        if lower[0] == selig[-1]:
            lower = lower[1:]
        return selig + lower
    return [pair(number, words) for number, words in body]
