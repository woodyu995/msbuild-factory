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

        busy = client.post("/api/v1/images/ensure", json={"environment": COLD_B})
        assert busy.status_code == 503
        detail = busy.json()["detail"]
        assert detail["code"] == "FACTORY_BUSY"
        assert detail["retryable"] is True


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
