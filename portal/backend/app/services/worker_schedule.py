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
