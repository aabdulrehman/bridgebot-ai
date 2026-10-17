"""
Outbound webhooks: event fan-out, HMAC signing, delivery log and retries.

Events: connector.online, connector.offline, task.completed, skill.injected,
training.ready, plus webhook.test (manual "send test" button).

Each delivery is a POST with JSON body
    {"id": "<delivery id>", "event": "...", "created_at": "...", "data": {...}}
and headers
    X-BridgeBot-Event: <event>
    X-BridgeBot-Delivery: <delivery id>
    X-BridgeBot-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, f"{t}.{raw_body}")>

A background worker retries failures with exponential backoff
(30s, 2m, 8m, 32m …) up to WEBHOOK_MAX_ATTEMPTS, then marks FAILED.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import or_, select

from .config import get_settings
from .database import SessionLocal
from .models import Webhook, WebhookDelivery

log = logging.getLogger("bridgebot.webhooks")
settings = get_settings()

EVENTS = ["connector.online", "connector.offline", "task.completed", "skill.injected", "training.ready"]
_wakeup = asyncio.Event()


def sign(secret: str, body: bytes, ts: Optional[int] = None) -> str:
    """Return the X-BridgeBot-Signature header value."""
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_signature(secret: str, body: bytes, header: str, tolerance_s: int = 300) -> bool:
    """Reference verifier (receivers can copy this)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(time.time() - ts) > tolerance_s:
        return False
    expected = sign(secret, body, ts).split("v1=")[1]
    return hmac.compare_digest(expected, parts.get("v1", ""))


def validate_webhook_url(url: str) -> None:
    """Reject non-http(s) URLs and (in production) private / loopback targets (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL must be http(s)://host/…")
    if settings.allow_private_webhooks:
        return
    if parsed.scheme != "https":
        raise ValueError("Webhook URLs must use https in production")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except socket.gaierror:
        raise ValueError("Cannot resolve webhook host")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("Webhook host resolves to a private address")


async def emit(user_id: uuid.UUID, event: str, data: Dict[str, Any]) -> int:
    """Queue `event` for every active webhook of the user subscribed to it. Returns #queued."""
    try:
        async with SessionLocal() as db:
            hooks = list(await db.scalars(select(Webhook).where(Webhook.user_id == user_id, Webhook.is_active.is_(True))))
            n = 0
            for h in hooks:
                if event == "webhook.test" or event in (h.events_json or []) or "*" in (h.events_json or []):
                    db.add(WebhookDelivery(webhook_id=h.id, event=event, payload_json=data, status="PENDING",
                                           next_attempt_at=datetime.now(timezone.utc)))
                    n += 1
            if n:
                await db.commit()
                _wakeup.set()
            return n
    except Exception as exc:  # noqa: BLE001 — never break the caller
        log.error("webhook emit failed: %s", exc)
        return 0


async def _deliver(db, d: WebhookDelivery, hook: Webhook) -> None:  # type: ignore[no-untyped-def]
    body = json.dumps({"id": str(d.id), "event": d.event, "created_at": d.created_at.isoformat(), "data": d.payload_json},
                      default=str, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "BridgeBot-Webhooks/1.0",
        "X-BridgeBot-Event": d.event,
        "X-BridgeBot-Delivery": str(d.id),
        "X-BridgeBot-Signature": sign(hook.secret, body),
    }
    d.attempts += 1
    try:
        validate_webhook_url(hook.url)
        async with httpx.AsyncClient(timeout=settings.webhook_timeout_s, follow_redirects=False) as client:
            resp = await client.post(hook.url, content=body, headers=headers)
        d.response_code = resp.status_code
        d.response_body = resp.text[:1000]
        ok = 200 <= resp.status_code < 300
        d.error = None if ok else f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        ok = False
        d.error = f"{type(exc).__name__}: {exc}"[:1000]
    now = datetime.now(timezone.utc)
    if ok:
        d.status, d.delivered_at, d.next_attempt_at = "SUCCESS", now, None
    elif d.attempts >= settings.webhook_max_attempts:
        d.status, d.next_attempt_at = "FAILED", None
    else:
        d.next_attempt_at = now + timedelta(seconds=settings.webhook_retry_base_s * (4 ** (d.attempts - 1)))


async def process_due(limit: int = 50) -> int:
    """Deliver all due PENDING deliveries once. Returns the number processed."""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        rows = list(await db.scalars(
            select(WebhookDelivery).where(WebhookDelivery.status == "PENDING",
                                          or_(WebhookDelivery.next_attempt_at.is_(None), WebhookDelivery.next_attempt_at <= now))
            .order_by(WebhookDelivery.created_at).limit(limit)))
        for d in rows:
            hook = await db.get(Webhook, d.webhook_id)
            if hook is None or not hook.is_active:
                d.status, d.error = "FAILED", "webhook disabled or deleted"
                continue
            await _deliver(db, d, hook)
        await db.commit()
        return len(rows)


async def retry_now(delivery_id: uuid.UUID) -> None:
    """Re-queue a delivery immediately (used by the 'Retry' button)."""
    async with SessionLocal() as db:
        d = await db.get(WebhookDelivery, delivery_id)
        if d:
            d.status, d.next_attempt_at = "PENDING", datetime.now(timezone.utc)
            if d.attempts >= settings.webhook_max_attempts:
                d.attempts = settings.webhook_max_attempts - 1
            await db.commit()
    _wakeup.set()


async def worker(stop: asyncio.Event) -> None:
    """Background loop started in the app lifespan."""
    log.info("webhook worker started")
    while not stop.is_set():
        _wakeup.clear()  # clear first so events emitted while processing wake the next loop
        try:
            await process_due()
        except Exception as exc:  # noqa: BLE001
            log.error("webhook worker error: %s", exc)
        try:
            await asyncio.wait_for(_wakeup.wait(), timeout=min(5.0, max(0.5, settings.webhook_retry_base_s)))
        except asyncio.TimeoutError:
            pass
