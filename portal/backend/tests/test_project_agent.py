from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

AGENT = (
    Path(__file__).resolve().parents[3]
    / "jenkins"
    / "shared-library"
    / "scripts"
    / "portal_project_agent.py"
)


def _load_agent():
    spec = importlib.util.spec_from_file_location("portal_project_agent", AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_project_agent_resolve_sends_bearer(monkeypatch):
    mod = _load_agent()
    seen = {}

    def fake_request(method, url, **kwargs):
        seen["headers_token"] = kwargs.get("api_token")
        return {
            "id": "br-1",
            "status": "BUILD_QUEUED",
            "repository": "Demo",
            "resolvedCommit": "a" * 40,
            "solutionPath": "Demo.sln",
            "configuration": "Release",
            "platform": "x64",
            "nugetMode": "repo-packages",
            "imageDigest": "sha256:x",
        }

    monkeypatch.setattr(mod, "_request", fake_request)
    args = SimpleNamespace(
        portal_url="http://portal",
        request_id="br-1",
        api_token="jenkins-bot-token",
    )
    mod.cmd_resolve(args)
    assert seen["headers_token"] == "jenkins-bot-token"


def test_project_agent_token_from_env(monkeypatch):
    mod = _load_agent()
    monkeypatch.setenv("PORTAL_API_TOKEN", "env-token")
    args = SimpleNamespace(api_token="", hmac_secret="")
    assert mod._resolve_api_token(args) == "env-token"
