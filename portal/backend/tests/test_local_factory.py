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


def test_local_factory_post_build_error_marks_failed(tmp_path, monkeypatch):
    def fake_run(cmd, *, cwd=None):
        if cmd[:2] == ["docker", "build"]:
            return ""
        if cmd[:3] == ["docker", "image", "inspect"]:
            return "sha256:abcdef0123456789"
        if cmd[:2] == ["docker", "save"]:
            out = Path(cmd[cmd.index("-o") + 1])
            out.write_bytes(b"tar")
            return ""
        raise AssertionError(cmd)

    monkeypatch.setattr("app.services.local_factory._run", fake_run)
    monkeypatch.setattr(
        "app.services.local_factory._capability_for_profile",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("capability boom")),
    )

    with _client(tmp_path, monkeypatch) as client:
        ensured = client.post("/api/v1/images/ensure", json={"environment": COLD_ENV}).json()
        hash_ = ensured["matchedProfileHash"]
        status = client.get(f"/api/v1/images/{hash_}").json()
        assert status["imageStatus"] == "FAILED"
        assert status["ready"] is False


def test_hot_preset_has_no_fake_local_tar(tmp_path, monkeypatch):
    hot = {
        "visualStudio": "2022",
        "dotnetFrameworks": ["4.8"],
        "dotnetSdks": ["8.0"],
        "cppToolsets": [],
        "windowsSdks": [],
        "features": ["managed-desktop"],
        "reuseMode": "preferCompatible",
    }
    with _client(tmp_path, monkeypatch) as client:
        ensured = client.post("/api/v1/images/ensure", json={"environment": hot}).json()
        assert ensured["ready"] is True
        assert ensured.get("localTarFile") in (None, "")
        status = client.get(f"/api/v1/images/{ensured['matchedProfileHash']}").json()
        assert status.get("localTarFile") in (None, "")


def test_wake_waiters_local_factory_skips_project_jenkins(tmp_path, monkeypatch):
    import json
    import uuid

    from app.db.models import BuildImage, BuildRequest
    from app.services.factory import wake_waiters_for_image
    from app.services.jenkins import get_jenkins_client

    with _client(tmp_path, monkeypatch) as client:
        session_factory = client.app.state.session_factory
        profile_hash = "a" * 64
        with session_factory() as session:
            image = BuildImage(
                profile_hash=profile_hash,
                image_repository="msbuild-local",
                image_tag="vs2022-deadbeef",
                image_digest="sha256:local-ready",
                status="READY",
                base_image_digest="sha256:base",
                windows_base="ltsc2022",
                vs_generation="2022",
                capability_profile_json=json.dumps(
                    {
                        "visualStudio": "2022",
                        "dotnetFrameworks": ["4.8"],
                        "dotnetSdks": [],
                        "cppToolsets": [],
                        "windowsSdks": [],
                        "features": [],
                        "windowsBase": "ltsc2022",
                        "customSdks": [],
                    }
                ),
                catalog_version="test",
                hot=False,
            )
            session.add(image)
            req = BuildRequest(
                id=f"br-{uuid.uuid4().hex[:8]}",
                status="IMAGE_WAITING",
                requested_by="test",
                repository="App",
                git_ref="main",
                resolved_commit="a" * 40,
                solution_path="App.sln",
                configuration="Release",
                platform="x64",
                requested_profile_hash=profile_hash,
                match_type="PENDING",
                reuse_mode="exactReuse",
                environment_json=json.dumps(COLD_ENV),
                nuget_mode="repo-packages-and-internal-feed",
            )
            session.add(req)
            session.commit()
            req_id = req.id

            jenkins = get_jenkins_client()
            before = list(jenkins.calls)
            wake_waiters_for_image(session, image, success=True)
            session.commit()
            after = list(jenkins.calls)
            assert after == before

            refreshed = session.get(BuildRequest, req_id)
            assert refreshed is not None
            assert refreshed.status == "BUILD_QUEUED"
            assert refreshed.image_digest == "sha256:local-ready"
            assert refreshed.jenkins_job_name is None
            assert refreshed.jenkins_queue_id is None
