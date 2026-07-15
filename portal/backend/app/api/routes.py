from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.schemas import (
    BuildRequestCreate,
    BuildRequestResponse,
    EnvironmentSelection,
    ImageRef,
    InternalBuildEvent,
    InternalImageStatus,
    ValidateRequest,
    ValidateResponse,
)
from app.domain.profile_resolver import ProfileRejected
from app.domain.project_validation import InvalidProjectInput
from app.security.hmac_auth import CallbackAuthError, verify_hmac
from app.services.build_requests import (
    apply_build_event_callback,
    create_build_request,
    get_build_request,
    is_factory_enabled,
    list_events,
)
from app.services.factory import build_factory_artifacts, heartbeat_lease
from app.services.image_resolve import apply_image_status_callback, resolve_image
from app.services.reconcile import reconcile_expired_leases
from app.services.simulation import auto_advance_request, simulate_factory_run, simulate_project_build
from app.services.worker_schedule import maybe_schedule_auto_advance
from app.db.models import BuildProfile
from sqlalchemy import select

router = APIRouter()


def get_session(request: Request) -> Session:
    session_factory = request.app.state.session_factory
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


def get_catalog(request: Request):
    return request.app.state.catalog


def get_settings(request: Request):
    return request.app.state.settings


def actor_from_headers(
    request: Request,
    x_actor: Annotated[str | None, Header()] = None,
) -> str:
    settings = request.app.state.settings
    if settings.require_auth and not x_actor:
        raise HTTPException(status_code=401, detail="authentication required")
    return x_actor or settings.default_actor


def _request_to_response(row, session: Session) -> BuildRequestResponse:
    image = None
    windows_base = None
    if row.image_digest and row.matched_profile_hash:
        from sqlalchemy import select
        from app.db.models import BuildImage

        img = session.scalar(
            select(BuildImage).where(BuildImage.profile_hash == row.matched_profile_hash)
        )
        if img:
            image = ImageRef(
                repository=img.image_repository,
                tag=img.image_tag,
                digest=img.image_digest,
            )
            windows_base = img.windows_base
    elif row.matched_profile_hash:
        from sqlalchemy import select
        from app.db.models import BuildImage

        img = session.scalar(
            select(BuildImage).where(BuildImage.profile_hash == row.matched_profile_hash)
        )
        if img:
            windows_base = img.windows_base

    provided = json.loads(row.provided_capabilities_json or "[]")
    extra = json.loads(row.extra_capabilities_json or "[]")
    environment = json.loads(row.environment_json) if row.environment_json else None
    return BuildRequestResponse(
        id=row.id,
        status=row.status,
        repository=row.repository,
        gitRef=row.git_ref,
        resolvedCommit=row.resolved_commit,
        solutionPath=row.solution_path,
        configuration=row.configuration,
        platform=row.platform,
        requestedProfileHash=row.requested_profile_hash,
        matchedProfileHash=row.matched_profile_hash,
        matchType=row.match_type,
        reuseMode=row.reuse_mode,
        imageDigest=row.image_digest,
        nugetMode=row.nuget_mode,
        jenkinsJobName=row.jenkins_job_name,
        jenkinsBuildNumber=row.jenkins_build_number,
        providedCapabilities=provided,
        extraCapabilities=extra,
        errorCode=row.error_code,
        errorMessage=row.error_message,
        image=image,
        windowsBase=windows_base,
        environment=environment,
    )


@router.get("/api/v1/build-environment/options")
def options(request: Request):
    catalog = get_catalog(request)
    settings = get_settings(request)
    visual_studios = []
    for vs_id, entry in catalog.visual_studio.items():
        visual_studios.append(
            {
                "id": vs_id,
                "windowsBase": entry["windowsBase"],
                "allowed": entry["allowed"],
            }
        )
    return {
        "catalogVersion": catalog.version,
        "presets": [
            {
                "id": p["id"],
                "displayName": p["displayName"],
                "hot": p.get("hot", False),
                "environment": p["environment"],
            }
            for p in catalog.presets
        ],
        "visualStudios": visual_studios,
        "compatibilityRules": catalog.compatibility_rules,
        "capabilityMatching": catalog.capability_matching,
        "estimatedImageBuildMinutes": catalog.estimated_minutes,
        "mvpFactoryEnabled": is_factory_enabled(catalog, settings),
        "simulateWorkers": settings.simulate_workers,
    }


