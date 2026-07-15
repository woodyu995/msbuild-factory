from __future__ import annotations

import re

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._\-_/]+$")


class GitResolveError(ValueError):
    def __init__(self, message: str, *, code: str = "GIT_REF_INVALID"):
        super().__init__(message)
        self.code = code
        self.message = message


def resolve_git_ref(git_ref: str) -> tuple[str, str]:
    """Return (resolved_commit, resolution_mode).

    Exact 40-char SHA is accepted as pinned.
    Branch/tag refs are accepted syntactically but marked placeholder until a
    real git server integration is wired.
    """
    value = (git_ref or "").strip()
    if not value:
        raise GitResolveError("gitRef is required")
    if _SHA_RE.match(value):
        return value.lower(), "exact"
    if value.startswith("-") or ".." in value or not _REF_RE.match(value):
        raise GitResolveError("gitRef has invalid characters")
    if len(value) > 255:
        raise GitResolveError("gitRef is too long")
    return f"resolved:{value}", "placeholder"
