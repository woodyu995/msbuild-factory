from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import BuildImage
from app.domain.catalog import Catalog
from app.domain.profile_resolver import ProfileRejected
from app.services.factory import FactoryBusy, get_active_image, start_or_join_factory
from app.services.image_resolve import ensure_profile_row, resolve_image


class ImageQuarantined(Exception):
    def __init__(self, message: str = "Profile image is quarantined", *, profile_hash: str | None = None):
        super().__init__(message)
        self.code = "IMAGE_QUARANTINED"
        self.message = message
        self.profile_hash = profile_hash


@dataclass
class EnsureImageResult:
    requested_profile_hash: str
    matched_profile_hash: str | None
    match_type: str | None
    action: str
    image_status: str
    estimated_wait_minutes: int
    provided_capabilities: list[str]
    extra_capabilities: list[str]
    image: dict[str, str] | None
    windows_base: str | None
    factory_lease_id: str | None
    lease_expires_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    factory_phase: str | None = None  # CREATING | WAITING | READY | None

    def to_dict(self) -> dict[str, Any]:
        ready_statuses = {"READY", "DEPRECATED"}
        return {
            "requestedProfileHash": self.requested_profile_hash,
            "matchedProfileHash": self.matched_profile_hash,
            "matchType": self.match_type,
            "action": self.action,
            "imageStatus": self.image_status,
            "estimatedWaitMinutes": self.estimated_wait_minutes,
            "providedCapabilities": self.provided_capabilities,
            "extraCapabilities": self.extra_capabilities,
            "image": self.image,
            "windowsBase": self.windows_base,
            "factoryLeaseId": self.factory_lease_id,
            "leaseExpiresAt": self.lease_expires_at,
            "errorCode": self.error_code,
            "errorMessage": self.error_message,
            "ready": self.image_status in ready_statuses and self.image is not None,
            "factoryPhase": self.factory_phase,
        }


def _image_ref(row: BuildImage) -> dict[str, str]:
    return {
        "repository": row.image_repository,
        "tag": row.image_tag,
        "digest": row.image_digest,
    }


def _digest_usable(digest: str | None) -> bool:
    return bool(digest) and not str(digest).startswith("sha256:pending-")


def ensure_image(
    session: Session,
    catalog: Catalog,
    *,
    environment: dict[str, Any],
    actor: str,
    factory_enabled_override: bool | None = None,
) -> EnsureImageResult:
    """Step 1: reuse READY image or start factory. Does not start project build.

    Hard failures raise:
      ProfileRejected — factory disabled / profile rejected
      FactoryBusy — global factory slots full (caller should retry)
      ImageQuarantined — profile image quarantined
    In-progress creation returns with ready=false (HTTP 200).
    """
    resolved, match, action = resolve_image(
        session,
        catalog,
        environment,
        factory_enabled_override=factory_enabled_override,
    )
    ensure_profile_row(session, catalog, resolved, actor)

    if match is not None:
        row = get_active_image(session, match.candidate.profile_hash)
        status = match.candidate.status
        ref = (
            _image_ref(row)
            if row
            else {
                "repository": match.candidate.repository,
                "tag": match.candidate.tag,
                "digest": match.candidate.image_digest,
            }
        )
        return EnsureImageResult(
            requested_profile_hash=resolved.profile_hash,
            matched_profile_hash=match.candidate.profile_hash,
            match_type=match.match_type,
            action=action,
            image_status=status,
            estimated_wait_minutes=0,
            provided_capabilities=list(match.provided_capabilities),
            extra_capabilities=list(match.extra_capabilities),
            image=ref if _digest_usable(ref.get("digest")) else None,
            windows_base=resolved.windows_base,
            factory_lease_id=None,
            lease_expires_at=None,
            factory_phase="READY",
        )

    if action == "REJECTED":
        raise ProfileRejected(
            "No compatible READY image and factory is disabled",
            code="PROFILE_REJECTED",
        )

    try:
        image, phase = start_or_join_factory(session, catalog, resolved=resolved)
    except FactoryBusy:
        raise

    if phase == "QUARANTINED":
        raise ImageQuarantined(profile_hash=image.profile_hash)

    if phase == "READY":
        return EnsureImageResult(
            requested_profile_hash=resolved.profile_hash,
            matched_profile_hash=image.profile_hash,
            match_type="EXACT",
            action="REUSE_EXACT",
            image_status="READY",
            estimated_wait_minutes=0,
            provided_capabilities=[],
            extra_capabilities=[],
            image=_image_ref(image) if _digest_usable(image.image_digest) else None,
            windows_base=image.windows_base,
            factory_lease_id=None,
            lease_expires_at=None,
            factory_phase="READY",
        )

    from app.config import get_settings

    wait = 1 if get_settings().local_factory else int(catalog.estimated_minutes.get("coldAverage", 75))
    return EnsureImageResult(
        requested_profile_hash=resolved.profile_hash,
        matched_profile_hash=image.profile_hash,
        match_type="CREATED" if phase == "CREATING" else None,
        action="IMAGE_CREATION_REQUIRED",
        image_status=image.status,
        estimated_wait_minutes=wait,
        provided_capabilities=[],
        extra_capabilities=[],
        image=None,
        windows_base=image.windows_base,
        factory_lease_id=image.lease_id,
        lease_expires_at=(
            image.lease_expires_at.isoformat() if image.lease_expires_at else None
        ),
        factory_phase=phase,
    )


def get_image_status(session: Session, profile_hash: str) -> dict[str, Any]:
    import json
    from pathlib import Path

    from app.config import get_settings
    from app.services.local_factory import local_artifact_paths

    row = get_active_image(session, profile_hash)
    if row is None:
        raise LookupError("image not found")
    ready = row.status in {"READY", "DEPRECATED"} and _digest_usable(row.image_digest)
    local_image_ref = None
    local_tar_path = None
    local_tar_file = None
    settings = get_settings()

    meta: dict[str, Any] | None = None
    if row.validation_result_json:
        try:
            parsed = json.loads(row.validation_result_json)
            if isinstance(parsed, dict):
                meta = parsed
        except json.JSONDecodeError:
            meta = None

    locally_built = bool(meta and meta.get("mode") == "local-factory")
    if locally_built:
        local_image_ref = meta.get("localImageRef")
        local_tar_path = meta.get("localTarPath")
        local_tar_file = meta.get("localTarFile")
    elif settings.local_factory and row.image_tag and row.image_repository == settings.local_image_repo:
        # Only advertise paths when the tar was actually written (not seeded hot presets).
        arts = local_artifact_paths(settings, row.image_tag)
        if Path(arts["localTarPath"]).is_file():
            local_image_ref = arts["localImageRef"]
            local_tar_path = arts["localTarPath"]
            local_tar_file = arts["localTarFile"]

    return {
        "profileHash": row.profile_hash,
        "imageStatus": row.status,
        "ready": ready,
        "image": _image_ref(row) if ready else None,
        "windowsBase": row.windows_base,
        "factoryLeaseId": row.lease_id,
        "leaseExpiresAt": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
        "localImageRef": local_image_ref,
        "localTarPath": local_tar_path,
        "localTarFile": local_tar_file,
    }
