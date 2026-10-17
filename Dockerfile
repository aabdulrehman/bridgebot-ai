# =============================================================================
# BridgeBot AI — single-container image: FastAPI hub + WebSockets + static PWA.
# Works on Render (free web service), Koyeb (free instance), Fly, any Docker host.
#   docker build -t bridgebot . && docker run -p 8000:8000 bridgebot
# =============================================================================

# ---- Stage 1: build the static Next.js PWA (same-origin API) ----------------
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# prebuild copies the connector into public/downloads (served at /downloads/…)
COPY connector/ /connector/
# Empty => the PWA talks to the origin that serves it (this container).
ENV NEXT_PUBLIC_API_URL="" NEXT_TELEMETRY_DISABLED=1
RUN rm -f .env.local && npm run build

# ---- Stage 2: Python runtime -------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PORT=8000 FRONTEND_DIR=/app/frontend/out DATABASE_URL=sqlite+aiosqlite:////data/bridgebot.db
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
COPY --from=frontend /fe/out /app/frontend/out
COPY connector/ /app/connector/
RUN useradd -m -u 1000 app && mkdir -p /data && chown -R app /data /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz')" || exit 1
# ONE worker: the live WebSocket session pool is in-process memory.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT} --workers 1 --ws-max-size 33554432 --proxy-headers --forwarded-allow-ips='*'"]
