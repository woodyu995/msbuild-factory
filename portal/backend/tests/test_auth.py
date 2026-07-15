from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.security.actors import parse_api_tokens
from app.services.jenkins import reset_jenkins_client


@pytest.fixture()
def _reset_env(monkeypatch):
    get_settings.cache_clear()
    reset_jenkins_client()
    yield
    get_settings.cache_clear()


def test_parse_api_tokens():
    mapping = parse_api_tokens("alice:tok-a:admin|operator,bob:tok-b:builder")
    assert mapping["tok-a"].name == "alice"
    assert "admin" in mapping["tok-a"].roles
    assert mapping["tok-b"].can_simulate is False


def test_require_auth_rejects_missing_token(tmp_path, monkeypatch, _reset_env):
    monkeypatch.setenv("PORTAL_REQUIRE_AUTH", "true")
    monkeypatch.setenv("PORTAL_API_TOKENS", "alice:secret-token:builder")
    monkeypatch.setenv("PORTAL_SIMULATE_WORKERS", "false")
    get_settings.cache_clear()
    app = create_app(database_url=f"sqlite:///{tmp_path / 'auth.db'}")
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "Demo",
                    "gitRef": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "solutionPath": "Demo.sln",
                },
                "environment": {
                    "visualStudio": "2022",
                    "dotnetFrameworks": ["4.8"],
                    "dotnetSdks": ["8.0"],
                    "cppToolsets": [],
                    "windowsSdks": [],
                    "features": ["managed-desktop"],
                    "reuseMode": "preferCompatible",
                },
            },
            headers={"X-Actor": "spoofed"},
        )
        assert resp.status_code == 401


def test_bearer_token_sets_actor(tmp_path, monkeypatch, _reset_env):
    monkeypatch.setenv("PORTAL_REQUIRE_AUTH", "true")
    monkeypatch.setenv("PORTAL_API_TOKENS", "alice:secret-token:builder")
    monkeypatch.setenv("PORTAL_SIMULATE_WORKERS", "false")
    get_settings.cache_clear()
    app = create_app(database_url=f"sqlite:///{tmp_path / 'auth2.db'}")
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "Demo",
                    "gitRef": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "solutionPath": "Demo.sln",
                },
                "environment": {
                    "visualStudio": "2022",
                    "dotnetFrameworks": ["4.8"],
                    "dotnetSdks": ["8.0"],
                    "cppToolsets": [],
                    "windowsSdks": [],
                    "features": ["managed-desktop"],
                    "reuseMode": "preferCompatible",
                },
            },
            headers={"Authorization": "Bearer secret-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["commitResolution"] == "exact"
        assert data["resolvedCommit"] == "a" * 40
        # actor stored on request
        from sqlalchemy import select
        from app.db.models import BuildRequest

        session = client.app.state.session_factory()
        row = session.scalar(select(BuildRequest).where(BuildRequest.id == data["id"]))
        assert row.requested_by == "alice"
        session.close()


def test_bearer_wrong_length_is_401(tmp_path, monkeypatch, _reset_env):
    monkeypatch.setenv("PORTAL_REQUIRE_AUTH", "true")
    monkeypatch.setenv("PORTAL_API_TOKENS", "alice:secret-token:builder")
    monkeypatch.setenv("PORTAL_SIMULATE_WORKERS", "false")
    get_settings.cache_clear()
    app = create_app(database_url=f"sqlite:///{tmp_path / 'auth3.db'}")
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "Demo",
                    "gitRef": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "solutionPath": "Demo.sln",
                },
                "environment": {
                    "visualStudio": "2022",
                    "dotnetFrameworks": ["4.8"],
                    "dotnetSdks": ["8.0"],
                    "cppToolsets": [],
                    "windowsSdks": [],
                    "features": ["managed-desktop"],
                    "reuseMode": "preferCompatible",
                },
            },
            headers={"Authorization": "Bearer short"},
        )
        assert resp.status_code == 401


def test_simulate_requires_operator_role_when_auth_on(tmp_path, monkeypatch, _reset_env):
    monkeypatch.setenv("PORTAL_SIMULATE_WORKERS", "true")
    monkeypatch.setenv("PORTAL_API_TOKENS", "bob:builder-tok:builder,op:op-tok:operator")
    monkeypatch.setenv("PORTAL_REQUIRE_AUTH", "false")
    get_settings.cache_clear()
    app = create_app(database_url=f"sqlite:///{tmp_path / 'sim.db'}")
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "Demo",
                    "gitRef": "main",
                    "solutionPath": "Demo.sln",
                },
                "environment": {
                    "visualStudio": "2022",
                    "dotnetFrameworks": ["4.8"],
                    "dotnetSdks": ["8.0"],
                    "cppToolsets": [],
                    "windowsSdks": [],
                    "features": ["managed-desktop"],
                    "reuseMode": "preferCompatible",
                },
            },
            headers={"Authorization": "Bearer builder-tok"},
        ).json()
        denied = client.post(
            f"/api/v1/build-requests/{created['id']}/simulate",
            headers={"Authorization": "Bearer builder-tok"},
        )
        assert denied.status_code == 403
        allowed = client.post(
            f"/api/v1/build-requests/{created['id']}/simulate",
            headers={"Authorization": "Bearer op-tok"},
        )
        assert allowed.status_code == 200
