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
        # Step 1: ensure image (no build request yet)
        e1 = client.post("/api/v1/images/ensure", json={"environment": COLD_ENV}).json()
        assert e1["imageStatus"] == "CREATING"
        assert e1["ready"] is False
        profile_hash = e1["requestedProfileHash"]
        assert e1["matchedProfileHash"] == profile_hash

        e2 = client.post("/api/v1/images/ensure", json={"environment": COLD_ENV}).json()
        assert e2["imageStatus"] in {"CREATING", "VALIDATING"}
        assert e2["matchedProfileHash"] == profile_hash

        jenkins = get_jenkins_client()
        assert any(job == "msbuild-image-factory" for job, _ in jenkins.calls)

        # cold build must not start until READY
        denied = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ColdApp",
                    "gitRef": "main",
                    "solutionPath": "ColdApp.sln",
                },
                "environment": COLD_ENV,
                "matchedProfileHash": profile_hash,
                "imageDigest": "sha256:pending-not-ready",
            },
        )
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "IMAGE_NOT_READY"

        session = client.app.state.session_factory()
        image = session.query(BuildImage).filter_by(profile_hash=profile_hash).one()
        lease_id = image.lease_id
        session.close()
        assert lease_id

        raw = json.dumps({"leaseId": lease_id}).encode()
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

        status = client.get(f"/api/v1/images/{profile_hash}").json()
        assert status["ready"] is True
        assert status["image"]["digest"] == "sha256:cold-image-ready"

        # Step 2: start builds against READY image
        payload = {
            "project": {
                "repository": "ColdApp",
                "gitRef": "main",
                "solutionPath": "ColdApp.sln",
            },
            "environment": COLD_ENV,
            "matchedProfileHash": profile_hash,
            "imageDigest": "sha256:cold-image-ready",
        }
        r1 = client.post("/api/v1/build-requests", json=payload).json()
        r2 = client.post("/api/v1/build-requests", json=payload).json()
        assert r1["status"] == "BUILD_QUEUED"
        assert r2["status"] == "BUILD_QUEUED"
        assert r1["imageDigest"] == "sha256:cold-image-ready"
        assert any(job == "msbuild-project-build" for job, _ in jenkins.calls)


def test_stale_lease_still_rejected(tmp_path):
    with _client(tmp_path) as client:
        ensured = client.post("/api/v1/images/ensure", json={"environment": COLD_ENV}).json()
        profile_hash = ensured["requestedProfileHash"]
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
        ensured = client.post("/api/v1/images/ensure", json={"environment": COLD_ENV}).json()
        profile_hash = ensured["requestedProfileHash"]

        session = client.app.state.session_factory()
        image = session.query(BuildImage).filter_by(profile_hash=profile_hash).one()
        image.lease_expires_at = utcnow() - timedelta(minutes=1)
        session.commit()
        session.close()

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

        status = client.get(f"/api/v1/images/{profile_hash}").json()
        assert status["imageStatus"] == "FAILED"
        assert status["ready"] is False

        denied = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ColdApp",
                    "gitRef": "main",
                    "solutionPath": "ColdApp.sln",
                },
                "environment": COLD_ENV,
                "matchedProfileHash": profile_hash,
                "imageDigest": "sha256:pending-expired",
            },
        )
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "IMAGE_NOT_READY"


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
    assert "COPY install-manifest.json" in docker
    assert "COPY layout C:\\Layout" in docker
    assert "agent-base-ltsc2022@sha256:abc" in docker
    assert "company.build.profile-hash=\"abcdabcdabcd\"" in docker
    vs = generate_vsconfig(build_input)
    assert "Microsoft.Component.MSBuild" in vs["components"]

    mvp = dict(build_input)
    mvp["agentBase"] = {
        "image": "msbuild-agent-base",
        "digest": "sha256:agent-base-ltsc2022-mvp0001",
    }
    mvp_docker = generate_dockerfile(mvp, profile_hash="abcd" * 16)
    assert "msbuild-agent-base:ltsc2022" in mvp_docker
    assert "@sha256:agent-base" not in mvp_docker
