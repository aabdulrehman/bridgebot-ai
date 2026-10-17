"""
BridgeBot AI — Cloud Backend & WebSocket Routing Hub.

Run locally:   uvicorn server:app --reload --port 8000
Production:    uvicorn server:app --host 0.0.0.0 --port $PORT --workers 1
(Use ONE worker: the live session pool is in-process memory.)
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.hub import hub
from app.models import Connector
from app import webhooks
from app.routers import admin, apikeys, auth, billing, connectors, hooks, public, tasks, training, webhooks_api, ws

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bridgebot")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables, seed data and reset stale ONLINE flags on boot."""
    await init_db()
    async with SessionLocal() as db:
        await db.execute(update(Connector).values(status="OFFLINE"))
        await db.commit()
    if "CHANGE_ME" in settings.jwt_secret and settings.environment == "production":
        log.warning("JWT_SECRET is the default value — set a strong secret in production!")
    log.info("BridgeBot AI backend ready (db=%s, llm=%s, admin=%s)", "sqlite" if settings.is_sqlite else "postgres",
             "openrouter" if settings.openrouter_api_key else "template-fallback", settings.admin_email_normalized or "-")
    stop = asyncio.Event()
    worker = asyncio.create_task(webhooks.worker(stop))
    try:
        yield
    finally:
        stop.set()
        worker.cancel()


app = FastAPI(title="BridgeBot AI", version="1.0.0", lifespan=lifespan)

_origins = settings.cors_origin_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials="*" not in _origins,  # credentials + wildcard is invalid per CORS spec
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    """Never leak stack traces to clients."""
    log.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/api", tags=["meta"])
async def api_root() -> Dict[str, str]:
    return {"service": "BridgeBot AI", "docs": "/docs", "health": "/healthz"}


@app.get("/healthz", tags=["meta"])
async def healthz() -> Dict[str, object]:
    """Health probe for Render/Koyeb."""
    return {"ok": True, "online_connectors": len(hub.connectors), "llm": bool(settings.openrouter_api_key)}


app.include_router(public.router)
app.include_router(auth.router)
app.include_router(billing.router)
app.include_router(apikeys.router)
app.include_router(webhooks_api.router)
app.include_router(hooks.router)
app.include_router(admin.router)
app.include_router(connectors.router)
app.include_router(training.router)
app.include_router(tasks.router)
app.include_router(ws.router)


# -----------------------------------------------------------------------------
# Optional: serve the statically-exported PWA (frontend/out) from the same
# origin, so ONE free web service hosts API + WebSockets + dashboard.
# Mounted last so API/WS routes always win.
# -----------------------------------------------------------------------------
_frontend = Path(settings.frontend_dir).expanduser()
if not _frontend.is_absolute():
    _frontend = (Path(__file__).parent / _frontend).resolve()
if (_frontend / "index.html").is_file():
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
    log.info("serving frontend from %s", _frontend)
else:
    @app.get("/", tags=["meta"])
    async def root() -> Dict[str, str]:
        return {"service": "BridgeBot AI", "docs": "/docs", "health": "/healthz"}
