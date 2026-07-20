from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import BuildImage, BuildImageCapability, BuildRequest, FactoryControl, utcnow
from app.domain.capability_matcher import capability_keys
from app.domain.catalog import Catalog
from app.domain.dockerfile_gen import (
    generate_dockerfile,
    generate_install_manifest,
    generate_vsconfig,
)
from app.domain.profile_resolver import ResolvedProfile
from app.services.events import append_event
from app.services.jenkins import get_jenkins_client


FACTORY_JOB = "msbuild-image-factory"
PROJECT_JOB = "msbuild-project-build"
DEFAULT_LEASE_MINUTES = 135
# Keep aligned with Jenkins msbuild-image-factory (single factory node / disableConcurrentBuilds).
MAX_GLOBAL_CREATING = 1


class FactoryBusy(Exception):
    pass


def _lease_ttl_minutes(catalog: Catalog) -> int:
    cold = int((catalog.estimated_minutes or {}).get("coldAverage") or 90)
    return max(DEFAULT_LEASE_MINUTES, int(cold * 1.5))


def count_creating(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(BuildImage).where(
                BuildImage.status.in_(["CREATING", "VALIDATING"])
            )
        )
        or 0
    )


def get_active_image(session: Session, profile_hash: str, *, for_update: bool = False) -> BuildImage | None:
    stmt = select(BuildImage).where(
        BuildImage.profile_hash == profile_hash,
        BuildImage.status != "DELETED",
    )
    if for_update:
        stmt = stmt.with_for_update()
    # Prefer in-flight, then READY, then newest row to avoid arbitrary picks.
    rows = list(session.scalars(stmt.order_by(BuildImage.id.desc())).all())
    if not rows:
        return None
    for status in ("CREATING", "VALIDATING", "READY", "FAILED", "DEPRECATED", "QUARANTINED"):
        for row in rows:
            if row.status == status:
                return row
    return rows[0]


def _lock_factory_slots(session: Session) -> None:
    """Serialize global creating-slot checks across concurrent cold profiles."""
    ctrl = session.get(FactoryControl, 1, with_for_update=True)
    if ctrl is None:
        session.add(FactoryControl(id=1))
        session.flush()
        ctrl = session.get(FactoryControl, 1, with_for_update=True)
    assert ctrl is not None
    ctrl.updated_at = utcnow()


def _wait_for_inflight(
    session: Session,
    request: BuildRequest,
    existing: BuildImage,
    profile_hash: str,
) -> str:
    request.status = "IMAGE_WAITING"
    request.match_type = "PENDING"
    request.matched_profile_hash = existing.profile_hash
    append_event(
        session,
        request.id,
        "IMAGE_WAITING",
        "Waiting for in-flight image factory run",
        {"profileHash": profile_hash, "leaseId": existing.lease_id},
    )
    return "IMAGE_WAITING"


def start_or_join_factory(
    session: Session,
    catalog: Catalog,
    *,
    resolved: ResolvedProfile,
    request_id: str | None = None,
) -> tuple[BuildImage, str]:
    """Ensure a factory run exists for profile hash (no BuildRequest required).

    Returns (image_row, phase) where phase is:
      READY | CREATING | WAITING | QUARANTINED | BUSY
    """
    from sqlalchemy.exc import IntegrityError

    existing = get_active_image(session, resolved.profile_hash, for_update=True)
    if existing and existing.status == "READY":
        return existing, "READY"
    if existing and existing.status in {"CREATING", "VALIDATING"}:
        return existing, "WAITING"
    if existing and existing.status == "QUARANTINED":
        return existing, "QUARANTINED"

    _lock_factory_slots(session)
    existing = get_active_image(session, resolved.profile_hash, for_update=True)
    if existing and existing.status == "READY":
        return existing, "READY"
    if existing and existing.status in {"CREATING", "VALIDATING"}:
        return existing, "WAITING"
    if existing and existing.status == "QUARANTINED":
        return existing, "QUARANTINED"

    if count_creating(session) >= MAX_GLOBAL_CREATING:
        raise FactoryBusy("factory slots full")

    lease_id = f"factory-{uuid.uuid4().hex[:10]}"
    if existing and existing.status in {"FAILED", "DEPRECATED"}:
        existing.status = "CREATING"
        existing.lease_id = lease_id
        existing.lease_owner = lease_id
        existing.lease_expires_at = utcnow() + timedelta(minutes=_lease_ttl_minutes(catalog))
        existing.failure_code = None
        existing.updated_at = utcnow()
        image = existing
    elif existing:
        existing.status = "CREATING"
        existing.lease_id = lease_id
        existing.lease_owner = lease_id
        existing.lease_expires_at = utcnow() + timedelta(minutes=_lease_ttl_minutes(catalog))
        existing.updated_at = utcnow()
        image = existing
    else:
        tag = f"vs{resolved.vs_generation}-{resolved.profile_hash[:12]}"
        image = BuildImage(
            profile_hash=resolved.profile_hash,
            image_repository=_registry().final_image,
            image_tag=tag,
            image_digest=f"sha256:pending-{resolved.profile_hash[:16]}",
            status="CREATING",
            base_image_digest=resolved.build_input["windowsBase"]["digest"],
            windows_base=resolved.windows_base,
            vs_generation=resolved.vs_generation,
            capability_profile_json=json.dumps(
                {
                    "visualStudio": resolved.vs_generation,
                    "dotnetFrameworks": [],
                    "dotnetSdks": [],
                    "cppToolsets": [],
                    "windowsSdks": [],
                    "features": [],
                    "windowsBase": resolved.windows_base,
                    "customSdks": [],
                },
                ensure_ascii=False,
            ),
            catalog_version=catalog.version,
            hot=False,
            lease_id=lease_id,
            lease_owner=lease_id,
            lease_expires_at=utcnow() + timedelta(minutes=_lease_ttl_minutes(catalog)),
        )
        session.add(image)
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError:
            raced = get_active_image(session, resolved.profile_hash, for_update=True)
            if raced and raced.status in {"CREATING", "VALIDATING"}:
                return raced, "WAITING"
            if raced and raced.status == "READY":
                return raced, "READY"
            raise

    jenkins = get_jenkins_client()
    trigger = jenkins.trigger_job(
        FACTORY_JOB,
        {
            "BUILD_REQUEST_ID": request_id or "",
            "PROFILE_HASH": resolved.profile_hash,
            "FACTORY_LEASE_ID": image.lease_id,
        },
    )
    image.factory_job_id = trigger.queue_id
    image.updated_at = utcnow()
    session.flush()
    return image, "CREATING"


