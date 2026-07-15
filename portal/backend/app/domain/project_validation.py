from __future__ import annotations

import re


class InvalidProjectInput(ValueError):
    def __init__(self, message: str, *, code: str = "INVALID_PROJECT_INPUT"):
        super().__init__(message)
        self.code = code
        self.message = message


_SOLUTION_RE = re.compile(r"^[A-Za-z0-9._\- /\\]+\.sln$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._\-]+(/[A-Za-z0-9._\-]+)*$")


def validate_solution_path(path: str) -> str:
    value = (path or "").strip().replace("\\", "/")
    if not value:
        raise InvalidProjectInput("solutionPath is required")
    if value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        raise InvalidProjectInput("solutionPath must be repository-relative")
    if ".." in value.split("/"):
        raise InvalidProjectInput("solutionPath must not contain '..'")
    if not _SOLUTION_RE.match(value):
        raise InvalidProjectInput("solutionPath has invalid characters or extension")
    return value.replace("/", "\\") if "\\" in path else value


def validate_repository(name: str) -> str:
    value = (name or "").strip()
    if not value:
        raise InvalidProjectInput("repository is required")
    if not _REPO_RE.match(value):
        raise InvalidProjectInput("repository has invalid characters")
    return value
