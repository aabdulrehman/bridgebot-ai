"""
HTTP tunnels (ngrok-style) over the connector WebSocket.

    https://<host>/t/{slug}/{path}?{query}
        → TUNNEL_REQUEST {request_id, port, method, path, headers, body_b64}   (cloud → connector)
        → connector does http://127.0.0.1:{port}{path} on the user's machine
        ← TUNNEL_RESPONSE {request_id, status, headers, body_b64, error?}       (connector → cloud)

Owner API: GET/POST /api/tunnels, PATCH/DELETE /api/tunnels/{id}, POST /api/tunnels/{id}/rotate-token
Optional access token: header `X-Tunnel-Token: tnl_…` or query `?tunnel_token=tnl_…` (stripped before forwarding).
"""
from __future__ import annotations

import asyncio
import base64
import html
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import write_audit
from ..billing.service import enforce_tunnel_limit
from ..config import get_settings
from ..database import SessionLocal, get_db
from ..deps import get_current_user
from ..hub import hub
from ..models import Connector, Tunnel, User
from ..security import hash_api_key

router = APIRouter(tags=["tunnels"])
settings = get_settings()

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length"}
STRIP_REQUEST = HOP_BY_HOP | {"x-tunnel-token", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-forwarded-prefix",
                              "x-real-ip", "forwarded", "cf-connecting-ip", "true-client-ip", "x-request-start", "rndr-id"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,46}[a-z0-9]$")
RESERVED = {"api", "admin", "www", "static", "docs", "app", "ws", "healthz"}


# --------------------------------------------------------------------------- schemas / helpers
class TunnelCreateIn(BaseModel):
    connector_id: uuid.UUID
    local_port: int = Field(ge=1, le=65535)
    name: str = Field(default="", max_length=120)
    slug: Optional[str] = Field(default=None, max_length=48)
    require_token: bool = False


class TunnelPatchIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    local_port: Optional[int] = Field(default=None, ge=1, le=65535)
    is_active: Optional[bool] = None
    require_token: Optional[bool] = None


def public_base(request: Request) -> str:
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


def tunnel_out(t: Tunnel, base: str, machine: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": str(t.id), "slug": t.slug, "name": t.name, "connector_id": str(t.connector_id), "machine_name": machine,
        "local_port": t.local_port, "protected": t.token_hash is not None, "is_active": t.is_active,
        "online": hub.is_online(t.connector_id), "public_url": f"{base}/t/{t.slug}/",
        "requests_count": t.requests_count, "bytes_in": t.bytes_in, "bytes_out": t.bytes_out, "last_status": t.last_status,
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None, "created_at": t.created_at.isoformat(),
    }


def _new_token() -> str:
    return "tnl_" + secrets.token_urlsafe(24)


async def _owned(db: AsyncSession, user: User, tunnel_id: uuid.UUID) -> Tunnel:
    t = await db.get(Tunnel, tunnel_id)
    if t is None or t.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tunnel not found")
    return t


