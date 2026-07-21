from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.schemas import (
    BuildRequestCreate,
    BuildRequestResponse,
    EnsureImageRequest,
    EnsureImageResponse,
    ImageRef,
    ImageStatusResponse,
    InternalBuildEvent,
    InternalImageStatus,
    ValidateRequest,
    ValidateResponse,
)
from app.domain.profile_resolver import ProfileRejected
from app.domain.project_validation import InvalidProjectInput
from app.domain.git_resolve import GitResolveError
from app.security.hmac_auth import CallbackAuthError, verify_hmac
from app.services.build_requests import (
    IdempotencyConflict,
    ImageNotReady,
    apply_build_event_callback,
    create_build_request,
    get_build_request,
    is_factory_enabled,
    list_events,
)
from app.services.factory import FactoryBusy, build_factory_artifacts, heartbeat_lease, require_active_factory_lease
from app.services.image_ensure import ImageQuarantined, ensure_image, get_image_status
from app.services.image_resolve import apply_image_status_callback, resolve_image
from app.services.reconcile import reconcile_expired_leases
from app.services.simulation import auto_advance_request, simulate_factory_run, simulate_project_build
from app.services.worker_schedule import maybe_schedule_auto_advance, maybe_schedule_factory_simulate
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
    authorization: Annotated[str | None, Header()] = None,
):
    from app.security.actors import (
        Actor,
        constant_time_token_lookup,
        extract_bearer,
        parse_api_tokens,
    )

    settings = request.app.state.settings
    tokens = parse_api_tokens(settings.api_tokens)
    bearer = extract_bearer(authorization)
    if bearer:
        if not tokens:
            raise HTTPException(
                status_code=401,
                detail="Bearer provided but PORTAL_API_TOKENS is not configured",
            )
        actor = constant_time_token_lookup(tokens, bearer)
        if actor is None:
            raise HTTPException(status_code=401, detail="invalid API token")
        return actor
    if settings.require_auth:
        raise HTTPException(status_code=401, detail="Bearer token required")
    roles = frozenset(
        r.strip() for r in (settings.default_actor_roles or "builder").split(",") if r.strip()
    )
    return Actor(name=x_actor or settings.default_actor, roles=roles)


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
        commitResolution=getattr(row, "commit_resolution", None),
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
def options(
    request: Request,
    actor: Annotated[object, Depends(actor_from_headers)],
):
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
        "localFactory": settings.local_factory,
        "requireAuth": settings.require_auth,
    }


@router.post("/api/v1/build-environment/validate", response_model=ValidateResponse)
def validate(
    body: ValidateRequest,
    request: Request,
    session: SessionDep,
    actor: Annotated[object, Depends(actor_from_headers)],
):
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


@router.post("/api/v1/images/ensure", response_model=EnsureImageResponse)
def ensure_image_endpoint(
    body: EnsureImageRequest,
    request: Request,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    actor: Annotated[object, Depends(actor_from_headers)],
):
    """Step 1: reuse a READY image or start/join Image Factory. Does not start project build."""
    catalog = get_catalog(request)
    settings = get_settings(request)
    actor_name = getattr(actor, "name", str(actor))
    try:
        result = ensure_image(
            session,
            catalog,
            environment=body.environment.model_dump(),
            actor=actor_name,
            factory_enabled_override=settings.factory_enabled,
        )
    except ProfileRejected as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": exc.code,
                "message": exc.message,
            },
        ) from exc
    except FactoryBusy as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "FACTORY_BUSY",
                "message": str(exc),
                "retryable": True,
                "blocking": getattr(exc, "blocking", []) or [],
            },
        ) from exc
    except ImageQuarantined as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": exc.message,
                "matchedProfileHash": exc.profile_hash,
            },
        ) from exc

    # Only the creator schedules local factory simulation (not joiners).
    if result.factory_phase == "CREATING" and result.matched_profile_hash:
        maybe_schedule_factory_simulate(
            background_tasks=background_tasks,
            settings=settings,
            session_factory=request.app.state.session_factory,
            catalog=catalog,
            profile_hash=result.matched_profile_hash,
            image_status=result.image_status,
        )

    data = result.to_dict()
    image = None
    if data.get("image"):
        image = ImageRef(**data["image"])
    local_image_ref = local_tar_path = local_tar_file = None
    if settings.local_factory and data.get("matchedProfileHash"):
        try:
            status = get_image_status(session, data["matchedProfileHash"])
            local_image_ref = status.get("localImageRef")
            local_tar_path = status.get("localTarPath")
            local_tar_file = status.get("localTarFile")
        except LookupError:
            pass
    return EnsureImageResponse(
        requestedProfileHash=data["requestedProfileHash"],
        matchedProfileHash=data["matchedProfileHash"],
        matchType=data["matchType"],
        action=data["action"],
        imageStatus=data["imageStatus"],
        estimatedWaitMinutes=data["estimatedWaitMinutes"],
        providedCapabilities=data["providedCapabilities"],
        extraCapabilities=data["extraCapabilities"],
        image=image,
        windowsBase=data["windowsBase"],
        factoryLeaseId=data["factoryLeaseId"],
        leaseExpiresAt=data.get("leaseExpiresAt"),
        errorCode=data["errorCode"],
        errorMessage=data["errorMessage"],
        ready=data["ready"],
        localImageRef=local_image_ref,
        localTarPath=local_tar_path,
        localTarFile=local_tar_file,
    )


