"""Exceptions raised by the simulator."""

from __future__ import annotations


class SimError(Exception):
    """Base class for every error the simulator raises on purpose."""


class InputError(SimError):
    """An invalid machine profile, job file, .dat file or request.

    `messages` holds one entry per violation found.
    """

    def __init__(self, messages: list[str] | str) -> None:
        self.messages = [messages] if isinstance(messages, str) else list(messages)
        super().__init__("\n".join(self.messages))


class GCodeError(SimError):
    """A G-code line outside the accepted subset, or an undefined state."""

    def __init__(self, line: int, message: str) -> None:
        self.line = line
        super().__init__(f"line {line}: {message}")
