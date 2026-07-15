from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.jenkins import reset_jenkins_client
from tests.helpers import HOT_ENV, build_payload, ensure_ready


def _client(tmp_path: Path):
    get_settings.cache_clear()
    reset_jenkins_client()
    app = create_app(database_url=f"sqlite:///{tmp_path / 'idem.db'}")
    return TestClient(app)


def test_idempotency_returns_same_request(tmp_path):
    with _client(tmp_path) as client:
        ensured = ensure_ready(client)
        payload = build_payload(ensured=ensured)
        headers = {"Idempotency-Key": "idem-same"}
        a = client.post("/api/v1/build-requests", json=payload, headers=headers).json()
        b = client.post("/api/v1/build-requests", json=payload, headers=headers).json()
        assert a["id"] == b["id"]


def test_idempotency_conflict_on_different_payload(tmp_path):
    with _client(tmp_path) as client:
        ensured = ensure_ready(client)
        payload = build_payload(ensured=ensured)
        headers = {"Idempotency-Key": "idem-conflict"}
        first = client.post("/api/v1/build-requests", json=payload, headers=headers)
        assert first.status_code == 200
        other = build_payload(
            ensured=ensured,
            project={**payload["project"], "repository": "OtherRepo"},
        )
        second = client.post("/api/v1/build-requests", json=other, headers=headers)
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_build_requires_image_pins(tmp_path):
    with _client(tmp_path) as client:
        resp = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ProductClient",
                    "gitRef": "main",
                    "solutionPath": "A.sln",
                },
                "environment": HOT_ENV,
            },
        )
        assert resp.status_code == 422


def test_image_ref_mismatch(tmp_path):
    with _client(tmp_path) as client:
        ensured = ensure_ready(client)
        payload = build_payload(ensured=ensured)
        payload["imageDigest"] = "sha256:wrong-digest-value"
        resp = client.post("/api/v1/build-requests", json=payload)
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "IMAGE_REF_MISMATCH"
