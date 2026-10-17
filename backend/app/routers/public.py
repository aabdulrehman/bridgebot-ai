"""Public (no auth) endpoints: plan catalogue, site config, connector download."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..models import Plan
from ..schemas import PlanOut
from ..webhooks import EVENTS

router = APIRouter(prefix="/api", tags=["public"])
settings = get_settings()

# Searched in order: Docker image path, repo checkout path.
_CONNECTOR_CANDIDATES = [Path("/app/connector"), Path(__file__).resolve().parents[3] / "connector"]
_DOWNLOADABLE = {"bridgebot_sdk.py", "local_connector.py", "connector_config.example.json", "requirements.txt"}


@router.get("/plans", response_model=List[PlanOut])
async def list_plans(db: AsyncSession = Depends(get_db)) -> list[Plan]:
    """Active subscription plans (used by the Pricing page)."""
    return list(await db.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)))


@router.get("/public-config")
async def public_config() -> Dict[str, Any]:
    """Non-secret settings the frontend needs."""
    return {
        "app_name": settings.app_name,
        "support_whatsapp": settings.support_whatsapp,
        "payment_provider": settings.payment_provider,
        "manual_payment_instructions": settings.manual_payment_instructions,
        "paypal_me_url": settings.paypal_me_url,
        "paypal_account_name": settings.paypal_account_name,
        "webhook_events": EVENTS,
        "llm_enabled": bool(settings.openrouter_api_key),
    }


@router.get("/downloads/{filename}")
async def download(filename: str) -> FileResponse:
    """Download the local connector daemon files."""
    if filename not in _DOWNLOADABLE:
        raise HTTPException(404, "Not found")
    for base in _CONNECTOR_CANDIDATES:
        f = base / filename
        if f.is_file():
            return FileResponse(f, filename=filename, media_type="text/plain")
    raise HTTPException(404, "Connector files are not bundled with this deployment")
