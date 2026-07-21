from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.models import BuildImage
from app.main import create_app
from app.services.jenkins import reset_jenkins_client


COLD_A = {
    "visualStudio": "2022",
    "dotnetFrameworks": ["4.8"],
    "dotnetSdks": [],
    "cppToolsets": ["v143"],
    "windowsSdks": ["10.0.22621.0"],
    "features": ["managed-desktop", "mfc"],
    "reuseMode": "exactReuse",
}

COLD_B = {
    "visualStudio": "2022",
    "dotnetFrameworks": ["4.8"],
    "dotnetSdks": ["8.0"],
    "cppToolsets": ["v143"],
    "windowsSdks": ["10.0.22621.0"],
    "features": ["managed-desktop", "mfc", "atl"],
    "reuseMode": "exactReuse",
}


def _client(tmp_path):
    import os

    os.environ["PORTAL_SIMULATE_WORKERS"] = "false"
    get_settings.cache_clear()
    reset_jenkins_client()
    app = create_app(database_url=f"sqlite:///{tmp_path / 'ensure-err.db'}")
    return TestClient(app)


def test_ensure_factory_busy_returns_503(tmp_path):
    # MAX_GLOBAL_CREATING=1 (aligned with Jenkins disableConcurrentBuilds)
    with _client(tmp_path) as client:
        a = client.post("/api/v1/images/ensure", json={"environment": COLD_A})
        assert a.status_code == 200
        assert a.json()["imageStatus"] == "CREATING"
        hash_a = a.json()["matchedProfileHash"]

        busy = client.post("/api/v1/images/ensure", json={"environment": COLD_B})
        assert busy.status_code == 503
        detail = busy.json()["detail"]
        assert detail["code"] == "FACTORY_BUSY"
        assert detail["retryable"] is True
        assert detail["blocking"]
        assert detail["blocking"][0]["profileHash"] == hash_a

        # Same profile joins the in-flight slot instead of 503.
        again = client.post("/api/v1/images/ensure", json={"environment": COLD_A})
        assert again.status_code == 200
        assert again.json()["imageStatus"] == "CREATING"
        assert again.json()["factoryLeaseId"]


def test_ensure_frees_slot_after_expired_lease_reconcile(tmp_path):
    from datetime import timedelta

    from app.db.models import utcnow

    with _client(tmp_path) as client:
        a = client.post("/api/v1/images/ensure", json={"environment": COLD_A})
        assert a.status_code == 200
        hash_a = a.json()["matchedProfileHash"]

        session = client.app.state.session_factory()
        image = session.query(BuildImage).filter_by(profile_hash=hash_a).one()
        image.lease_expires_at = utcnow() - timedelta(minutes=1)
        session.commit()
        session.close()

        # Different profile can start after expired holder is reconciled.
        b = client.post("/api/v1/images/ensure", json={"environment": COLD_B})
        assert b.status_code == 200, b.text
        assert b.json()["imageStatus"] == "CREATING"


def test_ensure_factory_disabled_returns_400(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_FACTORY_ENABLED", "false")
    get_settings.cache_clear()
    reset_jenkins_client()
    app = create_app(database_url=f"sqlite:///{tmp_path / 'ensure-off.db'}")
    with TestClient(app) as client:
        resp = client.post("/api/v1/images/ensure", json={"environment": COLD_A})
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "PROFILE_REJECTED"
    get_settings.cache_clear()
    monkeypatch.delenv("PORTAL_FACTORY_ENABLED", raising=False)


def test_ensure_quarantined_returns_409(tmp_path):
    with _client(tmp_path) as client:
        created = client.post("/api/v1/images/ensure", json={"environment": COLD_A}).json()
        profile_hash = created["matchedProfileHash"]
        session = client.app.state.session_factory()
        image = session.query(BuildImage).filter_by(profile_hash=profile_hash).one()
        image.status = "QUARANTINED"
        image.lease_id = None
        session.commit()
        session.close()

        resp = client.post("/api/v1/images/ensure", json={"environment": COLD_A})
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "IMAGE_QUARANTINED"