# --------------------------------------------------------------------------- owner API
@router.get("/api/tunnels")
async def list_tunnels(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> List[Dict[str, Any]]:
    base = public_base(request)
    rows = (await db.execute(select(Tunnel, Connector.machine_name).join(Connector, Connector.id == Tunnel.connector_id)
                             .where(Tunnel.user_id == user.id).order_by(Tunnel.created_at))).all()
    return [tunnel_out(t, base, m) for t, m in rows]


@router.post("/api/tunnels", status_code=201)
async def create_tunnel(body: TunnelCreateIn, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Create a public URL for a local port on one of your machines (plan limit: Free 1 · Pro 5 · Business 20)."""
    await enforce_tunnel_limit(db, user)
    conn = await db.get(Connector, body.connector_id)
    if conn is None or conn.user_id != user.id:
        raise HTTPException(404, "Connector not found")
    if body.slug:
        slug = body.slug.strip().lower()
        if not SLUG_RE.match(slug) or slug in RESERVED:
            raise HTTPException(422, "slug must be 4-48 chars: lowercase letters, digits and '-'")
        if await db.scalar(select(Tunnel).where(Tunnel.slug == slug)) is not None:
            raise HTTPException(409, f"Slug '{slug}' is taken")
    else:
        slug = secrets.token_hex(5)
    token = _new_token() if body.require_token else None
    t = Tunnel(user_id=user.id, connector_id=conn.id, slug=slug, name=body.name.strip() or f"port {body.local_port}",
               local_port=body.local_port, token_hash=hash_api_key(token) if token else None)
    db.add(t)
    await db.commit()
    _CACHE.clear()
    await db.refresh(t)
    await write_audit(conn.id, "TUNNEL_CREATED", {"tunnel_id": str(t.id), "slug": slug, "port": body.local_port})
    return {"tunnel": tunnel_out(t, public_base(request), conn.machine_name), "access_token": token}


@router.patch("/api/tunnels/{tunnel_id}")
async def patch_tunnel(tunnel_id: uuid.UUID, body: TunnelPatchIn, request: Request, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    t = await _owned(db, user, tunnel_id)
    token = None
    if body.name is not None:
        t.name = body.name
    if body.local_port is not None:
        t.local_port = body.local_port
    if body.is_active is not None:
        t.is_active = body.is_active
    if body.require_token is True and t.token_hash is None:
        token = _new_token()
        t.token_hash = hash_api_key(token)
    elif body.require_token is False:
        t.token_hash = None
    await db.commit()
    _CACHE.clear()
    return {"tunnel": tunnel_out(t, public_base(request)), "access_token": token}


@router.post("/api/tunnels/{tunnel_id}/rotate-token")
async def rotate_tunnel_token(tunnel_id: uuid.UUID, request: Request, user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    t = await _owned(db, user, tunnel_id)
    token = _new_token()
    t.token_hash = hash_api_key(token)
    await db.commit()
    _CACHE.clear()
    return {"tunnel": tunnel_out(t, public_base(request)), "access_token": token}


@router.delete("/api/tunnels/{tunnel_id}", status_code=204)
async def delete_tunnel(tunnel_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> None:
    t = await _owned(db, user, tunnel_id)
    await db.delete(t)
    await db.commit()
    _CACHE.clear()


# --------------------------------------------------------------------------- public proxy
def _page(code: int, title: str, detail: str) -> HTMLResponse:
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{code} · {html.escape(title)} · BridgeBot tunnel</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#020617;color:#e2e8f0;font:16px system-ui,sans-serif}}
.c{{max-width:520px;padding:32px;border:1px solid #1e293b;border-radius:16px;background:#0f172a;text-align:center}}
h1{{margin:.2em 0;font-size:56px;color:#34d399}}p{{color:#94a3b8;line-height:1.5}}a{{color:#38bdf8}}</style></head>
<body><div class="c"><h1>{code}</h1><h2>{html.escape(title)}</h2><p>{html.escape(detail)}</p>
<p><a href="/docs/tunnels/">BridgeBot tunnels</a></p></div></body></html>"""
    return HTMLResponse(body, status_code=code, headers={"X-BridgeBot-Tunnel-Error": str(code), "Cache-Control": "no-store"})


def _rewrite_location(value: str, slug: str, port: int, base: str) -> str:
    """Keep redirects inside the tunnel: /x → /t/slug/x, http://localhost:port/x → base/t/slug/x."""
    if value.startswith("/") and not value.startswith("//"):
        return f"/t/{slug}{value}"
    parts = urlsplit(value)
    if parts.hostname in ("localhost", "127.0.0.1", "0.0.0.0") and (parts.port or 80) == port:
        rest = parts.path or "/"
        return f"{base}/t/{slug}{rest}" + (f"?{parts.query}" if parts.query else "")
    return value


class _Info:
    """Immutable snapshot of what the proxy needs about a tunnel (cached a few seconds per slug)."""

    __slots__ = ("id", "slug", "connector_id", "local_port", "token_hash", "is_active", "owner_status", "machine_name")

    def __init__(self, t: Tunnel, owner: User, conn: Connector) -> None:
        self.id, self.slug, self.connector_id, self.local_port = t.id, t.slug, t.connector_id, t.local_port
        self.token_hash, self.is_active = t.token_hash, t.is_active
        self.owner_status, self.machine_name = owner.status, conn.machine_name


CACHE_TTL_S = 5.0
_CACHE: Dict[str, Any] = {}          # slug -> (expires_monotonic, _Info | None); cleared on every owner change
_BG: set = set()


def _bg(coro: Any) -> None:
    """Fire-and-forget (stats writes must not delay the proxied response)."""
    task = asyncio.create_task(coro)
    _BG.add(task)
    task.add_done_callback(_BG.discard)


async def _lookup(slug: str) -> Optional[_Info]:
    now = asyncio.get_running_loop().time()
    hit = _CACHE.get(slug)
    if hit and hit[0] > now:
        return hit[1]
    async with SessionLocal() as db:   # one round-trip: tunnel + owner + machine
        row = (await db.execute(select(Tunnel, User, Connector).join(User, User.id == Tunnel.user_id)
                                .join(Connector, Connector.id == Tunnel.connector_id).where(Tunnel.slug == slug))).first()
    info = _Info(*row) if row else None
    if len(_CACHE) > 5000:
        _CACHE.clear()
    _CACHE[slug] = (now + CACHE_TTL_S, info)
    return info


async def _record(tunnel_id: uuid.UUID, status_code: int, bytes_in: int, bytes_out: int) -> None:
    try:
        async with SessionLocal() as db:
            await db.execute(update(Tunnel).where(Tunnel.id == tunnel_id).values(
                requests_count=Tunnel.requests_count + 1, bytes_in=Tunnel.bytes_in + bytes_in,
                bytes_out=Tunnel.bytes_out + bytes_out, last_status=status_code, last_used_at=datetime.now(timezone.utc)))
            await db.commit()
    except Exception:  # noqa: BLE001 — stats must never break proxying
        pass


@router.api_route("/t/{slug}", methods=METHODS, include_in_schema=False)
async def proxy_root(slug: str, request: Request) -> Response:
    if request.method in ("GET", "HEAD"):
        q = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/t/{slug}/{q}", status_code=307)
    return await proxy(slug, "", request)


@router.api_route("/t/{slug}/{path:path}", methods=METHODS, include_in_schema=False)
async def proxy(slug: str, path: str, request: Request) -> Response:
    """Forward any HTTP request to http://127.0.0.1:{port}/{path} on the owner's machine."""
    t = await _lookup(slug.lower())
    if t is None:
        return _page(404, "Tunnel not found", f"No tunnel named “{slug}” exists.")
    if t.owner_status != "ACTIVE":
        return _page(403, "Tunnel unavailable", "The owner's account is not active.")
    if not t.is_active:
        return _page(503, "Tunnel paused", "The owner has paused this tunnel.")

    raw_qs = request.scope.get("query_string", b"").decode("latin-1")
    parts = [p for p in raw_qs.split("&") if p]
    provided = request.headers.get("x-tunnel-token") or next(
        (v for k, v in parse_qsl(raw_qs, keep_blank_values=True) if k == "tunnel_token"), None)
    kept_qs = "&".join(p for p in parts if p.split("=", 1)[0] != "tunnel_token")
    if t.token_hash and (not provided or not secrets.compare_digest(hash_api_key(provided), t.token_hash)):
        return _page(401, "Access token required", "This tunnel is protected. Send the header X-Tunnel-Token or ?tunnel_token=.")

    session = hub.connectors.get(t.connector_id)
    if session is None:
        return _page(502, "Tunnel offline", f"The machine “{t.machine_name}” is not connected. Start local_connector.py on it and retry.")
    if "tunnel" not in session.features:
        return _page(502, "Connector update required", "The connector on this machine is too old for tunnels. Download the latest local_connector.py.")

    limit = settings.tunnel_max_body_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        return _page(413, "Request too large", f"Tunnel request bodies are limited to {limit // (1024 * 1024)} MB.")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return _page(413, "Request too large", f"Tunnel request bodies are limited to {limit // (1024 * 1024)} MB.")
        chunks.append(chunk)
    body = b"".join(chunks)

    base = public_base(request)
    headers = [[k, v] for k, v in request.headers.items() if k.lower() not in STRIP_REQUEST]
    client_ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()) or (request.client.host if request.client else "")
    headers += [["X-Forwarded-For", client_ip], ["X-Forwarded-Proto", urlsplit(base).scheme],
                ["X-Forwarded-Host", urlsplit(base).netloc], ["X-Forwarded-Prefix", f"/t/{t.slug}"]]
    # forward the still-encoded path (keeps %2F, spaces, unicode exactly as the client sent them)
    raw_path = request.scope.get("raw_path", b"").decode("latin-1") or request.url.path
    prefix = f"/t/{slug}"
    rest = raw_path[len(prefix):] if raw_path.lower().startswith(prefix.lower()) else "/" + path
    target = (rest or "/") + (f"?{kept_qs}" if kept_qs else "")

    request_id = uuid.uuid4().hex
    fut: "asyncio.Future[Dict[str, Any]]" = asyncio.get_running_loop().create_future()
    hub.tunnel_waiters[request_id] = (t.connector_id, fut)
    try:
        sent = await hub.send_to_connector(t.connector_id, {
            "type": "TUNNEL_REQUEST", "request_id": request_id, "tunnel_id": str(t.id), "port": t.local_port,
            "method": request.method, "path": target, "headers": headers,
            "body_b64": base64.b64encode(body).decode() if body else "", "timeout_s": settings.tunnel_timeout_s,
            "max_response_bytes": limit})
        if not sent:
            return _page(502, "Tunnel offline", f"Could not reach the machine “{t.machine_name}”.")
        try:
            msg = await asyncio.wait_for(fut, timeout=settings.tunnel_timeout_s + 5)
        except asyncio.TimeoutError:
            _bg(_record(t.id, 504, len(body), 0))
            return _page(504, "Tunnel timeout", f"The local service on port {t.local_port} did not answer within {int(settings.tunnel_timeout_s)} s.")
        except ConnectionError:
            return _page(502, "Tunnel offline", "The machine disconnected while handling the request.")
    finally:
        hub.tunnel_waiters.pop(request_id, None)

    if msg.get("error"):
        code = int(msg.get("status") or 502)
        _bg(_record(t.id, code, len(body), 0))
        title = {504: "Tunnel timeout", 403: "Tunnel refused by machine"}.get(code, "Local service error")
        return _page(code, title, f"port {t.local_port}: {msg['error']}")
    try:
        data = base64.b64decode(msg.get("body_b64") or "")
    except (ValueError, TypeError):
        return _page(502, "Bad tunnel response", "The connector sent an invalid body.")
    code = int(msg.get("status") or 502)
    no_body = request.method == "HEAD" or code in (204, 304) or code < 200
    resp = Response(content=b"" if no_body else data, status_code=code)
    raw: list = []
    upstream_len = None
    for pair in msg.get("headers") or []:
        try:
            k, v = str(pair[0]), str(pair[1])
        except (IndexError, TypeError):
            continue
        kl = k.lower()
        if kl == "content-length":
            upstream_len = v
            continue
        if kl in HOP_BY_HOP:
            continue
        if kl == "location":
            v = _rewrite_location(v, t.slug, t.local_port, base)
        raw.append((kl.encode("latin-1"), v.encode("latin-1", "replace")))
    raw.append((b"x-bridgebot-tunnel", t.slug.encode()))
    if request.method == "HEAD" and upstream_len and upstream_len.isdigit():
        raw.append((b"content-length", upstream_len.encode()))
    elif not no_body:
        raw.append((b"content-length", str(len(data)).encode()))
    resp.raw_headers = raw
    _bg(_record(t.id, code, len(body), len(data)))
    return resp
