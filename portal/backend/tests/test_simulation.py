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
    import os

    prev_sim = os.environ.get("PORTAL_SIMULATE_WORKERS")
    prev_roles = os.environ.get("PORTAL_DEFAULT_ACTOR_ROLES")
    os.environ["PORTAL_SIMULATE_WORKERS"] = "true" if simulate else "false"
    # Simulate API requires operator/admin; grant for local sim tests.
    os.environ["PORTAL_DEFAULT_ACTOR_ROLES"] = "operator,builder"
    get_settings.cache_clear()
    db_path = tmp_path / "sim.db"
    app = create_app(database_url=f"sqlite:///{db_path}")
    client = TestClient(app)

    class _Guarded:
        def __enter__(self):
            return client.__enter__()

        def __exit__(self, *args):
            result = client.__exit__(*args)
            get_settings.cache_clear()
            if prev_sim is None:
                os.environ.pop("PORTAL_SIMULATE_WORKERS", None)
            else:
                os.environ["PORTAL_SIMULATE_WORKERS"] = prev_sim
            if prev_roles is None:
                os.environ.pop("PORTAL_DEFAULT_ACTOR_ROLES", None)
            else:
                os.environ["PORTAL_DEFAULT_ACTOR_ROLES"] = prev_roles
            return result

    return _Guarded()


def test_manual_auto_simulate_cold_to_succeeded(tmp_path):
    with _client(tmp_path, simulate=False) as client:
        ensured = client.post("/api/v1/images/ensure", json={"environment": COLD_ENV}).json()
        assert ensured["imageStatus"] == "CREATING"
        profile_hash = ensured["matchedProfileHash"]

        client.app.state.settings.simulate_workers = True
        factory = client.post(f"/api/v1/images/{profile_hash}/simulate").json()
        assert factory["ok"] is True
        assert factory["imageDigest"].startswith("sha256:simulated-")

        status = client.get(f"/api/v1/images/{profile_hash}").json()
        assert status["ready"] is True

        created = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ColdApp",
                    "gitRef": "main",
                    "solutionPath": "ColdApp.sln",
                },
                "environment": COLD_ENV,
                "matchedProfileHash": profile_hash,
                "imageDigest": status["image"]["digest"],
            },
        ).json()
        assert created["status"] in {"BUILD_QUEUED", "SUCCEEDED"}

        # Background auto-advance may already have finished; simulate is idempotent.
        advanced = client.post(f"/api/v1/build-requests/{created['id']}/simulate").json()
        assert advanced["finalStatus"] == "SUCCEEDED"

        got = client.get(f"/api/v1/build-requests/{created['id']}").json()
        assert got["status"] == "SUCCEEDED"
        assert got["imageDigest"].startswith("sha256:simulated-")


def test_preset_reuse_simulate_project_only(tmp_path):
    with _client(tmp_path, simulate=False) as client:
        ensured = client.post(
            "/api/v1/images/ensure",
            json={
                "environment": {
                    "visualStudio": "2022",
                    "dotnetFrameworks": ["4.8"],
                    "dotnetSdks": ["8.0"],
                    "cppToolsets": [],
                    "windowsSdks": [],
                    "features": ["managed-desktop"],
                    "reuseMode": "preferCompatible",
                }
            },
        ).json()
        assert ensured["ready"] is True

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
                "matchedProfileHash": ensured["matchedProfileHash"],
                "imageDigest": ensured["image"]["digest"],
            },
        ).json()
        assert created["status"] == "BUILD_QUEUED"
        assert created["matchType"] == "EXACT"

        client.app.state.settings.simulate_workers = True
        advanced = client.post(f"/api/v1/build-requests/{created['id']}/simulate").json()
        assert advanced["finalStatus"] == "SUCCEEDED"


def test_background_auto_simulate(tmp_path):
    with _client(tmp_path, simulate=True) as client:
        ensured = client.post("/api/v1/images/ensure", json={"environment": COLD_ENV}).json()
        profile_hash = ensured["matchedProfileHash"] or ensured["requestedProfileHash"]
        # Background factory simulate should finish after ensure response.
        status = client.get(f"/api/v1/images/{profile_hash}").json()
        assert status["ready"] is True

        created = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ColdApp",
                    "gitRef": "main",
                    "solutionPath": "ColdApp.sln",
                },
                "environment": COLD_ENV,
                "matchedProfileHash": profile_hash,
                "imageDigest": status["image"]["digest"],
            },
        ).json()
        got = client.get(f"/api/v1/build-requests/{created['id']}").json()
        assert got["status"] == "SUCCEEDED"


def test_healthz_reports_simulation_flag(tmp_path):
    with _client(tmp_path, simulate=True) as client:
        health = client.get("/healthz").json()
        assert health["simulateWorkers"] is True
        assert health["jenkinsConfigured"] is False
