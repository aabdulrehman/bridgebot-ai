"""Helper for writing audit log rows without breaking the caller on failure."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from .database import SessionLocal
from .models import AuditLog

log = logging.getLogger("bridgebot.audit")


async def write_audit(connector_id: Optional[uuid.UUID], event_type: str, payload: Dict[str, Any]) -> None:
    """Persist an audit event in its own short transaction (best effort)."""
    try:
        async with SessionLocal() as session:
            session.add(AuditLog(connector_id=connector_id, event_type=event_type[:50], payload_json=payload))
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.error("audit write failed (%s): %s", event_type, exc)