@router.get("/api/v1/images/{profile_hash}", response_model=ImageStatusResponse)
def image_status_endpoint(
    profile_hash: str,
    session: SessionDep,
    actor: Annotated[object, Depends(actor_from_headers)],
):
    """Poll image readiness after POST /api/v1/images/ensure."""
    try:
        data = get_image_status(session, profile_hash)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    image = ImageRef(**data["image"]) if data.get("image") else None
    return ImageStatusResponse(
        profileHash=data["profileHash"],
        imageStatus=data["imageStatus"],
        ready=data["ready"],
        image=image,
        windowsBase=data["windowsBase"],
        factoryLeaseId=data["factoryLeaseId"],
        leaseExpiresAt=data["leaseExpiresAt"],
        localImageRef=data.get("localImageRef"),
        localTarPath=data.get("localTarPath"),
        localTarFile=data.get("localTarFile"),
    )


@router.post("/api/v1/images/{profile_hash}/rearm-lease", response_model=ImageStatusResponse)
def rearm_factory_lease_endpoint(
    profile_hash: str,
    request: Request,
    session: SessionDep,
    actor: Annotated[object, Depends(actor_from_headers)],
):
    """Operator helper: mint/refresh factory lease while image is still CREATING.

    Use after reboot when leaseExpiresAt is in the past and Ensure alone (old Portal)
    did not move the TTL. Does not change profileHash.
    """
    from app.services.factory import _rearm_inflight_lease, get_active_image

    catalog = get_catalog(request)
    image = get_active_image(session, profile_hash, for_update=True)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    if image.status not in {"CREATING", "VALIDATING"}:
        raise HTTPException(
            status_code=409,
            detail=f"image status is {image.status}; only CREATING/VALIDATING can rearm",
        )
    _rearm_inflight_lease(image, catalog)
    session.flush()
    data = get_image_status(session, profile_hash)
    image_ref = ImageRef(**data["image"]) if data.get("image") else None
    return ImageStatusResponse(
        profileHash=data["profileHash"],
        imageStatus=data["imageStatus"],
        ready=data["ready"],
        image=image_ref,
        windowsBase=data["windowsBase"],
        factoryLeaseId=data["factoryLeaseId"],
        leaseExpiresAt=data["leaseExpiresAt"],
        localImageRef=data.get("localImageRef"),
        localTarPath=data.get("localTarPath"),
        localTarFile=data.get("localTarFile"),
    )


@router.post("/api/v1/images/{profile_hash}/simulate")
def simulate_image_factory_from_api(
    profile_hash: str,
    request: Request,
    session: SessionDep,
    actor: Annotated[object, Depends(actor_from_headers)],
):
    """Dev helper: complete Image Factory simulation for a profile (no build request)."""
    _require_simulation_enabled(request, actor)
    catalog = get_catalog(request)
    try:
        return simulate_factory_run(session, catalog, profile_hash=profile_hash)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/build-requests", response_model=BuildRequestResponse)
