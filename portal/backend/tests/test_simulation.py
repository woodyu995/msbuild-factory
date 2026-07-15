from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.jenkins import reset_jenkins_client


COLD_ENV = {
    "visualStudio": "2022",
    "dotnetFrameworks": ["4.8"],
    "dotnetSdks": [],
    "cppToolsets": ["v143"],
    "windowsSdks": ["10.0.22621.0"],
    "features": ["managed-desktop", "mfc"],
    "reuseMode": "exactReuse",
}


def _client(tmp_path, *, simulate: bool = False):
    get_settings.cache_clear()
    reset_jenkins_client()
    # Rebuild settings with env-like override by constructing Settings after monkeypatching is hard;
    # mutate cached settings object after create_app... create_app calls get_settings once.
    # So set env via os.environ before cache clear.
    import os

    os.environ["PORTAL_SIMULATE_WORKERS"] = "true" if simulate else "false"
    get_settings.cache_clear()
    db_path = tmp_path / "sim.db"
    app = create_app(database_url=f"sqlite:///{db_path}")
    return TestClient(app)


def test_manual_auto_simulate_cold_to_succeeded(tmp_path):
    with _client(tmp_path, simulate=False) as client:
        created = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ColdApp",
                    "gitRef": "main",
                    "solutionPath": "ColdApp.sln",
                },
                "environment": COLD_ENV,
            },
        ).json()
        assert created["status"] == "IMAGE_BUILD_QUEUED"

        advanced = client.post(f"/api/v1/build-requests/{created['id']}/simulate").json()
        assert advanced["finalStatus"] == "SUCCEEDED"
        assert any("factory" in step for step in advanced["steps"])
        assert any("projectBuild" in step for step in advanced["steps"])

        got = client.get(f"/api/v1/build-requests/{created['id']}").json()
        assert got["status"] == "SUCCEEDED"
        assert got["imageDigest"].startswith("sha256:simulated-")


def test_preset_reuse_simulate_project_only(tmp_path):
    with _client(tmp_path, simulate=False) as client:
        created = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ProductClient",
                    "gitRef": "release/2.1",
                    "solutionPath": "ProductClient.sln",
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
        ).json()
        assert created["status"] == "BUILD_QUEUED"
        assert created["matchType"] == "EXACT"

        advanced = client.post(f"/api/v1/build-requests/{created['id']}/simulate").json()
        assert advanced["finalStatus"] == "SUCCEEDED"


def test_background_auto_simulate(tmp_path):
    with _client(tmp_path, simulate=True) as client:
        # TestClient runs background tasks before returning response... actually
        # after the response is sent. Starlette runs them after request completes.
        created = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ColdApp",
                    "gitRef": "main",
                    "solutionPath": "ColdApp.sln",
                },
                "environment": COLD_ENV,
            },
        ).json()
        # Immediately after POST, status may still be queued; background should finish.
        got = client.get(f"/api/v1/build-requests/{created['id']}").json()
        assert got["status"] == "SUCCEEDED"


def test_healthz_reports_simulation_flag(tmp_path):
    with _client(tmp_path, simulate=True) as client:
        health = client.get("/healthz").json()
        assert health["simulateWorkers"] is True
        assert health["jenkinsConfigured"] is False
