"""
Central configuration loaded from environment variables / `.env`.

Every setting has a zero-cost default so the backend runs locally with
`uvicorn server:app` and nothing else (SQLite + template skill generator).
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core -----------------------------------------------------------------
    app_name: str = "BridgeBot AI"
    environment: str = "development"
    # Postgres example (Supabase): postgresql+asyncpg://user:pass@host:5432/postgres
    # A plain postgres:// or postgresql:// URL is auto-rewritten to asyncpg.
    database_url: str = "sqlite+aiosqlite:///./bridgebot.db"
    auto_create_tables: bool = True

    # --- Auth -----------------------------------------------------------------
    jwt_secret: str = "CHANGE_ME_dev_only_secret_please_override_32+chars"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    # --- CORS -----------------------------------------------------------------
    # Comma-separated list, "*" allowed for development.
    cors_origins: str = "*"

    # --- Service 2: Master Bot generation ------------------------------------
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # "openrouter/free" is OpenRouter's router across currently-free models.
    openrouter_model: str = "openrouter/free"
    # Tried in order if the primary model fails (rate-limit, removed, ...).
    openrouter_fallback_models: str = "google/gemma-4-31b-it:free,nvidia/nemotron-3-super-120b-a12b:free"
    openrouter_timeout_s: float = 90.0
    signup_token_grant: int = 10000
    min_training_cost: int = 250

    # Static PWA export to serve at "/" (relative to backend/). Ignored if missing.
    frontend_dir: str = "../frontend/out"

    # --- Admin bootstrap ------------------------------------------------------
    # ONLY the account whose email equals ADMIN_EMAIL (case-insensitive) is admin.
    # The role is re-derived on every authenticated request, so nobody else can
    # ever hold it (there is no "first registered user becomes admin" fallback).
    # If ADMIN_PASSWORD is also set, that account is created at startup when missing.
    admin_email: str = "Abaudii12@gmail.com"
    admin_password: str = ""

    # --- Billing ----------------------------------------------------------------
    # "manual" = request upgrade + admin approval (bank transfer, cash, etc.).
    # Future: "stripe" / "paddle" (see app/billing/providers.py).
    payment_provider: str = "manual"
    # Support / sales WhatsApp (digits only, international format, no +).
    support_whatsapp: str = "966567009846"
    # PayPal.Me link (manual verification): users pay {paypal_me_url}/{amount}USD and submit the transaction id.
    paypal_me_url: str = "https://paypal.me/BunnyOnMoney"
    paypal_account_name: str = "Abdul Rehman Chohan"
    manual_payment_instructions: str = (
        "Pay with PayPal (paypal.me/BunnyOnMoney — Abdul Rehman Chohan) and submit the PayPal transaction ID / "
        "your PayPal email here, or pay/confirm via WhatsApp (+966 56 700 9846). An admin verifies and approves "
        "your upgrade — usually within a few hours."
    )
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    paddle_api_key: str = ""
    paddle_webhook_secret: str = ""
    billing_period_days: int = 30

    # --- Webhooks ---------------------------------------------------------------
    # Outbound webhooks to private/loopback IPs are blocked in production (SSRF guard).
    # None = allow only when ENVIRONMENT != production.
    webhook_allow_private: Optional[bool] = None
    webhook_max_attempts: int = 5
    webhook_retry_base_s: float = 30.0   # backoff: base * 4^(attempt-1)
    webhook_timeout_s: float = 10.0
    # ---- HTTP tunnels (/t/{slug}/…) ----
    tunnel_timeout_s: float = 30.0
    tunnel_max_body_bytes: int = 8 * 1024 * 1024        # request and response bodies (base64 over the WS)
    public_base_url: str = ""          # e.g. https://bridgebot-ai.onrender.com (used in docs/examples)

    # --- Bridge ---------------------------------------------------------------
    heartbeat_timeout_s: int = 90           # close connector socket if silent this long
    default_task_timeout_s: int = 120

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        """Accept Heroku/Supabase-style URLs and force the async driver."""
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v[len("postgresql://"):]
        if v.startswith("sqlite:///"):
            v = "sqlite+aiosqlite:///" + v[len("sqlite:///"):]
        return v

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def fallback_model_list(self) -> List[str]:
        return [m.strip() for m in self.openrouter_fallback_models.split(",") if m.strip()]

    @property
    def admin_email_normalized(self) -> str:
        return self.admin_email.strip().lower()

    def is_admin_email(self, email: str) -> bool:
        return bool(self.admin_email_normalized) and email.strip().lower() == self.admin_email_normalized

    @property
    def allow_private_webhooks(self) -> bool:
        if self.webhook_allow_private is not None:
            return self.webhook_allow_private
        return self.environment.lower() != "production"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Cached singleton accessor."""
    return Settings()
