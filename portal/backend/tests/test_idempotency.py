from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.jenkins import reset_jenkins_client


def _client(tmp_path: Path):
    get_settings.cache_clear()
    reset_jenkins_client()
    app = create_app(database_url=f"sqlite:///{tmp_path / 'idem.db'}")
    return TestClient(app)


PAYLOAD = {
    "project": {
        "repository": "ProductClient",
        "gitRef": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
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
}


def test_idempotency_returns_same_request(tmp_path):
    with _client(tmp_path) as client:
        headers = {"Idempotency-Key": "idem-same"}
        a = client.post("/api/v1/build-requests", json=PAYLOAD, headers=headers).json()
        b = client.post("/api/v1/build-requests", json=PAYLOAD, headers=headers).json()
        assert a["id"] == b["id"]


def test_idempotency_conflict_on_different_payload(tmp_path):
    with _client(tmp_path) as client:
        headers = {"Idempotency-Key": "idem-conflict"}
        first = client.post("/api/v1/build-requests", json=PAYLOAD, headers=headers)
        assert first.status_code == 200
        other = dict(PAYLOAD)
        other = {
            **PAYLOAD,
            "project": {**PAYLOAD["project"], "repository": "OtherRepo"},
        }
        second = client.post("/api/v1/build-requests", json=other, headers=headers)
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