def acquire_or_wait_factory(
    session: Session,
    catalog: Catalog,
    *,
    resolved: ResolvedProfile,
    request: BuildRequest,
) -> str:
    """Ensure a factory run exists for requested hash (legacy combined path).

    Returns request status set: IMAGE_BUILD_QUEUED | IMAGE_WAITING | BUILD_QUEUED
    """
    try:
        image, phase = start_or_join_factory(
            session, catalog, resolved=resolved, request_id=request.id
        )
    except FactoryBusy:
        raise

    if phase == "READY":
        _attach_ready_image(session, request, image, match_type="EXACT")
        return "BUILD_QUEUED"
    if phase == "QUARANTINED":
        request.status = "PROFILE_REJECTED"
        request.error_code = "IMAGE_QUARANTINED"
        request.error_message = "Matched profile image is quarantined"
        request.finished_at = utcnow()
        append_event(session, request.id, "PROFILE_REJECTED", request.error_message)
        return "PROFILE_REJECTED"
    if phase == "WAITING":
        return _wait_for_inflight(session, request, image, resolved.profile_hash)

    # CREATING — this request owns / follows the newly queued factory job
    request.status = "IMAGE_BUILD_QUEUED"
    request.match_type = "CREATED"
    request.matched_profile_hash = resolved.profile_hash
    request.jenkins_job_name = FACTORY_JOB
    request.jenkins_queue_id = image.factory_job_id
    append_event(
        session,
        request.id,
        "IMAGE_BUILD_QUEUED",
        "Image factory job queued",
        {
            "profileHash": resolved.profile_hash,
            "leaseId": image.lease_id,
            "queueId": image.factory_job_id,
            "jobName": FACTORY_JOB,
        },
    )
    session.flush()
    return "IMAGE_BUILD_QUEUED"


def _attach_ready_image(
    session: Session,
    request: BuildRequest,
    image: BuildImage,
    *,
    match_type: str,
    provided: list[str] | None = None,
    extra: list[str] | None = None,
) -> None:
    request.matched_profile_hash = image.profile_hash
    request.match_type = match_type
    request.image_digest = image.image_digest
    request.provided_capabilities_json = json.dumps(provided or [], ensure_ascii=False)
    request.extra_capabilities_json = json.dumps(extra or [], ensure_ascii=False)
    request.status = "BUILD_QUEUED"
    request.error_code = None
    request.error_message = None
    request.finished_at = None
    request.jenkins_job_name = PROJECT_JOB

    jenkins = get_jenkins_client()
    trigger = jenkins.trigger_job(
        PROJECT_JOB,
        {
            "BUILD_REQUEST_ID": request.id,
            "IMAGE_DIGEST": image.image_digest,
            "PROFILE_HASH": image.profile_hash,
        },
    )
    request.jenkins_queue_id = trigger.queue_id
    image.last_used_at = utcnow()
    image.updated_at = utcnow()
    append_event(
        session,
        request.id,
        "BUILD_QUEUED",
        f"Project build queued ({match_type})",
        {
            "matchType": match_type,
            "matchedProfileHash": image.profile_hash,
            "imageDigest": image.image_digest,
            "queueId": trigger.queue_id,
        },
    )


