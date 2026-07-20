from __future__ import annotations

from app.config import Settings
from app.domain.catalog import Catalog
from app.services.simulation import auto_advance_request


def run_auto_advance(session_factory, catalog: Catalog, request_id: str) -> dict:
    with session_factory() as session:
        try:
            result = auto_advance_request(session, catalog, request_id=request_id)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def run_factory_simulate(session_factory, catalog: Catalog, profile_hash: str) -> dict:
    from app.services.simulation import simulate_factory_run

    with session_factory() as session:
        try:
            result = simulate_factory_run(session, catalog, profile_hash=profile_hash)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def maybe_schedule_auto_advance(
    *,
    background_tasks,
    settings: Settings,
    session_factory,
    catalog: Catalog,
    request_id: str,
    status: str,
) -> bool:
    if not settings.simulate_workers:
        return False
    if status not in {
        "IMAGE_BUILD_QUEUED",
        "IMAGE_WAITING",
        "BUILD_QUEUED",
    }:
        return False
    background_tasks.add_task(run_auto_advance, session_factory, catalog, request_id)
    return True


def maybe_schedule_factory_simulate(
    *,
    background_tasks,
    settings: Settings,
    session_factory,
    catalog: Catalog,
    profile_hash: str,
    image_status: str,
) -> bool:
    """When ensure starts/joins factory and simulate_workers is on, finish factory locally."""
    if not settings.simulate_workers:
        return False
    if image_status not in {"CREATING", "VALIDATING"}:
        return False
    background_tasks.add_task(run_factory_simulate, session_factory, catalog, profile_hash)
    return True
