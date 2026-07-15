from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import BuildEvent, BuildRequest, utcnow
from app.domain.catalog import Catalog
from app.domain.profile_resolver import ProfileRejected
from app.domain.project_validation import (
    InvalidProjectInput,
    validate_configuration,
    validate_platform,
    validate_repository,
    validate_solution_path,
)
from app.services.events import append_event
from app.services.factory import get_active_image, queue_project_build
from app.services.image_resolve import (
    ensure_profile_row,
    factory_enabled,
    resolve_image,
)


class IdempotencyConflict(Exception):
    def __init__(self, message: str = "Idempotency-Key reused with different payload"):
        super().__init__(message)
        self.code = "IDEMPOTENCY_CONFLICT"
        self.message = message


class ImageNotReady(Exception):
    """Build start requires a READY image from POST /api/v1/images/ensure."""

    def __init__(
        self,
        message: str = "Image is not READY; call POST /api/v1/images/ensure first",
        *,
        code: str = "IMAGE_NOT_READY",
        requested_profile_hash: str | None = None,
        image_status: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.requested_profile_hash = requested_profile_hash
        self.image_status = image_status


def _new_request_id() -> str:
    stamp = utcnow().strftime("%Y%m%d")
    return f"br-{stamp}-{uuid.uuid4().hex[:6]}"


def payload_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _existing_for_idempotency(
    session: Session,
    *,
    idempotency_key: str,
    fingerprint: str,
) -> BuildRequest | None:
    existing = session.scalar(
        select(BuildRequest).where(BuildRequest.idempotency_key == idempotency_key)
    )
    if existing is None:
        return None
    if existing.idempotency_payload_hash and existing.idempotency_payload_hash != fingerprint:
        raise IdempotencyConflict()
    if not existing.idempotency_payload_hash:
        existing.idempotency_payload_hash = fingerprint
    return existing


def create_build_request(
    session: Session,
    catalog: Catalog,
    *,
    payload: dict[str, Any],
    actor: str,
    idempotency_key: str | None = None,
    factory_enabled_override: bool | None = None,
    git_resolver=None,
) -> BuildRequest:
    fingerprint = payload_fingerprint(payload)
    if idempotency_key:
        existing = _existing_for_idempotency(
            session, idempotency_key=idempotency_key, fingerprint=fingerprint
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

    repository = validate_repository(project["repository"])
    solution_path = validate_solution_path(project["solutionPath"])
    configuration = validate_configuration(project.get("configuration"))
    platform = validate_platform(project.get("platform"))
    git_ref = project["gitRef"]
    if git_resolver is None:
        from app.domain.git_resolve import PlaceholderGitResolver

        git_resolver = PlaceholderGitResolver()
    resolved_git = git_resolver.resolve(repository, git_ref)
    resolved_commit, commit_resolution = resolved_git.commit, resolved_git.mode

    # Resolve image before creating the request — start-build never runs factory.
    resolved, match, action = resolve_image(
        session,
        catalog,
        environment,
        factory_enabled_override=factory_enabled_override,
    )
    ensure_profile_row(session, catalog, resolved, actor)

    client_hash = payload.get("matchedProfileHash")
    client_digest = payload.get("imageDigest")

    if match is None:
        inflight = get_active_image(session, resolved.profile_hash)
        status = inflight.status if inflight else "NOT_CREATED"
        if action == "REJECTED":
            raise ProfileRejected(
                "No compatible READY image and factory is disabled",
            )
        raise ImageNotReady(
            f"Image status is {status}; call POST /api/v1/images/ensure and wait until READY",
            requested_profile_hash=resolved.profile_hash,
            image_status=status,
        )

    image = get_active_image(session, match.candidate.profile_hash)
    if image is None or image.status not in {"READY", "DEPRECATED"}:
        status = image.status if image else "NOT_CREATED"
        raise ImageNotReady(
            f"Matched image is not READY (status={status})",
            requested_profile_hash=resolved.profile_hash,
            image_status=status,
        )

    if client_hash and client_hash != image.profile_hash:
        raise ImageNotReady(
            f"matchedProfileHash mismatch: expected {image.profile_hash}",
            code="IMAGE_REF_MISMATCH",
            requested_profile_hash=resolved.profile_hash,
            image_status=image.status,
        )
    if client_digest and client_digest != image.image_digest:
        raise ImageNotReady(
            "imageDigest does not match READY image",
            code="IMAGE_REF_MISMATCH",
            requested_profile_hash=resolved.profile_hash,
            image_status=image.status,
        )

    request = BuildRequest(
        id=_new_request_id(),
        repository=repository,
        git_ref=git_ref,
        resolved_commit=resolved_commit,
        commit_resolution=commit_resolution,
        solution_path=solution_path,
        configuration=configuration,
        platform=platform,
        requested_profile_hash=resolved.profile_hash,
        match_type="PENDING",
        reuse_mode=resolved.requested.get("reuseMode") or "preferCompatible",
        nuget_mode=nuget_mode,
        status="REQUESTED",
        requested_by=actor,
        idempotency_key=idempotency_key,
        idempotency_payload_hash=fingerprint,
        environment_json=json.dumps(environment, ensure_ascii=False),
    )
    session.add(request)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        if not idempotency_key:
            raise
        raced = _existing_for_idempotency(
            session, idempotency_key=idempotency_key, fingerprint=fingerprint
        )
        if raced:
            return raced
        raise

    append_event(
        session,
        request.id,
        "REQUESTED",
        "Build request accepted",
        {"commitResolution": commit_resolution, "resolvedCommit": resolved_commit},
    )
    append_event(session, request.id, "VALIDATING_PROFILE", "Validating profile against catalog")
    append_event(
        session,
        request.id,
        "RESOLVING_IMAGE",
        "Attaching READY image (factory already complete)",
    )

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
    allowed_from = {
        "BUILDING": {"BUILD_QUEUED", "BUILDING"},
        "TESTING": {"BUILDING", "TESTING"},
        "PUBLISHING": {"TESTING", "PUBLISHING", "BUILDING"},
        "SUCCEEDED": {"PUBLISHING", "TESTING", "BUILDING", "BUILD_QUEUED"},
        "PROJECT_BUILD_FAILED": {
            "BUILD_QUEUED",
            "BUILDING",
            "TESTING",
            "PUBLISHING",
        },
        "TEST_FAILED": {"TESTING", "BUILDING"},
        "IMAGE_BUILDING": {"IMAGE_BUILD_QUEUED", "IMAGE_WAITING", "IMAGE_BUILDING"},
        "IMAGE_VALIDATING": {"IMAGE_BUILDING", "IMAGE_VALIDATING", "IMAGE_BUILD_QUEUED"},
        "IMAGE_BUILD_FAILED": {
            "IMAGE_BUILD_QUEUED",
            "IMAGE_WAITING",
            "IMAGE_BUILDING",
            "IMAGE_VALIDATING",
        },
        "IMAGE_VALIDATION_FAILED": {"IMAGE_VALIDATING", "IMAGE_BUILDING"},
        "CANCELLED": {
            "REQUESTED",
            "VALIDATING_PROFILE",
            "RESOLVING_IMAGE",
            "IMAGE_BUILD_QUEUED",
            "IMAGE_WAITING",
            "BUILD_QUEUED",
            "BUILDING",
            "TESTING",
            "PUBLISHING",
        },
    }
    if event_type in allowed_from and request.status not in allowed_from[event_type]:
        raise ValueError(
            f"invalid transition {request.status} -> {event_type}"
        )

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