def queue_project_build(
    session: Session,
    request: BuildRequest,
    image: BuildImage,
    *,
    match_type: str,
    provided: list[str] | None = None,
    extra: list[str] | None = None,
) -> None:
    _attach_ready_image(
        session,
        request,
        image,
        match_type=match_type,
        provided=provided,
        extra=extra,
    )
    session.flush()


def _registry():
    from app.config import get_settings
    from app.domain.registry import registry_from_settings

    return registry_from_settings(get_settings())


def build_factory_artifacts(resolved_build_input: dict[str, Any], profile_hash: str) -> dict[str, Any]:
    reg = _registry()
    return {
        "profileHash": profile_hash,
        "dockerfile": generate_dockerfile(resolved_build_input, profile_hash=profile_hash),
        "vsconfig": generate_vsconfig(resolved_build_input),
        "installManifest": generate_install_manifest(resolved_build_input),
        # Push destinations (factory host docker login/tag/push)
        "stagingRepository": reg.push_staging_image,
        "finalRepository": reg.push_final_image,
        "registryHost": reg.push_host,
        # Pull/reference destinations (Windows workers / Portal DB)
        "registryPullHost": reg.host,
        "finalPullRepository": reg.final_image,
        "imageTag": f"vs{resolved_build_input['visualStudio']['generation']}-{profile_hash[:12]}",
    }


def replace_capability_rows(session: Session, image: BuildImage, capability: dict[str, Any]) -> None:
    for cap in list(image.capabilities):
        session.delete(cap)
    session.flush()
    for key in sorted(capability_keys(capability)):
        version = ""
        if key.startswith("dotnet-sdk-") and key.count(".") >= 2:
            version = key.removeprefix("dotnet-sdk-")
        session.add(
            BuildImageCapability(
                build_image_id=image.id,
                capability_key=key,
                capability_version=version,
            )
        )


def wake_waiters_for_image(
    session: Session,
    image: BuildImage,
    *,
    success: bool,
    failure_message: str | None = None,
) -> list[str]:
    """Transition Exact waiters / creators to next state. Returns affected request ids."""
    waiters = session.scalars(
        select(BuildRequest).where(
            BuildRequest.requested_profile_hash == image.profile_hash,
            BuildRequest.status.in_(
                ["IMAGE_WAITING", "IMAGE_BUILD_QUEUED", "IMAGE_BUILDING", "IMAGE_VALIDATING"]
            ),
        )
    ).all()
    affected: list[str] = []
    for request in waiters:
        if success:
            queue_project_build(
                session,
                request,
                image,
                match_type="CREATED" if request.match_type == "CREATED" else "EXACT",
            )
            # keep CREATED if it owned the factory run
            if request.match_type not in {"CREATED", "EXACT"}:
                request.match_type = "EXACT"
        else:
            request.status = "IMAGE_BUILD_FAILED"
            request.error_code = "IMAGE_BUILD_FAILED"
            request.error_message = failure_message or "Image factory failed"
            request.finished_at = utcnow()
            append_event(session, request.id, "IMAGE_BUILD_FAILED", request.error_message)
        affected.append(request.id)
    session.flush()
    return affected


def heartbeat_lease(session: Session, profile_hash: str, lease_id: str, extend_minutes: int = 30) -> BuildImage:
    image = get_active_image(session, profile_hash, for_update=True)
    if image is None:
        raise LookupError("image not found")
    if not image.lease_id or image.lease_id != lease_id:
        raise PermissionError("stale leaseId")
    now = utcnow()
    expires = image.lease_expires_at
    if expires is not None:
        if expires.tzinfo is None:
            from datetime import timezone

            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= now:
            raise PermissionError("lease expired")
    image.lease_expires_at = now + timedelta(minutes=extend_minutes)
    image.updated_at = now
    session.flush()
    return image


def require_active_factory_lease(session: Session, profile_hash: str, lease_id: str) -> BuildImage:
    """Validate lease for factory-artifacts / privileged factory reads."""
    image = get_active_image(session, profile_hash, for_update=True)
    if image is None:
        raise LookupError("image not found")
    if image.status not in {"CREATING", "VALIDATING"}:
        raise PermissionError("factory lease not active for this profile")
    if not image.lease_id or image.lease_id != lease_id:
        raise PermissionError("stale or missing leaseId")
    now = utcnow()
    expires = image.lease_expires_at
    if expires is not None:
        if expires.tzinfo is None:
            from datetime import timezone

            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= now:
            raise PermissionError("lease expired")
    return image
