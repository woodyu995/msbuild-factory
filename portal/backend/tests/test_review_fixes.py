from __future__ import annotations

import json
import time
from datetime import timedelta

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.models import BuildImage, utcnow
from app.domain.project_validation import InvalidProjectInput, validate_solution_path
from app.main import create_app
from app.security.hmac_auth import sign_body, verify_hmac, CallbackAuthError
from app.services.jenkins import reset_jenkins_client


def _client(tmp_path, **env):
    import os

    os.environ["PORTAL_SIMULATE_WORKERS"] = env.get("simulate", "false")
    os.environ["PORTAL_ALLOW_INSECURE_DEFAULTS"] = "true"
    get_settings.cache_clear()
    reset_jenkins_client()
    app = create_app(database_url=f"sqlite:///{tmp_path / 'review.db'}")
    return TestClient(app)


def test_null_lease_status_callback_rejected(tmp_path):
    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ColdApp",
                    "gitRef": "main",
                    "solutionPath": "ColdApp.sln",
                },
                "environment": {
                    "visualStudio": "2022",
                    "dotnetFrameworks": ["4.8"],
                    "dotnetSdks": [],
                    "cppToolsets": ["v143"],
                    "windowsSdks": ["10.0.22621.0"],
                    "features": ["managed-desktop", "mfc"],
                    "reuseMode": "exactReuse",
                },
            },
        ).json()
        profile_hash = created["requestedProfileHash"]
        session = client.app.state.session_factory()
        image = session.query(BuildImage).filter_by(profile_hash=profile_hash).one()
        image.lease_id = None
        image.lease_owner = None
        session.commit()
        session.close()

        body = {
            "status": "READY",
            "leaseId": "anything",
            "imageDigest": "sha256:x",
            "capabilityProfile": {"visualStudio": "2022", "windowsBase": "ltsc2022"},
        }
        raw = json.dumps(body).encode()
        ts = str(int(time.time()))
        sig = sign_body("dev-callback-secret-change-me", ts, raw)
        resp = client.post(
            f"/internal/v1/images/{profile_hash}/status",
            content=raw,
            headers={"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig},
        )
        assert resp.status_code == 409


def test_simulate_disabled_by_default(tmp_path):
    with _client(tmp_path, simulate="false") as client:
        created = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ProductClient",
                    "gitRef": "main",
                    "solutionPath": "A.sln",
                },
                "environment": {
                    "visualStudio": "2022",
                    "dotnetFrameworks": ["4.8"],
                    "dotnetSdks": ["8.0"],
                    "cppToolsets": [],
                    "windowsSdks": [],
                    "features": ["managed-desktop"],
                },
            },
        ).json()
        resp = client.post(f"/api/v1/build-requests/{created['id']}/simulate")
        assert resp.status_code == 403


def test_factory_artifacts_requires_hmac(tmp_path):
    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ColdApp",
                    "gitRef": "main",
                    "solutionPath": "ColdApp.sln",
                },
                "environment": {
                    "visualStudio": "2022",
                    "dotnetFrameworks": ["4.8"],
                    "dotnetSdks": [],
                    "cppToolsets": ["v143"],
                    "windowsSdks": ["10.0.22621.0"],
                    "features": ["managed-desktop", "mfc"],
                    "reuseMode": "exactReuse",
                },
            },
        ).json()
        profile_hash = created["requestedProfileHash"]
        denied = client.post(f"/internal/v1/images/{profile_hash}/factory-artifacts", json={})
        assert denied.status_code == 401

        raw = b"{}"
        ts = str(int(time.time()))
        sig = sign_body("dev-callback-secret-change-me", ts, raw)
        ok = client.post(
            f"/internal/v1/images/{profile_hash}/factory-artifacts",
            content=raw,
            headers={"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig},
        )
        assert ok.status_code == 200
        assert "dockerfile" in ok.json()


def test_solution_path_rejects_traversal():
    try:
        validate_solution_path("../evil.sln")
        assert False, "expected InvalidProjectInput"
    except InvalidProjectInput:
        pass
    assert validate_solution_path("src/App.sln").endswith("App.sln")


def test_hmac_length_mismatch_is_401():
    try:
        verify_hmac(
            secret="dev-callback-secret-change-me",
            timestamp_header=str(int(time.time())),
            signature_header="ab",
            body=b"{}",
        )
        assert False
    except CallbackAuthError:
        pass


def test_invalid_build_event_transition(tmp_path):
    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ProductClient",
                    "gitRef": "main",
                    "solutionPath": "A.sln",
                },
                "environment": {
                    "visualStudio": "2022",
                    "dotnetFrameworks": ["4.8"],
                    "dotnetSdks": ["8.0"],
                    "cppToolsets": [],
                    "windowsSdks": [],
                    "features": ["managed-desktop"],
                },
            },
        ).json()
        # Jump from BUILD_QUEUED directly with IMAGE_BUILDING should fail
        body = {
            "requestId": created["id"],
            "eventType": "IMAGE_BUILDING",
            "message": "nope",
        }
        raw = json.dumps(body).encode()
        ts = str(int(time.time()))
        sig = sign_body("dev-callback-secret-change-me", ts, raw)
        resp = client.post(
            "/internal/v1/build-events",
            content=raw,
            headers={"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig},
        )
        assert resp.status_code == 409
