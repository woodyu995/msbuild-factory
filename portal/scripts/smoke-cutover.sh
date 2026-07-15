#!/usr/bin/env bash
# Local / CI smoke for Portal cutover wiring (no Windows Docker required).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORTAL_URL="${PORTAL_URL:-http://127.0.0.1:8000}"
HMAC_SECRET="${PORTAL_CALLBACK_HMAC_SECRET:-dev-callback-secret-change-me}"
DB_PATH="${SMOKE_DB_PATH:-/tmp/portal-smoke.db}"
export PORTAL_ALLOW_INSECURE_DEFAULTS="${PORTAL_ALLOW_INSECURE_DEFAULTS:-true}"
export PORTAL_SIMULATE_WORKERS="${PORTAL_SIMULATE_WORKERS:-false}"
export PORTAL_DEFAULT_ACTOR_ROLES="${PORTAL_DEFAULT_ACTOR_ROLES:-operator,builder}"
export PORTAL_CALLBACK_HMAC_SECRET="$HMAC_SECRET"

cd "$ROOT/portal/backend"
PYTHONPATH=. python3 - <<'PY'
from pathlib import Path
import os, json, time, urllib.request

from app.config import get_settings
get_settings.cache_clear()
from app.main import create_app
from app.security.hmac_auth import sign_body
from fastapi.testclient import TestClient

db = Path(os.environ.get("SMOKE_DB_PATH", "/tmp/portal-smoke.db"))
if db.exists():
    db.unlink()
app = create_app(database_url=f"sqlite:///{db}")

with TestClient(app) as client:
    health = client.get("/healthz").json()
    assert health["status"] == "ok", health
    print("OK healthz", health)

    opts = client.get("/api/v1/build-environment/options").json()
    assert opts["catalogVersion"], opts
    print("OK options", opts["catalogVersion"], "presets", len(opts["presets"]))

    hot_env = {
        "visualStudio": "2022",
        "dotnetFrameworks": ["4.8"],
        "dotnetSdks": ["8.0"],
        "cppToolsets": [],
        "windowsSdks": [],
        "features": ["managed-desktop"],
        "reuseMode": "preferCompatible",
    }

    # Step 1: ensure READY preset image
    ensured = client.post("/api/v1/images/ensure", json={"environment": hot_env})
    assert ensured.status_code == 200, ensured.text
    ensure_body = ensured.json()
    assert ensure_body["ready"] is True, ensure_body
    print("OK ensure preset", ensure_body["matchType"], ensure_body["image"]["digest"][:20])

    # Step 2: start build against READY image
    created = client.post(
        "/api/v1/build-requests",
        headers={"Idempotency-Key": "smoke-preset-1"},
        json={
            "project": {
                "repository": "ProductClient",
                "gitRef": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "solutionPath": "ProductClient.sln",
            },
            "environment": hot_env,
            "matchedProfileHash": ensure_body["matchedProfileHash"],
            "imageDigest": ensure_body["image"]["digest"],
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "BUILD_QUEUED", body
    assert body["commitResolution"] == "exact", body
    print("OK preset reuse", body["id"], body["matchType"])

    # Idempotency conflict
    conflict = client.post(
        "/api/v1/build-requests",
        headers={"Idempotency-Key": "smoke-preset-1"},
        json={
            "project": {
                "repository": "Other",
                "gitRef": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "solutionPath": "Other.sln",
            },
            "environment": hot_env,
            "matchedProfileHash": ensure_body["matchedProfileHash"],
            "imageDigest": ensure_body["image"]["digest"],
        },
    )
    assert conflict.status_code == 409, conflict.text
    print("OK idempotency conflict")

    cold_env = {
        "visualStudio": "2022",
        "dotnetFrameworks": ["4.8"],
        "dotnetSdks": [],
        "cppToolsets": ["v143"],
        "windowsSdks": ["10.0.22621.0"],
        "features": ["managed-desktop", "mfc"],
        "reuseMode": "exactReuse",
    }

    # Cold: ensure starts factory; build must 409 until READY
    cold = client.post("/api/v1/images/ensure", json={"environment": cold_env}).json()
    assert cold["imageStatus"] == "CREATING", cold
    profile = cold["matchedProfileHash"]
    denied = client.post(
        "/api/v1/build-requests",
        json={
            "project": {
                "repository": "ColdApp",
                "gitRef": "main",
                "solutionPath": "ColdApp.sln",
            },
            "environment": cold_env,
        },
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["detail"]["code"] == "IMAGE_NOT_READY"
    print("OK cold build blocked until READY")

    from app.db.models import BuildImage
    session = client.app.state.session_factory()
    image = session.query(BuildImage).filter_by(profile_hash=profile).one()
    lease = image.lease_id
    session.close()

    raw = json.dumps({"leaseId": lease}).encode()
    ts = str(int(time.time()))
    sig = sign_body(os.environ["PORTAL_CALLBACK_HMAC_SECRET"], ts, raw)
    arts = client.post(
        f"/internal/v1/images/{profile}/factory-artifacts",
        content=raw,
        headers={"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig},
    )
    assert arts.status_code == 200, arts.text
    assert "dockerfile" in arts.json()
    print("OK factory-artifacts with lease")

    # Simulate factory then start build
    client.app.state.settings.simulate_workers = True
    sim_img = client.post(f"/api/v1/images/{profile}/simulate")
    assert sim_img.status_code == 200, sim_img.text
    status = client.get(f"/api/v1/images/{profile}").json()
    assert status["ready"] is True, status

    cold_build = client.post(
        "/api/v1/build-requests",
        json={
            "project": {
                "repository": "ColdApp",
                "gitRef": "main",
                "solutionPath": "ColdApp.sln",
            },
            "environment": cold_env,
            "matchedProfileHash": profile,
            "imageDigest": status["image"]["digest"],
        },
    ).json()
    assert cold_build["status"] == "BUILD_QUEUED", cold_build
    sim = client.post(f"/api/v1/build-requests/{cold_build['id']}/simulate")
    assert sim.status_code == 200, sim.text
    got = client.get(f"/api/v1/build-requests/{cold_build['id']}").json()
    assert got["status"] == "SUCCEEDED", got
    print("OK simulate cold -> SUCCEEDED")

    # Reconcile endpoint HMAC
    raw = b"{}"
    ts = str(int(time.time()))
    sig = sign_body(os.environ["PORTAL_CALLBACK_HMAC_SECRET"], ts, raw)
    rec = client.post(
        "/internal/v1/reconcile/leases",
        content=raw,
        headers={"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig},
    )
    assert rec.status_code == 200, rec.text
    print("OK reconcile", rec.json())

print("SMOKE PASSED")
PY
