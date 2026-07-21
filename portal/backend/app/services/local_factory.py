"""Local Docker image factory for step-by-step portal verification.

Builds a tiny offline-capable stub image (FROM scratch) that embeds the
resolved profile artifacts, tags it locally, and `docker save`s a tar under
PORTAL_LOCAL_IMAGES_DIR. No Jenkins, no Nexus push.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import BuildProfile
from app.domain.catalog import Catalog
from app.domain.dockerfile_gen import (
    _netfx_logical_version,
    generate_install_manifest,
    generate_vsconfig,
)
from app.services.factory import build_factory_artifacts, get_active_image
from app.services.image_resolve import apply_image_status_callback

logger = logging.getLogger(__name__)


class LocalFactoryError(RuntimeError):
    pass


def _run(cmd: list[str], *, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise LocalFactoryError(
            "docker CLI not found in portal container; rebuild image with docker CLI"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        raise LocalFactoryError(f"command failed ({' '.join(cmd)}): {detail}") from exc
    return (completed.stdout or "").strip()


def _stub_dockerfile(*, profile_hash: str, tag: str) -> str:
    hash12 = profile_hash[:12]
    return "\n".join(
        [
            "# Local verify stub — not a Windows MSBuild image",
            "# Real Server Core builds come later via Windows Factory + Nexus.",
            f"# profileHash={profile_hash}",
            f"# tag={tag}",
            "FROM scratch",
            "COPY install-manifest.json /profile/install-manifest.json",
            "COPY profile.vsconfig /profile/profile.vsconfig",
            "COPY build-input.json /profile/build-input.json",
            f'LABEL company.build.profile-hash="{hash12}"',
            'LABEL company.build.mode="local-verify"',
            f'LABEL company.build.profile-hash-full="{profile_hash}"',
            "",
        ]
    )


def _capability_for_profile(session: Session, profile_hash: str) -> dict[str, Any]:
    profile = session.scalar(select(BuildProfile).where(BuildProfile.profile_hash == profile_hash))
    if profile is None:
        raise LocalFactoryError(f"profile not found: {profile_hash}")
    build_input = json.loads(profile.normalized_profile_json)
    packs = build_input.get("dotnetFrameworkTargetingPacks") or []
    frameworks = sorted(
        {
            _netfx_logical_version(str(item.get("version") if isinstance(item, dict) else item))
            for item in packs
        }
    )
    return {
        "visualStudio": build_input["visualStudio"]["generation"],
        "dotnetFrameworks": frameworks,
        "dotnetSdks": [{"version": item["version"]} for item in build_input.get("dotnetSdks") or []],
        "cppToolsets": list(build_input.get("cppToolsets") or []),
        "windowsSdks": [
            item["version"] if isinstance(item, dict) else str(item)
            for item in build_input.get("windowsSdks") or []
        ],
        "features": list(build_input.get("features") or []),
        "windowsBase": build_input["windowsBase"]["name"],
        "customSdks": [],
    }


def local_artifact_paths(settings: Settings, image_tag: str) -> dict[str, str]:
    tar_name = f"{image_tag}.tar"
    return {
        "localImageRef": f"{settings.local_image_repo}:{image_tag}",
        "localTarPath": str(Path(settings.local_images_dir) / tar_name),
        "localTarFile": tar_name,
    }


def _mark_failed(session: Session, profile_hash: str, lease_id: str | None, message: str) -> None:
    if not lease_id:
        return
    try:
        apply_image_status_callback(
            session,
            profile_hash=profile_hash,
            lease_id=lease_id,
            status="FAILED",
            message=message,
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to mark local factory FAILED for %s", profile_hash)


def run_local_factory(
    session: Session,
    catalog: Catalog,
    *,
    profile_hash: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    image = get_active_image(session, profile_hash)
    if image is None:
        raise LookupError("image not found")
    if image.status == "READY":
        arts = local_artifact_paths(settings, image.image_tag)
        # Only claim tar artifacts when the file actually exists (or metadata says so).
        if Path(arts["localTarPath"]).is_file():
            return {
                "ok": True,
                "alreadyReady": True,
                "profileHash": profile_hash,
                "imageDigest": image.image_digest,
                **arts,
            }
        return {
            "ok": True,
            "alreadyReady": True,
            "profileHash": profile_hash,
            "imageDigest": image.image_digest,
        }
    if image.status not in {"CREATING", "VALIDATING", "FAILED"}:
        raise ValueError(f"cannot run local factory from status={image.status}")
    if not image.lease_id:
        raise ValueError("missing leaseId for local factory")

    lease_id = image.lease_id
    try:
        profile = session.scalar(select(BuildProfile).where(BuildProfile.profile_hash == profile_hash))
        if profile is None:
            raise LocalFactoryError(f"profile not found: {profile_hash}")
        build_input = json.loads(profile.normalized_profile_json)
        artifacts = build_factory_artifacts(build_input, profile_hash)
        tag = artifacts["imageTag"]
        repo_tag = f"{settings.local_image_repo}:{tag}"
        out_dir = Path(settings.local_images_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tar_path = out_dir / f"{tag}.tar"

        apply_image_status_callback(
            session,
            profile_hash=profile_hash,
            lease_id=lease_id,
            status="VALIDATING",
            message="[local-factory] building stub image",
        )
        image = get_active_image(session, profile_hash)
        assert image is not None and image.lease_id
        lease_id = image.lease_id

        work = Path(tempfile.mkdtemp(prefix="portal-local-factory-"))
        try:
            (work / "install-manifest.json").write_text(
                json.dumps(generate_install_manifest(build_input), indent=2),
                encoding="utf-8",
            )
            (work / "profile.vsconfig").write_text(
                json.dumps(generate_vsconfig(build_input), indent=2),
                encoding="utf-8",
            )
            (work / "build-input.json").write_text(
                json.dumps(build_input, indent=2, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            (work / "Dockerfile").write_text(
                _stub_dockerfile(profile_hash=profile_hash, tag=tag),
                encoding="utf-8",
            )

            _run(["docker", "build", "-t", repo_tag, "."], cwd=work)
            digest = _run(["docker", "image", "inspect", "--format", "{{.Id}}", repo_tag])
            if not digest.startswith("sha256:"):
                digest = f"sha256:{digest}" if digest else f"sha256:local-{profile_hash[:24]}"

            if tar_path.exists():
                tar_path.unlink()
            _run(["docker", "save", "-o", str(tar_path), repo_tag])

            readme = out_dir / "README.txt"
            if not readme.exists():
                readme.write_text(
                    "Local verify images (no Nexus).\n"
                    "Load: docker load -i <tag>.tar\n"
                    "List: docker images msbuild-local\n"
                    "Inspect labels: docker image inspect <ref>\n",
                    encoding="utf-8",
                )
        finally:
            shutil.rmtree(work, ignore_errors=True)

        capability = _capability_for_profile(session, profile_hash)
        arts = local_artifact_paths(settings, tag)
        row, affected = apply_image_status_callback(
            session,
            profile_hash=profile_hash,
            lease_id=lease_id,
            status="READY",
            image_digest=digest,
            capability_profile=capability,
            message="[local-factory] stub image built and saved (no Nexus push)",
        )
        row.image_repository = settings.local_image_repo
        row.image_tag = tag
        row.hot = False
        row.validation_result_json = json.dumps(
            {
                "mode": "local-factory",
                "message": "[local-factory] stub image built and saved (no Nexus push)",
                **arts,
            },
            ensure_ascii=False,
        )
        session.flush()

        logger.info("local factory READY %s digest=%s tar=%s", repo_tag, digest, tar_path)
        return {
            "ok": True,
            "alreadyReady": False,
            "profileHash": profile_hash,
            "imageDigest": row.image_digest,
            "affectedRequestIds": affected,
            "capabilityProfile": capability,
            **arts,
        }
    except LocalFactoryError as exc:
        _mark_failed(session, profile_hash, lease_id, f"[local-factory] {exc}")
        raise
    except Exception as exc:  # noqa: BLE001
        _mark_failed(session, profile_hash, lease_id, f"[local-factory] {exc}")
        raise LocalFactoryError(str(exc)) from exc
