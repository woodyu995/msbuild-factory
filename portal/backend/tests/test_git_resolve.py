from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.domain.git_resolve import (
    GitResolveError,
    HttpApiGitResolver,
    LsRemoteGitResolver,
    PlaceholderGitResolver,
    build_git_resolver,
    resolve_git_ref,
)


def test_resolve_exact_sha():
    commit, mode = resolve_git_ref("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert mode == "exact"
    assert commit == "a" * 40


def test_resolve_branch_placeholder():
    commit, mode = resolve_git_ref("release/2.1")
    assert mode == "placeholder"
    assert commit == "resolved:release/2.1"


def test_resolve_rejects_bad_ref():
    with pytest.raises(GitResolveError):
        resolve_git_ref("../evil")


def test_require_exact_forbids_placeholder():
    resolver = build_git_resolver(mode="placeholder", require_exact=True)
    with pytest.raises(GitResolveError) as exc:
        resolver.resolve("Repo", "main")
    assert exc.value.code == "GIT_PLACEHOLDER_FORBIDDEN"


def test_ls_remote_resolver(monkeypatch):
    resolver = LsRemoteGitResolver(url_template="https://git.example/{repository}.git")

    def fake_run(cmd, **kwargs):
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\trefs/heads/main\n"
        completed.stderr = ""
        return completed

    monkeypatch.setattr("app.domain.git_resolve.subprocess.run", fake_run)
    result = resolver.resolve("MyRepo", "main")
    assert result.mode == "ls_remote"
    assert result.commit == "b" * 40


def test_http_api_resolver(monkeypatch):
    resolver = HttpApiGitResolver(base_url="https://git-api.example", token="t")

    class Resp:
        def read(self):
            return json.dumps({"sha": "c" * 40}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "app.domain.git_resolve.urllib.request.urlopen",
        lambda *a, **k: Resp(),
    )
    result = resolver.resolve("MyRepo", "develop")
    assert result.mode == "http_api"
    assert result.commit == "c" * 40


def test_build_git_resolver_ls_remote_requires_template():
    with pytest.raises(GitResolveError) as exc:
        build_git_resolver(mode="ls_remote")
    assert exc.value.code == "GIT_CONFIG"