@router.post("/api/v1/build-environment/validate", response_model=ValidateResponse)
def validate(body: ValidateRequest, request: Request, session: SessionDep):
    catalog = get_catalog(request)
    settings = get_settings(request)
    env = body.environment.model_dump()
    try:
        resolved, match, action = resolve_image(
            session,
            catalog,
            env,
            factory_enabled_override=settings.factory_enabled,
        )
    except ProfileRejected as exc:
        return ValidateResponse(
            valid=False,
            action="REJECTED",
            errorCode=exc.code,
            errorMessage=exc.message,
        )

    if match is None:
        wait = 0 if action == "REJECTED" else int(catalog.estimated_minutes.get("coldAverage", 75))
        return ValidateResponse(
            valid=action != "REJECTED",
            requestedProfileHash=resolved.profile_hash,
            matchType=None,
            imageStatus="NOT_CREATED",
            action=action,
            estimatedWaitMinutes=wait,
            errorCode="IMAGE_CREATION_REQUIRED" if action == "IMAGE_CREATION_REQUIRED" else "NO_MATCH",
            errorMessage=(
                "No compatible READY image; factory disabled"
                if action == "REJECTED"
                else "Image creation required"
            ),
        )

    return ValidateResponse(
        valid=True,
        requestedProfileHash=resolved.profile_hash,
        matchedProfileHash=match.candidate.profile_hash,
        matchType=match.match_type,
        imageStatus=match.candidate.status,
        action=action,
        estimatedWaitMinutes=0,
        providedCapabilities=match.provided_capabilities,
        extraCapabilities=match.extra_capabilities,
        image=ImageRef(
            repository=match.candidate.repository,
            tag=match.candidate.tag,
            digest=match.candidate.image_digest,
        ),
    )


