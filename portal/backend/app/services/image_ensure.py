from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import BuildImage
from app.domain.catalog import Catalog
from app.domain.profile_resolver import ProfileRejected
from app.services.factory import FactoryBusy, get_active_image, start_or_join_factory
from app.services.image_resolve import ensure_profile_row, resolve_image


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
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
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
            "errorCode": self.error_code,
            "errorMessage": self.error_message,
            "ready": self.image_status == "READY" and self.image is not None,
        }


def _image_ref(row: BuildImage) -> dict[str, str]:
    return {
        "repository": row.image_repository,
        "tag": row.image_tag,
        "digest": row.image_digest,
    }


def ensure_image(
    session: Session,
    catalog: Catalog,
    *,
    environment: dict[str, Any],
    actor: str,
    factory_enabled_override: bool | None = None,
) -> EnsureImageResult:
    """Step 1: reuse READY image or start factory. Does not start project build."""
    resolved, match, action = resolve_image(
        session,
        catalog,
        environment,
        factory_enabled_override=factory_enabled_override,
    )
    ensure_profile_row(session, catalog, resolved, actor)

    if match is not None:
        row = get_active_image(session, match.candidate.profile_hash)
        return EnsureImageResult(
            requested_profile_hash=resolved.profile_hash,
            matched_profile_hash=match.candidate.profile_hash,
            match_type=match.match_type,
            action=action,
            image_status=match.candidate.status,
            estimated_wait_minutes=0,
            provided_capabilities=list(match.provided_capabilities),
            extra_capabilities=list(match.extra_capabilities),
            image=_image_ref(row) if row else {
                "repository": match.candidate.repository,
                "tag": match.candidate.tag,
                "digest": match.candidate.image_digest,
            },
            windows_base=resolved.windows_base,
            factory_lease_id=None,
        )

    if action == "REJECTED":
        return EnsureImageResult(
            requested_profile_hash=resolved.profile_hash,
            matched_profile_hash=None,
            match_type=None,
            action=action,
            image_status="NOT_CREATED",
            estimated_wait_minutes=0,
            provided_capabilities=[],
            extra_capabilities=[],
            image=None,
            windows_base=resolved.windows_base,
            factory_lease_id=None,
            error_code="IMAGE_CREATION_REQUIRED",
            error_message="No compatible READY image and factory is disabled",
        )

    try:
        image, phase = start_or_join_factory(session, catalog, resolved=resolved)
    except FactoryBusy as exc:
        return EnsureImageResult(
            requested_profile_hash=resolved.profile_hash,
            matched_profile_hash=None,
            match_type=None,
            action="FACTORY_BUSY",
            image_status="BUSY",
            estimated_wait_minutes=int(catalog.estimated_minutes.get("coldAverage", 75)),
            provided_capabilities=[],
            extra_capabilities=[],
            image=None,
            windows_base=resolved.windows_base,
            factory_lease_id=None,
            error_code="FACTORY_BUSY",
            error_message=str(exc),
        )

    if phase == "QUARANTINED":
        return EnsureImageResult(
            requested_profile_hash=resolved.profile_hash,
            matched_profile_hash=image.profile_hash,
            match_type=None,
            action="REJECTED",
            image_status="QUARANTINED",
            estimated_wait_minutes=0,
            provided_capabilities=[],
            extra_capabilities=[],
            image=None,
            windows_base=image.windows_base,
            factory_lease_id=None,
            error_code="IMAGE_QUARANTINED",
            error_message="Profile image is quarantined",
        )

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
            image=_image_ref(image),
            windows_base=image.windows_base,
            factory_lease_id=None,
        )

    wait = int(catalog.estimated_minutes.get("coldAverage", 75))
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
    )


def get_image_status(session: Session, profile_hash: str) -> dict[str, Any]:
    row = get_active_image(session, profile_hash)
    if row is None:
        raise LookupError("image not found")
    ready = row.status == "READY" and bool(row.image_digest) and not row.image_digest.startswith(
        "sha256:pending-"
    )
    return {
        "profileHash": row.profile_hash,
        "imageStatus": row.status,
        "ready": ready,
        "image": _image_ref(row) if ready else None,
        "windowsBase": row.windows_base,
        "factoryLeaseId": row.lease_id,
        "leaseExpiresAt": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
    }
