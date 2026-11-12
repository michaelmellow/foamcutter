"""Strict reading of YAML documents into typed values.

Every problem is recorded with its field path and reading carries on, so a
validator can report all problems in one go. Callers check `problems` at the
end and raise ValidationError if it is not empty.
"""

from pathlib import Path
from typing import Any

import yaml

from .errors import ValidationError


def load_yaml(path: Path, what: str) -> Any:
    if path.suffix != ".yaml":
        raise ValidationError(what, [f"{path.name}: file name must end in .yaml"])
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValidationError(what, [f"{path}: cannot be read ({error.strerror})"]) from None
    return parse_yaml(text, what)


def parse_yaml(text: str, what: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValidationError(what, [f"not valid YAML: {error}"]) from None


class Reader:
    def __init__(self) -> None:
        self.problems: list[str] = []

    def problem(self, path: str, message: str) -> None:
        self.problems.append(f"{path}: {message}" if path else message)

    def mapping(self, data: Any, path: str, required: set[str], optional: frozenset[str] = frozenset()) -> dict[str, Any]:
        """Check that data is a mapping with exactly the allowed keys."""
        if not isinstance(data, dict):
            self.problem(path, "must be a mapping")
            return {}
        for key in data:
            if key not in required and key not in optional:
                self.problem(join(path, str(key)), "unknown field")
        for key in sorted(required - data.keys()):
            self.problem(join(path, key), "missing")
        return {k: v for k, v in data.items() if isinstance(k, str)}

    def number(
        self,
        data: dict[str, Any],
        key: str,
        path: str,
        *,
        gt: float | None = None,
        ge: float | None = None,
        lt: float | None = None,
        le: float | None = None,
    ) -> float:
        if key not in data:
            return 0.0
        return self.number_value(data[key], join(path, key), gt=gt, ge=ge, lt=lt, le=le)

    def number_value(
        self,
        value: Any,
        path: str,
        *,
        gt: float | None = None,
        ge: float | None = None,
        lt: float | None = None,
        le: float | None = None,
    ) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.problem(path, "must be a number")
            return 0.0
        number = float(value)
        if gt is not None and not number > gt:
            self.problem(path, f"must be greater than {gt:g}")
        if ge is not None and not number >= ge:
            self.problem(path, f"must be at least {ge:g}")
        if lt is not None and not number < lt:
            self.problem(path, f"must be less than {lt:g}")
        if le is not None and not number <= le:
            self.problem(path, f"must be at most {le:g}")
        return number

    def integer(self, data: dict[str, Any], key: str, path: str, *, equal: int) -> int:
        value = data.get(key)
        if key in data and (isinstance(value, bool) or not isinstance(value, int)):
            self.problem(join(path, key), "must be a whole number")
        elif key in data and value != equal:
            self.problem(join(path, key), f"must be {equal}")
        return equal

    def text(
        self,
        data: dict[str, Any],
        key: str,
        path: str,
        *,
        nonempty: bool = False,
        choices: tuple[str, ...] | None = None,
    ) -> str:
        if key not in data:
            return ""
        value = data[key]
        if not isinstance(value, str):
            self.problem(join(path, key), "must be text (write it in double quotes)")
            return ""
        if nonempty and not value.strip():
            self.problem(join(path, key), "must not be empty")
        if choices is not None and value not in choices:
            self.problem(join(path, key), "must be one of " + ", ".join(f'"{c}"' for c in choices))
        return value

    def boolean(self, data: dict[str, Any], key: str, path: str) -> bool:
        if key not in data:
            return False
        value = data[key]
        if not isinstance(value, bool):
            self.problem(join(path, key), "must be true or false")
            return False
        return value

    def sequence(self, data: dict[str, Any], key: str, path: str, *, min_length: int = 0) -> list[Any]:
        if key not in data:
            return []
        value = data[key]
        if not isinstance(value, list):
            self.problem(join(path, key), "must be a list")
            return []
        if len(value) < min_length:
            self.problem(join(path, key), f"must have at least {min_length} entries")
        return value

    def points(self, data: dict[str, Any], key: str, path: str, *, min_length: int) -> list[tuple[float, float]]:
        result: list[tuple[float, float]] = []
        for i, item in enumerate(self.sequence(data, key, path, min_length=min_length)):
            item_path = f"{join(path, key)}[{i}]"
            if not isinstance(item, list) or len(item) != 2:
                self.problem(item_path, "must be a pair [u, v]")
                continue
            result.append((self.number_value(item[0], item_path), self.number_value(item[1], item_path)))
        return result

    def raise_if_problems(self, what: str) -> None:
        if self.problems:
            raise ValidationError(what, self.problems)


def join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key
