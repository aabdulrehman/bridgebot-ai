# API-only image (use when the frontend is hosted separately, e.g. on Vercel).
#   cd backend && docker build -t bridgebot-api . && docker run -p 8000:8000 bridgebot-api
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8000 \
    DATABASE_URL=sqlite+aiosqlite:////data/bridgebot.db
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . ./
RUN useradd -m -u 1000 app && mkdir -p /data && chown -R app /data /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