def create_request(
    body: BuildRequestCreate,
    request: Request,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    actor: Annotated[object, Depends(actor_from_headers)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Step 2: start project build. Requires a READY image from /images/ensure."""
    catalog = get_catalog(request)
    settings = get_settings(request)
    actor_name = getattr(actor, "name", str(actor))
    try:
        row = create_build_request(
            session,
            catalog,
            payload=body.model_dump(),
            actor=actor_name,
            idempotency_key=idempotency_key,
            factory_enabled_override=settings.factory_enabled,
            git_resolver=request.app.state.git_resolver,
        )
    except ProfileRejected as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    except ImageNotReady as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": exc.message,
                "requestedProfileHash": exc.requested_profile_hash,
                "imageStatus": exc.image_status,
            },
        ) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message}) from exc
    except (InvalidProjectInput, GitResolveError) as exc:
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
def get_request(
    request_id: str,
    session: SessionDep,
    actor: Annotated[object, Depends(actor_from_headers)],
):
    row = get_build_request(session, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="build request not found")
    return _request_to_response(row, session)


@router.get("/api/v1/build-requests/{request_id}/pod-template")
def get_pod_template(
    request_id: str,
    request: Request,
    session: SessionDep,
    actor: Annotated[object, Depends(actor_from_headers)],
    format: str = "yaml",
):
    from fastapi.responses import PlainTextResponse, JSONResponse
    from app.domain.pod_template import render_windows_builder_pod, render_windows_builder_pod_yaml
    from app.domain.registry import registry_from_settings

    row = get_build_request(session, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="build request not found")
    response = _request_to_response(row, session)
    if not response.imageDigest:
        raise HTTPException(status_code=409, detail="image digest not resolved yet")
    windows_base = response.windowsBase or "ltsc2022"
    reg = registry_from_settings(get_settings(request))
    pod_kwargs = dict(
        image_digest=response.imageDigest,
        windows_base=windows_base,
        request_id=request_id,
        pull_secret_name=reg.pull_secret_name,
        registry_host=reg.host,
        registry_final_repo=reg.final_repository,
    )
    try:
        if format == "json":
            return JSONResponse(render_windows_builder_pod(**pod_kwargs))
        yaml_text = render_windows_builder_pod_yaml(**pod_kwargs)
        return PlainTextResponse(yaml_text, media_type="application/yaml")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/build-requests/{request_id}/events")
async def request_events(
    request_id: str,
    request: Request,
    session: SessionDep,
    actor: Annotated[object, Depends(actor_from_headers)],
):
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
    payload = json.loads(raw.decode("utf-8") or "{}")
    lease_id = payload.get("leaseId")
    if not lease_id:
        raise HTTPException(status_code=400, detail="leaseId required")
    try:
        require_active_factory_lease(session, profile_hash, lease_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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


def _require_simulation_enabled(request: Request, actor=None) -> None:
    settings = get_settings(request)
    if not settings.simulate_workers:
        raise HTTPException(
            status_code=403,
            detail="simulation disabled; set PORTAL_SIMULATE_WORKERS=true for local only",
        )
    if actor is not None and hasattr(actor, "can_simulate") and not actor.can_simulate:
        raise HTTPException(status_code=403, detail="actor not allowed to simulate")


@router.post("/internal/v1/simulate/factory/{profile_hash}")
async def simulate_factory(profile_hash: str, request: Request, session: SessionDep):
    raw = await request.body()
    _verify_callback(request, raw)
    _require_simulation_enabled(request)
    catalog = get_catalog(request)
    try:
        return simulate_factory_run(session, catalog, profile_hash=profile_hash)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/internal/v1/simulate/build-requests/{request_id}")
async def simulate_build(request_id: str, request: Request, session: SessionDep, fail: bool = False):
    raw = await request.body()
    _verify_callback(request, raw)
    _require_simulation_enabled(request)
    try:
        return simulate_project_build(session, request_id=request_id, fail=fail)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/internal/v1/simulate/build-requests/{request_id}/auto")
async def simulate_auto(request_id: str, request: Request, session: SessionDep):
    raw = await request.body()
    _verify_callback(request, raw)
    _require_simulation_enabled(request)
    catalog = get_catalog(request)
    try:
        return auto_advance_request(session, catalog, request_id=request_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/build-requests/{request_id}/simulate")
def simulate_request_from_api(
    request_id: str,
    request: Request,
    session: SessionDep,
    actor: Annotated[object, Depends(actor_from_headers)],
):
    """Dev helper: advance factory/project simulation for a request."""
    _require_simulation_enabled(request, actor)
    catalog = get_catalog(request)
    try:
        return auto_advance_request(session, catalog, request_id=request_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc