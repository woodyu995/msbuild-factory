from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.models import BuildImage
from app.main import create_app
from app.security.hmac_auth import sign_body
from app.services.jenkins import reset_jenkins_client

AGENT = (
    Path(__file__).resolve().parents[3]
    / "jenkins"
    / "shared-library"
    / "scripts"
    / "portal_factory_agent.py"
)

COLD_ENV = {
    "visualStudio": "2022",
    "dotnetFrameworks": ["4.8"],
    "dotnetSdks": [],
    "cppToolsets": ["v143"],
    "windowsSdks": ["10.0.22621.0"],
    "features": ["managed-desktop", "mfc"],
    "reuseMode": "exactReuse",
}


def _load_agent():
    spec = importlib.util.spec_from_file_location("portal_factory_agent", AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_factory_agent_dry_run_against_portal(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_SIMULATE_WORKERS", "false")
    get_settings.cache_clear()
    reset_jenkins_client()
    app = create_app(database_url=f"sqlite:///{tmp_path / 'agent.db'}")
    mod = _load_agent()

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/build-requests",
            json={
                "project": {
                    "repository": "ColdApp",
                    "gitRef": "main",
                    "solutionPath": "ColdApp.sln",
                },
                "environment": COLD_ENV,
            },
        ).json()
        assert created["status"] == "IMAGE_BUILD_QUEUED"
        profile_hash = created["requestedProfileHash"]

        session = client.app.state.session_factory()
        image = session.query(BuildImage).filter_by(profile_hash=profile_hash).one()
        lease_id = image.lease_id
        session.close()

        def portal_request(method, url, body=None, hmac_secret=None):
            parsed = urlparse(url)
            path = parsed.path
            if parsed.query:
                path = f"{path}?{parsed.query}"
            headers = {}
            raw = None
            if body is not None:
                raw = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
                if hmac_secret:
                    ts = str(int(time.time()))
                    headers["X-Timestamp"] = ts
                    headers["X-Signature"] = sign_body(hmac_secret, ts, raw)
            resp = client.request(method, path, content=raw, headers=headers)
            assert resp.status_code < 400, resp.text
            return resp.json() if resp.content else {}

        mod._request = portal_request
        work = tmp_path / "factory-work"
        args = SimpleNamespace(
            portal_url=str(client.base_url),
            profile_hash=profile_hash,
            lease_id=lease_id,
            request_id=created["id"],
            work_dir=str(work),
            hmac_secret="dev-callback-secret-change-me",
            dry_run=True,
            layout_root="",
            installer_root="",
            message="",
        )
        mod.cmd_fetch(args)
        assert (work / "Dockerfile").exists()
        mod.cmd_heartbeat(args)
        mod.cmd_build(args)
        mod.cmd_finalize(args)

        got = client.get(f"/api/v1/build-requests/{created['id']}").json()
        assert got["status"] == "BUILD_QUEUED"
        assert got["imageDigest"].startswith("sha256:dry-run-")
        assert got["windowsBase"] == "ltsc2022"
        assert got["environment"]["visualStudio"] == "2022"
