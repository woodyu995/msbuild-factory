from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._\-_/]+$")


class GitResolveError(ValueError):
    def __init__(self, message: str, *, code: str = "GIT_REF_INVALID"):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GitResolveResult:
    commit: str
    mode: str  # exact | ls_remote | http_api | placeholder
    repository: str
    git_ref: str


class GitResolver(Protocol):
    def resolve(self, repository: str, git_ref: str) -> GitResolveResult: ...


def _validate_ref(git_ref: str) -> str:
    value = (git_ref or "").strip()
    if not value:
        raise GitResolveError("gitRef is required")
    if value.startswith("-") or ".." in value or not _REF_RE.match(value):
        raise GitResolveError("gitRef has invalid characters")
    if len(value) > 255:
        raise GitResolveError("gitRef is too long")
    return value


class PlaceholderGitResolver:
    """Local/dev resolver: SHA exact, otherwise placeholder commit id."""

    def resolve(self, repository: str, git_ref: str) -> GitResolveResult:
        ref = _validate_ref(git_ref)
        if _SHA_RE.match(ref):
            return GitResolveResult(ref.lower(), "exact", repository, ref)
        return GitResolveResult(f"resolved:{ref}", "placeholder", repository, ref)


class LsRemoteGitResolver:
    """Resolve refs with `git ls-remote` against a clone URL template."""

    def __init__(self, *, url_template: str, timeout_seconds: float = 30.0):
        # e.g. https://git.internal/{repository}.git
        self.url_template = url_template
        self.timeout_seconds = timeout_seconds

    def resolve(self, repository: str, git_ref: str) -> GitResolveResult:
        ref = _validate_ref(git_ref)
        if _SHA_RE.match(ref):
            return GitResolveResult(ref.lower(), "exact", repository, ref)
        url = self.url_template.format(repository=repository)
        try:
            completed = subprocess.run(
                ["git", "ls-remote", url, ref],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise GitResolveError("git binary not available on Portal host", code="GIT_UNAVAILABLE") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitResolveError("git ls-remote timed out", code="GIT_TIMEOUT") from exc
        if completed.returncode != 0:
            raise GitResolveError(
                f"git ls-remote failed: {completed.stderr.strip() or completed.stdout.strip()}",
                code="GIT_LS_REMOTE_FAILED",
            )
        lines = [ln.strip() for ln in completed.stdout.splitlines() if ln.strip()]
        if not lines:
            # try heads/tags prefixes
            for prefix in (f"refs/heads/{ref}", f"refs/tags/{ref}"):
                completed = subprocess.run(
                    ["git", "ls-remote", url, prefix],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
                lines = [ln.strip() for ln in completed.stdout.splitlines() if ln.strip()]
                if lines:
                    break
        if not lines:
            raise GitResolveError(f"ref not found: {ref}", code="GIT_REF_NOT_FOUND")
        sha = lines[0].split()[0].strip().lower()
        if not _SHA_RE.match(sha):
            raise GitResolveError(f"unexpected ls-remote sha: {sha}", code="GIT_BAD_SHA")
        return GitResolveResult(sha, "ls_remote", repository, ref)


class HttpApiGitResolver:
    """Resolve via simple internal API.

    Expected endpoint:
      GET {base}/repos/{repository}/commits/{ref}
      Authorization: Bearer {token} (optional)
      Response JSON: {"sha":"..."} or {"commit":{"id":"..."}}
    """

    def __init__(self, *, base_url: str, token: str | None = None, timeout_seconds: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def resolve(self, repository: str, git_ref: str) -> GitResolveResult:
        ref = _validate_ref(git_ref)
        if _SHA_RE.match(ref):
            return GitResolveResult(ref.lower(), "exact", repository, ref)
        path = f"/repos/{urllib.parse.quote(repository, safe='')}/commits/{urllib.parse.quote(ref, safe='')}"
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise GitResolveError(
                f"git http api failed: HTTP {exc.code}",
                code="GIT_HTTP_FAILED",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise GitResolveError(f"git http api error: {exc}", code="GIT_HTTP_FAILED") from exc
        sha = (
            payload.get("sha")
            or payload.get("id")
            or (payload.get("commit") or {}).get("id")
            or (payload.get("commit") or {}).get("sha")
        )
        if not sha or not _SHA_RE.match(str(sha)):
            raise GitResolveError("git http api returned invalid sha", code="GIT_BAD_SHA")
        return GitResolveResult(str(sha).lower(), "http_api", repository, ref)


def build_git_resolver(
    *,
    mode: str,
    url_template: str | None = None,
    http_base_url: str | None = None,
    token: str | None = None,
    require_exact: bool = False,
) -> GitResolver:
    mode = (mode or "placeholder").lower()
    if mode == "placeholder":
        inner: GitResolver = PlaceholderGitResolver()
    elif mode == "ls_remote":
        if not url_template:
            raise GitResolveError("PORTAL_GIT_URL_TEMPLATE required for ls_remote", code="GIT_CONFIG")
        inner = LsRemoteGitResolver(url_template=url_template)
    elif mode == "http_api":
        if not http_base_url:
            raise GitResolveError("PORTAL_GIT_HTTP_BASE_URL required for http_api", code="GIT_CONFIG")
        inner = HttpApiGitResolver(base_url=http_base_url, token=token)
    else:
        raise GitResolveError(f"unknown git resolve mode: {mode}", code="GIT_CONFIG")

    if not require_exact:
        return inner

    class _RequireExact:
        def resolve(self, repository: str, git_ref: str) -> GitResolveResult:
            result = inner.resolve(repository, git_ref)
            if result.mode == "placeholder":
                raise GitResolveError(
                    "exact commit resolution required; configure PORTAL_GIT_RESOLVE_MODE",
                    code="GIT_PLACEHOLDER_FORBIDDEN",
                )
            return result

    return _RequireExact()


# Back-compat helper used by older tests/call sites
def resolve_git_ref(git_ref: str) -> tuple[str, str]:
    result = PlaceholderGitResolver().resolve("unused", git_ref)
    return result.commit, result.mode
