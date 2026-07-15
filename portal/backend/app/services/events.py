from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import BuildEvent


def append_event(
    session: Session,
    request_id: str,
    event_type: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> BuildEvent:
    event = BuildEvent(
        build_request_id=request_id,
        event_type=event_type,
        message=message,
        metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
    )
    session.add(event)
    session.flush()
    return event