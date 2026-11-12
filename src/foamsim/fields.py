"""Small helpers to validate YAML data and report every violation with its path."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from .errors import InputError


class Checker:
    """Collects violations as `path: reason` strings."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, path: str, reason: str) -> None:
        self.errors.append(f"{path}: {reason}" if path else reason)

    def raise_if_any(self) -> None:
        if self.errors:
            raise InputError(self.errors)

    def mapping(
        self, data: Any, path: str, required: tuple[str, ...], optional: tuple[str, ...] = ()
    ) -> dict[str, Any] | None:
        """Check that `data` is a mapping with exactly the allowed keys."""
        if not isinstance(data, dict):
            self.error(path, "must be a mapping")
            return None
        for key in data:
            if key not in required and key not in optional:
                self.error(join(path, str(key)), "unknown key")
        for key in required:
            if key not in data:
                self.error(join(path, key), "required")
        return data

    def number(
        self,
        data: dict[str, Any] | None,
        key: str,
        path: str,
        rule: Callable[[float], bool] | None = None,
        rule_text: str = "",
    ) -> float | None:
        if data is None or key not in data:
            return None
        return self.value_number(data[key], join(path, key), rule, rule_text)

    def value_number(
        self,
        value: Any,
        path: str,
        rule: Callable[[float], bool] | None = None,
        rule_text: str = "",
    ) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.error(path, "must be a number")
            return None
        number = float(value)
        if not math.isfinite(number):
            self.error(path, "must be a finite number")
            return None
        if rule is not None and not rule(number):
            self.error(path, f"must be {rule_text}")
            return None
        return number

    def integer(self, data: dict[str, Any] | None, key: str, path: str) -> int | None:
        if data is None or key not in data:
            return None
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, int):
            self.error(join(path, key), "must be an integer")
            return None
        return value

    def text(
        self,
        data: dict[str, Any] | None,
        key: str,
        path: str,
        allow_empty: bool = False,
        choices: tuple[str, ...] | None = None,
    ) -> str | None:
        if data is None or key not in data:
            return None
        value = data[key]
        p = join(path, key)
        if not isinstance(value, str):
            self.error(p, "must be text")
            return None
        if not allow_empty and value == "":
            self.error(p, "must not be empty")
            return None
        if choices is not None and value not in choices:
            self.error(p, "must be one of " + ", ".join(f'"{c}"' for c in choices))
            return None
        return value

    def boolean(self, data: dict[str, Any] | None, key: str, path: str) -> bool | None:
        if data is None or key not in data:
            return None
        value = data[key]
        if not isinstance(value, bool):
            self.error(join(path, key), "must be true or false")
            return None
        return value

    def items(self, data: dict[str, Any] | None, key: str, path: str) -> list[Any] | None:
        if data is None or key not in data:
            return None
        value = data[key]
        if not isinstance(value, list):
            self.error(join(path, key), "must be a list")
            return None
        return value


def join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key
