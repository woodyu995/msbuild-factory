from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BuildImage, utcnow
from app.services.factory import wake_waiters_for_image


def reconcile_expired_leases(session: Session) -> dict:
    """Mark expired CREATING/VALIDATING leases as FAILED and fail waiters."""
    now = utcnow()
    rows = session.scalars(
        select(BuildImage)
        .where(
            BuildImage.status.in_(["CREATING", "VALIDATING"]),
            BuildImage.lease_expires_at.is_not(None),
            BuildImage.lease_expires_at < now,
        )
        .with_for_update()
    ).all()
    failed: list[str] = []
    woken: list[str] = []
    for image in rows:
        image.status = "FAILED"
        image.failure_code = "LEASE_EXPIRED"
        image.retry_count = (image.retry_count or 0) + 1
        image.lease_id = None
        image.lease_owner = None
        image.updated_at = now
        affected = wake_waiters_for_image(
            session,
            image,
            success=False,
            failure_message="Image factory lease expired",
        )
        failed.append(image.profile_hash)
        woken.extend(affected)
    session.flush()
    return {"expiredImages": failed, "affectedRequests": woken}