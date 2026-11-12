"""Exceptions raised by foamcam."""


class FoamcamError(Exception):
    """Base class for every error foamcam reports to the user."""


class ValidationError(FoamcamError):
    """A profile or job file breaks a rule of its format. Lists every problem."""

    def __init__(self, what: str, problems: list[str]) -> None:
        self.problems = problems
        lines = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"{what} is invalid:\n{lines}")


class CamError(FoamcamError):
    """A valid job cannot be cut on this machine. Lists every reason."""

    def __init__(self, problems: list[str] | str) -> None:
        self.problems = [problems] if isinstance(problems, str) else problems
        if len(self.problems) == 1:
            super().__init__(self.problems[0])
        else:
            lines = "\n".join(f"  - {p}" for p in self.problems)
            super().__init__(f"the job cannot be cut:\n{lines}")
