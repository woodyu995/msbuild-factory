from __future__ import annotations

import json
import time
from datetime import timedelta

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.models import BuildImage, utcnow
from app.main import create_app
from app.security.hmac_auth import sign_body
from app.services.jenkins import get_jenkins_client, reset_jenkins_client


def _client(tmp_path):
    import os

    os.environ["PORTAL_SIMULATE_WORKERS"] = "false"
    get_settings.cache_clear()
    reset_jenkins_client()
    db_path = tmp_path / "factory.db"
    app = create_app(database_url=f"sqlite:///{db_path}")
    return TestClient(app)


def _hmac_headers(body: dict) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode("utf-8")
    ts = str(int(time.time()))
    sig = sign_body("dev-callback-secret-change-me", ts, raw)
    return raw, {
        "Content-Type": "application/json",
        "X-Timestamp": ts,
        "X-Signature": sig,
    }


COLD_ENV = {
    "visualStudio": "2022",
    "dotnetFrameworks": ["4.8"],
    "dotnetSdks": [],
    "cppToolsets": ["v143"],
    "windowsSdks": ["10.0.22621.0"],
    "features": ["managed-desktop", "mfc"],
    "reuseMode": "exactReuse",  # force miss against broader presets
}


def test_options_factory_enabled(tmp_path):
    with _client(tmp_path) as client:
        data = client.get("/api/v1/build-environment/options").json()
        assert data["mvpFactoryEnabled"] is True


def test_validate_requests_image_creation_for_cold_exact(tmp_path):
    with _client(tmp_path) as client:
        resp = client.post(
            "/api/v1/build-environment/validate",
            json={"environment": COLD_ENV},
        )
        data = resp.json()
        assert data["action"] == "IMAGE_CREATION_REQUIRED"
        assert data["valid"] is True
        assert data["requestedProfileHash"]


def test_factory_queue_waiter_and_ready_callback(tmp_path):
    with _client(tmp_path) as client:
        payload = {
            "project": {
                "repository": "ColdApp",
                "gitRef": "main",
                "solutionPath": "ColdApp.sln",
            },
            "environment": COLD_ENV,
        }
        r1 = client.post("/api/v1/build-requests", json=payload).json()
        assert r1["status"] == "IMAGE_BUILD_QUEUED"
        assert r1["matchType"] == "CREATED"
        profile_hash = r1["requestedProfileHash"]

        r2 = client.post("/api/v1/build-requests", json=payload).json()
        assert r2["status"] == "IMAGE_WAITING"
        assert r2["requestedProfileHash"] == profile_hash

        jenkins = get_jenkins_client()
        assert any(job == "msbuild-image-factory" for job, _ in jenkins.calls)

        # fetch artifacts
        import time
        from app.security.hmac_auth import sign_body

        raw = b"{}"
        ts = str(int(time.time()))
        sig = sign_body("dev-callback-secret-change-me", ts, raw)
        arts = client.post(
            f"/internal/v1/images/{profile_hash}/factory-artifacts",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Timestamp": ts,
                "X-Signature": sig,
            },
        ).json()
        assert "FROM ${BASE_IMAGE}" in arts["dockerfile"]
        assert "Microsoft.Component.MSBuild" in arts["vsconfig"]["components"]
        assert arts["installManifest"]["visualStudio"]["generation"] == "2022"

        # load lease id
        session = client.app.state.session_factory()
        image = session.query(BuildImage).filter_by(profile_hash=profile_hash).one()
        lease_id = image.lease_id
        session.close()
        assert lease_id

        ready_body = {
            "status": "READY",
            "leaseId": lease_id,
            "message": "validated",
            "imageDigest": "sha256:cold-image-ready",
            "capabilityProfile": {
                "visualStudio": "2022",
                "dotnetFrameworks": ["4.8"],
                "dotnetSdks": [],
                "cppToolsets": ["v143"],
                "windowsSdks": ["10.0.22621.0"],
                "features": ["managed-desktop", "mfc"],
                "windowsBase": "ltsc2022",
                "customSdks": [],
            },
        }
        raw, headers = _hmac_headers(ready_body)
        ready = client.post(
            f"/internal/v1/images/{profile_hash}/status",
            content=raw,
            headers=headers,
        )
        assert ready.status_code == 200
        assert ready.json()["status"] == "READY"
        assert set(ready.json()["affectedRequestIds"]) >= {r1["id"], r2["id"]}

        g1 = client.get(f"/api/v1/build-requests/{r1['id']}").json()
        g2 = client.get(f"/api/v1/build-requests/{r2['id']}").json()
        assert g1["status"] == "BUILD_QUEUED"
        assert g2["status"] == "BUILD_QUEUED"
        assert g1["imageDigest"] == "sha256:cold-image-ready"
        assert any(job == "msbuild-project-build" for job, _ in jenkins.calls)


