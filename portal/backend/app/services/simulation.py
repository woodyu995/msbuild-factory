from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import BuildProfile, BuildRequest
from app.domain.catalog import Catalog
from app.domain.profile_resolver import resolve_profile
from app.services.build_requests import apply_build_event_callback, get_build_request
from app.services.factory import get_active_image
from app.services.image_resolve import apply_image_status_callback, capability_from_environment
from sqlalchemy import select


def _load_profile(session: Session, profile_hash: str) -> BuildProfile:
    row = session.scalar(select(BuildProfile).where(BuildProfile.profile_hash == profile_hash))
    if row is None:
        raise LookupError(f"profile not found: {profile_hash}")
    return row


def simulate_factory_run(
    session: Session,
    catalog: Catalog,
    *,
    profile_hash: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Local stand-in for Windows Image Factory.

    Emits IMAGE_BUILDING / IMAGE_VALIDATING on the owning request, then marks the
    image READY with a simulated digest and measured capability profile.
    """
    image = get_active_image(session, profile_hash)
    if image is None:
        raise LookupError("image not found")
    if image.status == "READY":
        return {
            "ok": True,
            "alreadyReady": True,
            "profileHash": profile_hash,
            "imageDigest": image.image_digest,
            "affectedRequestIds": [],
        }
    if image.status not in {"CREATING", "VALIDATING", "FAILED"}:
        raise ValueError(f"cannot simulate factory from status={image.status}")
    if not image.lease_id and image.status != "FAILED":
        raise ValueError("missing leaseId for factory simulation")

    # Revive FAILED into CREATING for explicit simulate retries
    if image.status == "FAILED":
        from datetime import timedelta
        from app.db.models import utcnow
        import uuid

        image.status = "CREATING"
        image.lease_id = f"factory-sim-{uuid.uuid4().hex[:8]}"
        image.lease_owner = image.lease_id
        image.lease_expires_at = utcnow() + timedelta(minutes=30)
        image.failure_code = None
        session.flush()

    owner_id = request_id
    if owner_id is None:
        owner = session.scalar(
            select(BuildRequest).where(
                BuildRequest.requested_profile_hash == profile_hash,
                BuildRequest.status.in_(
                    ["IMAGE_BUILD_QUEUED", "IMAGE_WAITING", "IMAGE_BUILDING", "IMAGE_VALIDATING"]
                ),
            )
        )
        owner_id = owner.id if owner else None

    if owner_id:
        apply_build_event_callback(
            session,
            request_id=owner_id,
            event_type="IMAGE_BUILDING",
            message="[simulate] Image factory build started",
        )
        apply_build_event_callback(
            session,
            request_id=owner_id,
            event_type="IMAGE_VALIDATING",
            message="[simulate] Validating installed tooling",
        )
        apply_image_status_callback(
            session,
            profile_hash=profile_hash,
            lease_id=image.lease_id or "",
            status="VALIDATING",
            message="[simulate] validating",
        )
        # refresh lease after validating transition (lease still held)
        image = get_active_image(session, profile_hash)
        assert image is not None

    # Derive capability from the request environment when available
    capability = None
    if owner_id:
        req = get_build_request(session, owner_id)
        if req:
            env = json.loads(req.environment_json)
            resolved = resolve_profile(catalog, env)
            capability = capability_from_environment(resolved)
    if capability is None:
        profile = _load_profile(session, profile_hash)
        build_input = json.loads(profile.normalized_profile_json)
        capability = {
            "visualStudio": build_input["visualStudio"]["generation"],
            "dotnetFrameworks": [
                # map installer version back is lossy; keep exact resolved versions for packs
                item["version"]
                for item in build_input.get("dotnetFrameworkTargetingPacks") or []
            ],
            "dotnetSdks": [
                {"version": item["version"]} for item in build_input.get("dotnetSdks") or []
            ],
            "cppToolsets": list(build_input.get("cppToolsets") or []),
            "windowsSdks": [
                item["version"] if isinstance(item, dict) else str(item)
                for item in build_input.get("windowsSdks") or []
            ],
            "features": list(build_input.get("features") or []),
            "windowsBase": build_input["windowsBase"]["name"],
            "customSdks": [],
        }

    digest = f"sha256:simulated-{profile_hash[:24]}"
    image = get_active_image(session, profile_hash)
    assert image is not None and image.lease_id
    row, affected = apply_image_status_callback(
        session,
        profile_hash=profile_hash,
        lease_id=image.lease_id,
        status="READY",
        image_digest=digest,
        capability_profile=capability,
        message="[simulate] factory completed",
    )
    return {
        "ok": True,
        "alreadyReady": False,
        "profileHash": profile_hash,
        "imageDigest": row.image_digest,
        "affectedRequestIds": affected,
        "capabilityProfile": capability,
    }


def simulate_project_build(
    session: Session,
    *,
    request_id: str,
    fail: bool = False,
) -> dict[str, Any]:
    """Local stand-in for Jenkins msbuild-project-build pipeline."""
    request = get_build_request(session, request_id)
    if request is None:
        raise LookupError("request not found")
    if request.status in {"SUCCEEDED", "PROJECT_BUILD_FAILED", "TEST_FAILED", "CANCELLED"}:
        return {"ok": True, "alreadyFinished": True, "status": request.status}

    if request.status not in {
        "BUILD_QUEUED",
        "BUILDING",
        "TESTING",
        "PUBLISHING",
        "IMAGE_WAITING",
        "IMAGE_BUILD_QUEUED",
        "IMAGE_BUILDING",
        "IMAGE_VALIDATING",
    }:
        # allow simulation only once image is usable or already in project phases
        if request.image_digest is None and request.status != "BUILD_QUEUED":
            raise ValueError(f"cannot simulate project build from status={request.status}")

    if request.status != "BUILD_QUEUED" and request.image_digest and request.status.startswith("IMAGE_"):
        raise ValueError("image factory still running; simulate factory first")

    steps = ["BUILDING", "TESTING", "PUBLISHING"]
    build_number = (request.jenkins_build_number or 1000) + 1
    for event_type in steps:
        apply_build_event_callback(
            session,
            request_id=request_id,
            event_type=event_type,
            message=f"[simulate] {event_type.lower()}",
            jenkins_build_number=build_number,
        )
        if fail and event_type == "TESTING":
            apply_build_event_callback(
                session,
                request_id=request_id,
                event_type="TEST_FAILED",
                message="[simulate] intentional test failure",
                jenkins_build_number=build_number,
            )
            req = get_build_request(session, request_id)
            return {"ok": True, "status": req.status if req else "TEST_FAILED", "failed": True}

    apply_build_event_callback(
        session,
        request_id=request_id,
        event_type="SUCCEEDED",
        message="[simulate] project build succeeded",
        jenkins_build_number=build_number,
    )
    req = get_build_request(session, request_id)
    return {"ok": True, "status": req.status if req else "SUCCEEDED", "failed": False}


def auto_advance_request(
    session: Session,
    catalog: Catalog,
    *,
    request_id: str,
) -> dict[str, Any]:
    """If request is waiting on factory, simulate factory; then simulate project build."""
    request = get_build_request(session, request_id)
    if request is None:
        raise LookupError("request not found")

    result: dict[str, Any] = {"requestId": request_id, "steps": []}
    if request.status in {
        "IMAGE_BUILD_QUEUED",
        "IMAGE_WAITING",
        "IMAGE_BUILDING",
        "IMAGE_VALIDATING",
    }:
        factory_result = simulate_factory_run(
            session,
            catalog,
            profile_hash=request.requested_profile_hash,
            request_id=request_id,
        )
        result["steps"].append({"factory": factory_result})
        request = get_build_request(session, request_id)

    if request and request.status == "BUILD_QUEUED":
        build_result = simulate_project_build(session, request_id=request_id)
        result["steps"].append({"projectBuild": build_result})

    request = get_build_request(session, request_id)
    result["finalStatus"] = request.status if request else None
    return result
