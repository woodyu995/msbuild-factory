from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.security.hmac_auth import sign_body
import time


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PORTAL_SIMULATE_WORKERS", "false")
    get_settings.cache_clear()
    from app.services.jenkins import reset_jenkins_client

    reset_jenkins_client()
    db_path = tmp_path / "test.db"
    app = create_app(database_url=f"sqlite:///{db_path}")
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()
    monkeypatch.delenv("PORTAL_SIMULATE_WORKERS", raising=False)


def test_options_contains_presets(client: TestClient):
    resp = client.get("/api/v1/build-environment/options")
    assert resp.status_code == 200
    data = resp.json()
    assert data["catalogVersion"] == "2026.07"
    assert len(data["presets"]) == 4
    assert data["mvpFactoryEnabled"] is True


def test_validate_exact_preset(client: TestClient):
    env = {
        "visualStudio": "2022",
        "dotnetFrameworks": ["4.8"],
        "dotnetSdks": ["8.0"],
        "cppToolsets": [],
        "windowsSdks": [],
        "features": ["managed-desktop"],
        "reuseMode": "preferCompatible",
    }
    resp = client.post("/api/v1/build-environment/validate", json={"environment": env})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["matchType"] == "EXACT"
    assert data["action"] == "REUSE_EXACT"
    assert data["image"]["digest"].startswith("sha256:")


def test_validate_compatible_superset(client: TestClient):
    # Request only net48 managed; should reuse managed-dotnet8 or managed-net48.
    # Prefer exact net48 if seeded.
    env = {
        "visualStudio": "2022",
        "dotnetFrameworks": ["4.8"],
        "dotnetSdks": [],
        "cppToolsets": [],
        "windowsSdks": [],
        "features": ["managed-desktop"],
        "reuseMode": "preferCompatible",
    }
    resp = client.post("/api/v1/build-environment/validate", json={"environment": env})
    data = resp.json()
    assert data["valid"] is True
    assert data["matchType"] in {"EXACT", "COMPATIBLE_SUPERSET"}
    assert data["action"] in {"REUSE_EXACT", "REUSE_COMPATIBLE"}


def test_validate_rejects_unknown_combination(client: TestClient):
    env = {
        "visualStudio": "2019",
        "dotnetFrameworks": ["4.8"],
        "dotnetSdks": ["8.0"],
        "cppToolsets": [],
        "windowsSdks": [],
        "features": ["managed-desktop"],
    }
    resp = client.post("/api/v1/build-environment/validate", json={"environment": env})
    data = resp.json()
    assert data["valid"] is False
    assert data["action"] == "REJECTED"


def test_create_build_request_and_idempotency(client: TestClient):
    from tests.helpers import HOT_ENV, build_payload, ensure_ready

    ensured = ensure_ready(client, HOT_ENV)
    payload = build_payload(
        ensured=ensured,
        project={
            "repository": "ProductClient",
            "gitRef": "release/2.1",
            "solutionPath": "ProductClient.sln",
            "configuration": "Release",
            "platform": "x64",
        },
    )
    payload["nuget"] = {"mode": "repo-packages-and-internal-feed"}
    headers = {"Idempotency-Key": "idem-1", "X-Actor": "tester"}
    r1 = client.post("/api/v1/build-requests", json=payload, headers=headers)
    r2 = client.post("/api/v1/build-requests", json=payload, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["status"] == "BUILD_QUEUED"
    assert r1.json()["matchType"] == "EXACT"

    rid = r1.json()["id"]
    got = client.get(f"/api/v1/build-requests/{rid}")
    assert got.status_code == 200
    assert got.json()["resolvedCommit"].startswith("resolved:")


def test_internal_callback_hmac(client: TestClient):
    from tests.helpers import HOT_ENV, build_payload, ensure_ready

    ensured = ensure_ready(client, HOT_ENV)
    payload = build_payload(
        ensured=ensured,
        project={
            "repository": "ProductClient",
            "gitRef": "abc",
            "solutionPath": "A.sln",
        },
    )
    created = client.post("/api/v1/build-requests", json=payload).json()
    body = {
        "requestId": created["id"],
        "eventType": "BUILDING",
        "message": "started",
        "jenkinsBuildNumber": 10,
    }
    raw = json.dumps(body).encode("utf-8")
    ts = str(int(time.time()))
    secret = "dev-callback-secret-change-me"
    sig = sign_body(secret, ts, raw)
    resp = client.post(
        "/internal/v1/build-events",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": ts,
            "X-Signature": sig,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "BUILDING"

    # bad signature
    bad = client.post(
        "/internal/v1/build-events",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": ts,
            "X-Signature": "deadbeef",
        },
    )
    assert bad.status_code == 401


def test_stale_lease_rejected(client: TestClient):
    # create a CREATING image row via direct session
    from app.db.models import BuildImage
    from app.main import create_app

    session = client.app.state.session_factory()
    img = BuildImage(
        profile_hash="a" * 64,
        image_repository="registry.internal/build/msbuild-profile",
        image_tag="creating",
        image_digest="sha256:pending",
        status="CREATING",
        base_image_digest="sha256:base",
        windows_base="ltsc2022",
        vs_generation="2022",
        capability_profile_json="{}",
        catalog_version="2026.07",
        lease_id="lease-new",
        lease_owner="factory-1",
    )
    session.add(img)
    session.commit()
    session.close()

    body = {
        "status": "FAILED",
        "leaseId": "lease-old",
        "message": "stale",
    }
    raw = json.dumps(body).encode("utf-8")
    ts = str(int(time.time()))
    sig = sign_body("dev-callback-secret-change-me", ts, raw)
    resp = client.post(
        f"/internal/v1/images/{'a'*64}/status",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": ts,
            "X-Signature": sig,
        },
    )
    assert resp.status_code == 409