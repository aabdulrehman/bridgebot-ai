"""
Plan catalogue — the single source of truth, upserted into the `plans` table
on startup. Edit here (or the table) to change pricing/limits.

Limits: -1 = unlimited, 0 = feature not included.
"""
from __future__ import annotations

from typing import Any, Dict, List

PLANS: List[Dict[str, Any]] = [
    {
        "id": "FREE", "name": "Free", "price_monthly_cents": 0, "currency": "USD", "sort_order": 0,
        "monthly_tokens": 5_000, "max_machines": 1, "max_tasks_per_day": 50,
        "max_webhooks": 1, "max_api_keys": 1, "max_bots": 1, "max_tunnels": 1, "training_access": True, "inbound_webhooks": False,
        "features_json": [
            "Cloud-to-Local Bridge: 1 machine",
            "Live stdout/stderr log streaming",
            "50 remote tasks / day",
            "On-Demand Training: 5,000 platform tokens / month (~5 skills)",
            "1 outbound webhook",
            "1 personal API key",
            "Universal Bot API: 1 linked bot (Hermes, OpenClaw, n8n, HTTP…)",
            "Marketplace: Free Master Bots",
            "HTTP tunnel: 1 public URL to a local port",
            "Community support",
        ],
    },
    {
        "id": "PRO", "name": "Pro", "price_monthly_cents": 1900, "currency": "USD", "sort_order": 1,
        "monthly_tokens": 100_000, "max_machines": 5, "max_tasks_per_day": 1_000,
        "max_webhooks": 10, "max_api_keys": 5, "max_bots": 10, "max_tunnels": 5, "training_access": True, "inbound_webhooks": True,
        "features_json": [
            "Cloud-to-Local Bridge: 5 machines",
            "Live log streaming + audit history",
            "1,000 remote tasks / day",
            "On-Demand Training: 100,000 platform tokens / month",
            "10 outbound webhooks (HMAC-signed, retries)",
            "Inbound webhook → trigger tasks from Zapier/n8n/Make",
            "5 personal API keys",
            "Universal Bot API: 10 linked bots",
            "Marketplace: Free + Pro Master Bots, one-click 'Make my bot a master'",
            "HTTP tunnels: 5 public URLs (n8n webhooks, local APIs)",
            "Email support",
        ],
    },
    {
        "id": "BUSINESS", "name": "Business", "price_monthly_cents": 7900, "currency": "USD", "sort_order": 2,
        "monthly_tokens": 500_000, "max_machines": 25, "max_tasks_per_day": -1,
        "max_webhooks": 50, "max_api_keys": 20, "max_bots": 50, "max_tunnels": 20, "training_access": True, "inbound_webhooks": True,
        "features_json": [
            "Cloud-to-Local Bridge: 25 machines",
            "Unlimited remote tasks",
            "On-Demand Training: 500,000 platform tokens / month",
            "50 outbound webhooks + inbound webhook",
            "20 personal API keys",
            "Universal Bot API: 50 linked bots",
            "Marketplace: every Master Bot incl. Business-only",
            "HTTP tunnels: 20 public URLs",
            "Priority support & onboarding",
        ],
    },
]

PLAN_IDS = {p["id"] for p in PLANS}
