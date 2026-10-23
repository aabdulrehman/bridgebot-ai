"""Live Mobilelty: pair an Android phone as a connector with a short one-time code.

The phone is just another owner-scoped connector (same /ws/connect auth, same RD_* owner-only relay).
Flow: the site calls POST /api/mobile/pair-code (logged in) -> creates/rotates a phone connector and returns a
6-digit code valid for 10 minutes. The app posts the code to POST /api/mobile/pair and receives the bbk_ key once.
"""
from __future__ import annotations

import secrets
import time
import uuid
from pathlib import Path
from typing import Dict, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing.service import enforce_machine_limit
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..hub import hub, kick
from ..models import Connector, User
from ..security import generate_api_key, hash_api_key

router = APIRouter(tags=["mobile"])
APK_URL = "https://github.com/aabdulrehman/bridgebot-ai/releases/download/live-mobilelty-v1/live-mobilelty.apk"
CODE_TTL_S = 600
_codes: Dict[str, Tuple[str, str, float]] = {}     # code -> (raw key, connector id, expiry)
_fails: Dict[str, list] = {}                        # ip -> recent failure timestamps (brute-force guard)


class PairCodeIn(BaseModel):
    device_name: str = Field("My Android phone", min_length=1, max_length=60)


class PairIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)


def _gc() -> None:
    now = time.time()
    for k in [k for k, v in _codes.items() if v[2] < now]:
        _codes.pop(k, None)


def _apk_path() -> Path | None:
    fe = Path(get_settings().frontend_dir).expanduser()
    if not fe.is_absolute():
        fe = (Path(__file__).resolve().parents[2] / fe).resolve()
    for candidate in (fe / "downloads" / "live-mobilelty.apk",
                      Path(__file__).resolve().parents[3] / "frontend" / "public" / "downloads" / "live-mobilelty.apk"):
        if candidate.is_file() and candidate.stat().st_size > 1000:
            return candidate
    return None


@router.post("/api/mobile/pair-code")
async def pair_code(body: PairCodeIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """Create (or re-key) the user's phone connector and return a one-time 6-digit code."""
    _gc()
    name = body.device_name.strip()
    conn = await db.scalar(select(Connector).where(Connector.user_id == user.id, Connector.machine_name == name))
    raw = generate_api_key()
    if conn is None:
        await enforce_machine_limit(db, user)
        conn = Connector(user_id=user.id, machine_name=name, api_key_hash=hash_api_key(raw), status="OFFLINE")
        db.add(conn)
    else:
        conn.api_key_hash = hash_api_key(raw)
        sess = hub.connectors.get(conn.id)
        if sess:
            await kick(sess.websocket, 4001, "Re-paired")
    await db.commit()
    code = f"{secrets.randbelow(1_000_000):06d}"
    while code in _codes:
        code = f"{secrets.randbelow(1_000_000):06d}"
    _codes[code] = (raw, str(conn.id), time.time() + CODE_TTL_S)
    return {"code": code, "expires_in": CODE_TTL_S, "connector_id": str(conn.id), "machine_name": name}


@router.post("/api/mobile/pair")
async def pair(body: PairIn, request: Request) -> dict:
    """Exchange a one-time code for the phone's connector key (no login needed on the phone)."""
    _gc()
    ip = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 600]
    if len(recent) >= 10:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many wrong codes, wait 10 minutes")
    item = _codes.pop(body.code.strip(), None)
    if item is None:
        _fails[ip] = recent + [now]
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wrong or expired code - make a new one on the website")
    raw, cid, _ = item
    uuid.UUID(cid)
    return {"api_key": raw, "connector_id": cid}


@router.get("/downloads/live-mobilelty.apk", include_in_schema=False)
async def apk():
    """Serve the signed APK from the site when bundled; otherwise redirect to the GitHub release."""
    path = _apk_path()
    if path is not None:
        return FileResponse(path, media_type="application/vnd.android.package-archive",
                            filename="live-mobilelty.apk")
    return RedirectResponse(APK_URL, status_code=302)
