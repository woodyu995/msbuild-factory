from __future__ import annotations

import re


class InvalidProjectInput(ValueError):
    def __init__(self, message: str, *, code: str = "INVALID_PROJECT_INPUT"):
        super().__init__(message)
        self.code = code
        self.message = message


_SOLUTION_RE = re.compile(r"^[A-Za-z0-9._\- /\\]+\.sln$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._\-]+(/[A-Za-z0-9._\-]+)*$")
_CONFIG_RE = re.compile(r"^[A-Za-z0-9._\-]+$")
_PLATFORM_RE = re.compile(r"^[A-Za-z0-9._\-]+$")
_ALLOWED_CONFIGURATIONS = frozenset({"Debug", "Release", "RelWithDebInfo", "MinSizeRel"})
_ALLOWED_PLATFORMS = frozenset({"x86", "x64", "AnyCPU", "Win32", "ARM64"})


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


def validate_configuration(value: str | None) -> str:
    config = (value or "Release").strip()
    if not _CONFIG_RE.match(config):
        raise InvalidProjectInput("configuration has invalid characters")
    if config not in _ALLOWED_CONFIGURATIONS:
        raise InvalidProjectInput(
            f"configuration must be one of: {', '.join(sorted(_ALLOWED_CONFIGURATIONS))}"
        )
    return config


def validate_platform(value: str | None) -> str:
    platform = (value or "x64").strip()
    if not _PLATFORM_RE.match(platform):
        raise InvalidProjectInput("platform has invalid characters")
    if platform not in _ALLOWED_PLATFORMS:
        raise InvalidProjectInput(
            f"platform must be one of: {', '.join(sorted(_ALLOWED_PLATFORMS))}"
        )
    return platform