def test_stale_lease_still_rejected(tmp_path):
    with _client(tmp_path) as client:
        payload = {
            "project": {
                "repository": "ColdApp",
                "gitRef": "main",
                "solutionPath": "ColdApp.sln",
            },
            "environment": COLD_ENV,
        }
        created = client.post("/api/v1/build-requests", json=payload).json()
        profile_hash = created["requestedProfileHash"]
        body = {"status": "FAILED", "leaseId": "not-the-lease", "message": "stale"}
        raw, headers = _hmac_headers(body)
        resp = client.post(
            f"/internal/v1/images/{profile_hash}/status",
            content=raw,
            headers=headers,
        )
        assert resp.status_code == 409


def test_reconcile_expired_lease(tmp_path):
    with _client(tmp_path) as client:
        payload = {
            "project": {
                "repository": "ColdApp",
                "gitRef": "main",
                "solutionPath": "ColdApp.sln",
            },
            "environment": COLD_ENV,
        }
        created = client.post("/api/v1/build-requests", json=payload).json()
        profile_hash = created["requestedProfileHash"]

        session = client.app.state.session_factory()
        image = session.query(BuildImage).filter_by(profile_hash=profile_hash).one()
        image.lease_expires_at = utcnow() - timedelta(minutes=1)
        session.commit()
        session.close()

        import time
        from app.security.hmac_auth import sign_body

        raw = b"{}"
        ts = str(int(time.time()))
        sig = sign_body("dev-callback-secret-change-me", ts, raw)
        result = client.post(
            "/internal/v1/reconcile/leases",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Timestamp": ts,
                "X-Signature": sig,
            },
        ).json()
        assert profile_hash in result["expiredImages"]
        assert created["id"] in result["affectedRequests"]

        got = client.get(f"/api/v1/build-requests/{created['id']}").json()
        assert got["status"] == "IMAGE_BUILD_FAILED"


def test_dockerfile_generation_unit():
    from app.domain.dockerfile_gen import generate_dockerfile, generate_vsconfig

    build_input = {
        "agentBase": {
            "image": "registry.internal/build/agent-base-ltsc2022",
            "digest": "sha256:abc",
        },
        "windowsBase": {"name": "ltsc2022", "digest": "sha256:base"},
        "visualStudio": {
            "generation": "2022",
            "layoutRelease": "vs2022-17.14.x",
            "components": ["Microsoft.VisualStudio.Workload.ManagedDesktopBuildTools"],
        },
        "templateVersion": "image-template-3",
        "dotnetFrameworkTargetingPacks": [],
        "dotnetSdks": [],
        "windowsSdks": [],
        "cppToolsets": [],
        "features": ["managed-desktop"],
        "customSdks": [],
    }
    docker = generate_dockerfile(build_input, profile_hash="abcd" * 16)
    assert "Install-BuildEnvironment.cmd" in docker
    assert "company.build.profile-hash=\"abcdabcdabcd\"" in docker
    vs = generate_vsconfig(build_input)
    assert "Microsoft.Component.MSBuild" in vs["components"]
