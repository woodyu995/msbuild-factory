from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BuildEvent, BuildRequest, utcnow
from app.domain.catalog import Catalog
from app.domain.profile_resolver import ProfileRejected
from app.services.events import append_event
from app.services.factory import (
    FactoryBusy,
    acquire_or_wait_factory,
    queue_project_build,
)
from app.services.image_resolve import (
    ensure_profile_row,
    factory_enabled,
    resolve_image,
)
from app.db.models import BuildImage


def _new_request_id() -> str:
    stamp = utcnow().strftime("%Y%m%d")
    return f"br-{stamp}-{uuid.uuid4().hex[:6]}"


def create_build_request(
    session: Session,
    catalog: Catalog,
    *,
    payload: dict[str, Any],
    actor: str,
    idempotency_key: str | None = None,
    factory_enabled_override: bool | None = None,
) -> BuildRequest:
    if idempotency_key:
        existing = session.scalar(
            select(BuildRequest).where(BuildRequest.idempotency_key == idempotency_key)
        )
        if existing:
            return existing

    project = payload["project"]
    environment = dict(payload["environment"])
    nuget = payload.get("nuget") or {}
    nuget_mode = nuget.get("mode") or "repo-packages-and-internal-feed"
    if nuget_mode not in {
        "repo-packages",
        "internal-feed",
        "repo-packages-and-internal-feed",
    }:
        raise ProfileRejected(f"unsupported nuget mode: {nuget_mode}")

    git_ref = project["gitRef"]
    resolved_commit = git_ref if len(git_ref) >= 40 else f"resolved:{git_ref}"

    request = BuildRequest(
        id=_new_request_id(),
        repository=project["repository"],
        git_ref=git_ref,
        resolved_commit=resolved_commit,
        solution_path=project["solutionPath"],
        configuration=project.get("configuration") or "Release",
        platform=project.get("platform") or "x64",
        requested_profile_hash="",
        match_type="PENDING",
        reuse_mode=environment.get("reuseMode") or "preferCompatible",
        nuget_mode=nuget_mode,
        status="REQUESTED",
        requested_by=actor,
        idempotency_key=idempotency_key,
        environment_json=json.dumps(environment, ensure_ascii=False),
    )
    session.add(request)
    session.flush()
    append_event(session, request.id, "REQUESTED", "Build request accepted")

    request.status = "VALIDATING_PROFILE"
    append_event(session, request.id, "VALIDATING_PROFILE", "Validating profile against catalog")

    request.status = "RESOLVING_IMAGE"
    append_event(session, request.id, "RESOLVING_IMAGE", "Resolving image via Exact/Capability matcher")

    resolved, match, action = resolve_image(
        session,
        catalog,
        environment,
        factory_enabled_override=factory_enabled_override,
    )
    ensure_profile_row(session, catalog, resolved, actor)
    request.requested_profile_hash = resolved.profile_hash
    request.reuse_mode = resolved.requested.get("reuseMode") or "preferCompatible"

    if match is None:
        if action == "REJECTED":
            request.status = "PROFILE_REJECTED"
            request.error_code = "PROFILE_REJECTED"
            request.error_message = "No compatible READY image and factory is disabled"
            request.finished_at = utcnow()
            append_event(
                session,
                request.id,
                request.status,
                request.error_message,
                {"action": action, "requestedProfileHash": resolved.profile_hash},
            )
            session.flush()
            return request

        # Factory path
        try:
            acquire_or_wait_factory(session, catalog, resolved=resolved, request=request)
        except FactoryBusy as exc:
            request.status = "IMAGE_BUILD_QUEUED"
            request.error_code = "FACTORY_BUSY"
            request.error_message = str(exc)
            append_event(session, request.id, "FACTORY_BUSY", str(exc))
        session.flush()
        return request

    image = session.scalar(
        select(BuildImage).where(
            BuildImage.profile_hash == match.candidate.profile_hash,
            BuildImage.status.in_(["READY", "DEPRECATED"]),
        )
    )
    if image is None:
        raise RuntimeError("matched image missing from database")

    queue_project_build(
        session,
        request,
        image,
        match_type=match.match_type,
        provided=match.provided_capabilities,
        extra=match.extra_capabilities,
    )
    session.flush()
    return request


def get_build_request(session: Session, request_id: str) -> BuildRequest | None:
    return session.get(BuildRequest, request_id)


def list_events(session: Session, request_id: str, after_id: int = 0) -> list[BuildEvent]:
    stmt = (
        select(BuildEvent)
        .where(BuildEvent.build_request_id == request_id, BuildEvent.id > after_id)
        .order_by(BuildEvent.id.asc())
    )
    return list(session.scalars(stmt).all())


def apply_build_event_callback(
    session: Session,
    *,
    request_id: str,
    event_type: str,
    message: str,
    jenkins_build_number: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> BuildRequest:
    request = session.get(BuildRequest, request_id)
    if request is None:
        raise LookupError("request not found")

    terminal_success = {"SUCCEEDED"}
    terminal_fail = {
        "PROJECT_BUILD_FAILED",
        "TEST_FAILED",
        "CANCELLED",
        "IMAGE_BUILD_FAILED",
        "IMAGE_VALIDATION_FAILED",
    }
    progressing = {
        "BUILDING",
        "TESTING",
        "PUBLISHING",
        "IMAGE_BUILDING",
        "IMAGE_VALIDATING",
    }

    if event_type in progressing | terminal_success | terminal_fail:
        request.status = event_type
    if jenkins_build_number is not None:
        request.jenkins_build_number = jenkins_build_number
    if event_type in terminal_success | terminal_fail:
        request.finished_at = utcnow()
        if event_type in terminal_fail:
            request.error_code = event_type
            request.error_message = message

    append_event(session, request.id, event_type, message, metadata)
    session.flush()
    return request


def is_factory_enabled(catalog: Catalog, settings) -> bool:
    override = getattr(settings, "factory_enabled", None)
    return factory_enabled(catalog, override)