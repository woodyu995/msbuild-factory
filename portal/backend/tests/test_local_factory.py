from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.jenkins import reset_jenkins_client
from app.services.local_factory import LocalFactoryError

COLD_ENV = {
    "visualStudio": "2022",
    "dotnetFrameworks": ["4.8"],
    "dotnetSdks": [],
    "cppToolsets": ["v143"],
    "windowsSdks": ["10.0.22621.0"],
    "features": ["managed-desktop", "mfc"],
    "reuseMode": "exactReuse",
}


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_ALLOW_INSECURE_DEFAULTS", "true")
    monkeypatch.setenv("PORTAL_LOCAL_FACTORY", "true")
    monkeypatch.setenv("PORTAL_SIMULATE_WORKERS", "false")
    monkeypatch.setenv("PORTAL_REQUIRE_AUTH", "false")
    monkeypatch.setenv("PORTAL_LOCAL_IMAGES_DIR", str(tmp_path / "images"))
    monkeypatch.setenv("PORTAL_LOCAL_IMAGE_REPO", "msbuild-local")
    get_settings.cache_clear()
    reset_jenkins_client()
    db_path = tmp_path / "local-factory.db"
    app = create_app(database_url=f"sqlite:///{db_path}")
    return TestClient(app)


def test_options_exposes_local_factory(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        data = client.get("/api/v1/build-environment/options").json()
        assert data["localFactory"] is True
        assert data["requireAuth"] is False


def test_ensure_local_factory_builds_and_saves(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, *, cwd=None):
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "build"]:
            assert cwd is not None
            assert Path(cwd, "Dockerfile").exists()
            return ""
        if cmd[:3] == ["docker", "image", "inspect"]:
            return "sha256:localdeadbeef0123456789abcdef"
        if cmd[:2] == ["docker", "save"]:
            out = Path(cmd[cmd.index("-o") + 1])
            out.write_bytes(b"fake-tar")
            return ""
        raise AssertionError(f"unexpected cmd {cmd}")

    monkeypatch.setattr("app.services.local_factory._run", fake_run)

    with _client(tmp_path, monkeypatch) as client:
        ensured = client.post("/api/v1/images/ensure", json={"environment": COLD_ENV}).json()
        assert ensured["matchedProfileHash"]
        hash_ = ensured["matchedProfileHash"]

        status = client.get(f"/api/v1/images/{hash_}").json()
        assert status["ready"] is True
        assert status["image"]["digest"] == "sha256:localdeadbeef0123456789abcdef"
        assert status["image"]["repository"] == "msbuild-local"
        assert status["localTarFile"]
        tar = Path(tmp_path / "images" / status["localTarFile"])
        assert tar.exists()
        assert tar.read_bytes() == b"fake-tar"
        assert any(c[:2] == ["docker", "build"] for c in calls)
        assert any(c[:2] == ["docker", "save"] for c in calls)

        again = client.post("/api/v1/images/ensure", json={"environment": COLD_ENV}).json()
        assert again["ready"] is True
        assert again["image"]["digest"] == "sha256:localdeadbeef0123456789abcdef"


def test_local_factory_failure_marks_failed(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise LocalFactoryError("docker missing")

    monkeypatch.setattr("app.services.local_factory._run", boom)

    with _client(tmp_path, monkeypatch) as client:
        ensured = client.post("/api/v1/images/ensure", json={"environment": COLD_ENV}).json()
        hash_ = ensured["matchedProfileHash"]
        status = client.get(f"/api/v1/images/{hash_}").json()
        assert status["ready"] is False
        assert status["imageStatus"] == "FAILED"
