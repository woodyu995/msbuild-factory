from __future__ import annotations

import yaml

from app.domain.pod_template import render_windows_builder_pod, render_windows_builder_pod_yaml


def test_render_pod_template_ltsc2022():
    pod = render_windows_builder_pod(
        image_digest="sha256:abc123",
        windows_base="ltsc2022",
        request_id="br-20260715-000142",
    )
    assert pod["kind"] == "Pod"
    assert pod["spec"]["nodeSelector"]["build.company.io/windows-release"] == "ltsc2022"
    assert pod["spec"]["containers"][0]["image"].endswith("@sha256:abc123")
    assert pod["metadata"]["labels"]["build.company.io/request-id"] == "br-20260715-000142"

    text = render_windows_builder_pod_yaml(
        image_digest="sha256:abc123",
        windows_base="ltsc2022",
        request_id="br-20260715-000142",
    )
    loaded = yaml.safe_load(text)
    assert loaded["spec"]["os"]["name"] == "windows"


def test_pod_template_api(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_SIMULATE_WORKERS", "false")
    from app.config import get_settings
    from app.main import create_app
    from app.services.jenkins import reset_jenkins_client
    from fastapi.testclient import TestClient

    get_settings.cache_clear()
    reset_jenkins_client()
    app = create_app(database_url=f"sqlite:///{tmp_path / 'pod.db'}")
    with TestClient(app) as client:
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
                },
            },
        ).json()
        assert created["status"] == "BUILD_QUEUED"
        yaml_text = client.get(f"/api/v1/build-requests/{created['id']}/pod-template").text
        assert "ltsc2022" in yaml_text
        assert created["imageDigest"] in yaml_text
        js = client.get(
            f"/api/v1/build-requests/{created['id']}/pod-template",
            params={"format": "json"},
        ).json()
        assert js["spec"]["nodeSelector"]["build.company.io/windows-release"] == "ltsc2022"