@router.post("/api/v1/build-requests", response_model=BuildRequestResponse)
def create_request(
    body: BuildRequestCreate,
    request: Request,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    actor: Annotated[str, Depends(actor_from_headers)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    catalog = get_catalog(request)
    settings = get_settings(request)
    try:
        row = create_build_request(
            session,
            catalog,
            payload=body.model_dump(),
            actor=actor,
            idempotency_key=idempotency_key,
            factory_enabled_override=settings.factory_enabled,
        )
    except ProfileRejected as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    except InvalidProjectInput as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc

    maybe_schedule_auto_advance(
        background_tasks=background_tasks,
        settings=settings,
        session_factory=request.app.state.session_factory,
        catalog=catalog,
        request_id=row.id,
        status=row.status,
    )
    return _request_to_response(row, session)


@router.get("/api/v1/build-requests/{request_id}", response_model=BuildRequestResponse)
def get_request(request_id: str, session: SessionDep):
    row = get_build_request(session, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="build request not found")
    return _request_to_response(row, session)


@router.get("/api/v1/build-requests/{request_id}/pod-template")
def get_pod_template(request_id: str, session: SessionDep, format: str = "yaml"):
    from fastapi.responses import PlainTextResponse, JSONResponse
    from app.domain.pod_template import render_windows_builder_pod, render_windows_builder_pod_yaml

    row = get_build_request(session, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="build request not found")
    response = _request_to_response(row, session)
    if not response.imageDigest:
        raise HTTPException(status_code=409, detail="image digest not resolved yet")
    windows_base = response.windowsBase or "ltsc2022"
    try:
        if format == "json":
            return JSONResponse(
                render_windows_builder_pod(
                    image_digest=response.imageDigest,
                    windows_base=windows_base,
                    request_id=request_id,
                )
            )
        yaml_text = render_windows_builder_pod_yaml(
            image_digest=response.imageDigest,
            windows_base=windows_base,
            request_id=request_id,
        )
        return PlainTextResponse(yaml_text, media_type="application/yaml")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/build-requests/{request_id}/events")
async def request_events(request_id: str, request: Request, session: SessionDep):
    from fastapi.responses import StreamingResponse
    import asyncio

    row = get_build_request(session, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="build request not found")

    last_event_id = 0
    if request.headers.get("Last-Event-ID"):
        try:
            last_event_id = int(request.headers["Last-Event-ID"])
        except ValueError:
            last_event_id = 0

    async def event_stream():
        nonlocal last_event_id
        # snapshot existing events, then poll briefly for MVP
        idle_rounds = 0
        while idle_rounds < 5:
            events = list_events(session, request_id, after_id=last_event_id)
            if not events:
                idle_rounds += 1
                yield "event: ping\ndata: {}\n\n"
                await asyncio.sleep(0.5)
                continue
            idle_rounds = 0
            for event in events:
                last_event_id = event.id
                payload = {
                    "id": event.id,
                    "eventType": event.event_type,
                    "message": event.message,
                    "metadata": json.loads(event.metadata_json) if event.metadata_json else None,
                    "createdAt": event.created_at.isoformat(),
                }
                yield f"id: {event.id}\nevent: {event.event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            current = get_build_request(session, request_id)
            if current and current.status in {
                "SUCCEEDED",
                "PROFILE_REJECTED",
                "PROJECT_BUILD_FAILED",
                "TEST_FAILED",
                "CANCELLED",
                "IMAGE_BUILD_FAILED",
            }:
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _verify_callback(request: Request, body: bytes) -> None:
    settings = get_settings(request)
    try:
        verify_hmac(
            secret=settings.callback_hmac_secret,
            timestamp_header=request.headers.get("X-Timestamp"),
            signature_header=request.headers.get("X-Signature"),
            body=body,
            skew_seconds=settings.callback_timestamp_skew_seconds,
        )
    except CallbackAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/internal/v1/build-events")
async def internal_build_events(request: Request, session: SessionDep):
    raw = await request.body()
    _verify_callback(request, raw)
    payload = InternalBuildEvent.model_validate_json(raw)
    try:
        row = apply_build_event_callback(
            session,
            request_id=payload.requestId,
            event_type=payload.eventType,
            message=payload.message,
            jenkins_build_number=payload.jenkinsBuildNumber,
            metadata=payload.metadata,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "status": row.status}


@router.post("/internal/v1/images/{profile_hash}/status")
async def internal_image_status(profile_hash: str, request: Request, session: SessionDep):
    raw = await request.body()
    _verify_callback(request, raw)
    payload = InternalImageStatus.model_validate_json(raw)
    try:
        row, affected = apply_image_status_callback(
            session,
            profile_hash=profile_hash,
            lease_id=payload.leaseId,
            status=payload.status,
            image_digest=payload.imageDigest,
            capability_profile=payload.capabilityProfile,
            message=payload.message,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "status": row.status,
        "profileHash": row.profile_hash,
        "affectedRequestIds": affected,
    }


@router.post("/internal/v1/images/{profile_hash}/factory-artifacts")
async def factory_artifacts(profile_hash: str, request: Request, session: SessionDep):
    raw = await request.body()
    _verify_callback(request, raw)
    profile = session.scalar(select(BuildProfile).where(BuildProfile.profile_hash == profile_hash))
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    build_input = json.loads(profile.normalized_profile_json)
    return build_factory_artifacts(build_input, profile_hash)


@router.post("/internal/v1/images/{profile_hash}/heartbeat")
async def factory_heartbeat(profile_hash: str, request: Request, session: SessionDep):
    raw = await request.body()
    _verify_callback(request, raw)
    payload = json.loads(raw.decode("utf-8"))
    lease_id = payload.get("leaseId")
    if not lease_id:
        raise HTTPException(status_code=400, detail="leaseId required")
    try:
        row = heartbeat_lease(session, profile_hash, lease_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True,
        "leaseExpiresAt": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
    }


@router.post("/internal/v1/reconcile/leases")
async def reconcile_leases(request: Request, session: SessionDep):
    raw = await request.body()
    _verify_callback(request, raw)
    return reconcile_expired_leases(session)


def _require_simulation_enabled(request: Request) -> None:
    settings = get_settings(request)
    if not settings.simulate_workers:
        raise HTTPException(
            status_code=403,
            detail="simulation disabled; set PORTAL_SIMULATE_WORKERS=true for local only",
        )


@router.post("/internal/v1/simulate/factory/{profile_hash}")
def simulate_factory(profile_hash: str, request: Request, session: SessionDep):
    _require_simulation_enabled(request)
    catalog = get_catalog(request)
    try:
        return simulate_factory_run(session, catalog, profile_hash=profile_hash)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/internal/v1/simulate/build-requests/{request_id}")
def simulate_build(request_id: str, request: Request, session: SessionDep, fail: bool = False):
    _require_simulation_enabled(request)
    try:
        return simulate_project_build(session, request_id=request_id, fail=fail)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/internal/v1/simulate/build-requests/{request_id}/auto")
def simulate_auto(request_id: str, request: Request, session: SessionDep):
    _require_simulation_enabled(request)
    catalog = get_catalog(request)
    try:
        return auto_advance_request(session, catalog, request_id=request_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/build-requests/{request_id}/simulate")
def simulate_request_from_api(request_id: str, request: Request, session: SessionDep):
    """Dev helper: advance factory/project simulation for a request."""
    _require_simulation_enabled(request)
    catalog = get_catalog(request)
    try:
        return auto_advance_request(session, catalog, request_id=request_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc