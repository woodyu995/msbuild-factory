from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.capability_matcher import MatchCandidate, MatchResult, match_images
from app.domain.catalog import Catalog
from app.domain.profile_resolver import ResolvedProfile, resolve_profile
from app.db.models import BuildImage, BuildProfile, utcnow
from app.services.factory import replace_capability_rows, wake_waiters_for_image


def capability_from_environment(resolved: ResolvedProfile) -> dict[str, Any]:
    """Capability profile for a newly created / seeded image from build input."""
    return {
        "visualStudio": resolved.vs_generation,
        "dotnetFrameworks": list(resolved.requested["dotnetFrameworks"]),
        "dotnetSdks": [
            {"version": item["version"]} for item in resolved.build_input["dotnetSdks"]
        ],
        "cppToolsets": list(resolved.requested["cppToolsets"]),
        "windowsSdks": [item["version"] for item in resolved.build_input["windowsSdks"]],
        "features": list(resolved.requested["features"]),
        "windowsBase": resolved.windows_base,
        "customSdks": [],
    }


def ensure_profile_row(session: Session, catalog: Catalog, resolved: ResolvedProfile, actor: str) -> BuildProfile:
    existing = session.scalar(
        select(BuildProfile).where(BuildProfile.profile_hash == resolved.profile_hash)
    )
    if existing:
        return existing
    row = BuildProfile(
        profile_hash=resolved.profile_hash,
        normalized_profile_json=json.dumps(resolved.build_input, ensure_ascii=False, sort_keys=True),
        canonical_json=resolved.canonical_json,
        display_name=f"VS{resolved.vs_generation}-{resolved.profile_hash[:12]}",
        catalog_version=catalog.version,
        created_by=actor,
    )
    session.add(row)
    session.flush()
    return row


def list_match_candidates(session: Session) -> list[MatchCandidate]:
    rows = session.scalars(
        select(BuildImage).where(BuildImage.status.in_(["READY", "DEPRECATED"]))
    ).all()
    candidates: list[MatchCandidate] = []
    for row in rows:
        candidates.append(
            MatchCandidate(
                profile_hash=row.profile_hash,
                image_digest=row.image_digest,
                repository=row.image_repository,
                tag=row.image_tag,
                capability=json.loads(row.capability_profile_json),
                windows_base=row.windows_base,
                vs_generation=row.vs_generation,
                status=row.status,
                hot=row.hot,
                size_gib=row.size_gib,
            )
        )
    return candidates


def factory_enabled(catalog: Catalog, settings_override: bool | None = None) -> bool:
    if settings_override is not None:
        return settings_override
    return bool(catalog.mvp_factory_enabled)


def resolve_image(
    session: Session,
    catalog: Catalog,
    environment: dict[str, Any],
    *,
    factory_enabled_override: bool | None = None,
) -> tuple[ResolvedProfile, MatchResult | None, str]:
    """Returns resolved profile, optional match, and action string."""
    resolved = resolve_profile(catalog, environment)
    reuse_mode = resolved.requested.get("reuseMode") or "preferCompatible"
    match = match_images(
        requested_hash=resolved.profile_hash,
        capability_request=resolved.capability_request,
        candidates=list_match_candidates(session),
        matching_policy=catalog.capability_matching,
        reuse_mode=reuse_mode,
    )
    if match is None:
        if factory_enabled(catalog, factory_enabled_override):
            action = "IMAGE_CREATION_REQUIRED"
        else:
            action = "REJECTED"
        return resolved, None, action

    if match.match_type == "EXACT":
        return resolved, match, "REUSE_EXACT"
    return resolved, match, "REUSE_COMPATIBLE"


def touch_image_usage(session: Session, profile_hash: str) -> None:
    row = session.scalar(select(BuildImage).where(BuildImage.profile_hash == profile_hash))
    if row:
        row.last_used_at = utcnow()
        row.updated_at = utcnow()


def apply_image_status_callback(
    session: Session,
    *,
    profile_hash: str,
    lease_id: str,
    status: str,
    image_digest: str | None = None,
    capability_profile: dict[str, Any] | None = None,
    message: str | None = None,
) -> tuple[BuildImage, list[str]]:
    row = session.scalar(
        select(BuildImage).where(
            BuildImage.profile_hash == profile_hash,
            BuildImage.status != "DELETED",
        )
    )
    if row is None:
        raise LookupError("image not found")
    # Lease CAS: mutating callbacks require an active lease that matches exactly.
    if not row.lease_id or lease_id != row.lease_id:
        raise PermissionError("stale or missing leaseId")
    now = utcnow()
    row.updated_at = now
    affected: list[str] = []

    if status == "READY":
        if not image_digest or not capability_profile:
            raise ValueError("READY requires digest and capabilityProfile")
        row.status = "READY"
        row.image_digest = image_digest
        row.capability_profile_json = json.dumps(capability_profile, ensure_ascii=False)
        row.ready_at = now
        row.lease_id = None
        row.lease_owner = None
        row.lease_expires_at = None
        replace_capability_rows(session, row, capability_profile)
        affected = wake_waiters_for_image(session, row, success=True)
    elif status == "VALIDATING":
        row.status = "VALIDATING"
    elif status == "CREATING":
        row.status = "CREATING"
    elif status in {"FAILED", "QUARANTINED"}:
        row.status = status
        row.failure_code = "IMAGE_BUILD_FAILED" if status == "FAILED" else "QUARANTINED"
        row.retry_count = (row.retry_count or 0) + 1
        row.lease_id = None
        row.lease_owner = None
        row.lease_expires_at = None
        affected = wake_waiters_for_image(
            session,
            row,
            success=False,
            failure_message=message or f"Image factory {status}",
        )
    else:
        raise ValueError(f"unsupported status {status}")

    if message:
        row.validation_result_json = json.dumps({"message": message}, ensure_ascii=False)
    session.flush()
    return row, affected