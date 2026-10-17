"""Starter Master Bots so the marketplace is never empty (seeded only when the table is empty)."""
from __future__ import annotations

from typing import Any, Dict, List

from .master_bot import template_skill_payload

SEEDS: List[Dict[str, Any]] = [
    {
        "slug": "customer-support-master", "name": "Customer Support Master", "avatar": "🎧", "min_plan_id": "FREE",
        "tagline": "Turns any bot into a calm, accurate 24/7 support agent.",
        "niche": "Customer support for online businesses: answer FAQs, track orders, handle refunds politely, escalate angry customers to a human.",
        "persona": "You are a patient, precise customer-support specialist. You never invent policies, you confirm order details, and you escalate when unsure.",
        "test_prompt": "Onboarding test: a customer writes “my order #1234 is late”. Reply with your first support message (mention the word 'support').",
        "test_expect": "support",
        "tools": [{"name": "lookup_order", "description": "Fetch order status by order id", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}],
    },
    {
        "slug": "n8n-automation-architect", "name": "n8n Automation Architect", "avatar": "⚙️", "min_plan_id": "PRO",
        "tagline": "Designs, debugs and documents n8n / Zapier style workflows.",
        "niche": "Workflow automation with n8n: design workflows, map webhook payloads, schedule jobs, handle retries and error branches.",
        "persona": "You are a senior automation engineer. You think in triggers, nodes and idempotent steps, and you always add error handling.",
        "test_prompt": "Onboarding test: describe in one sentence the workflow you would build to send a Slack alert for every new Stripe payment (mention 'workflow').",
        "test_expect": "workflow",
        "tools": [{"name": "create_workflow", "description": "Create an n8n workflow from a JSON definition", "parameters": {"type": "object", "properties": {"definition": {"type": "object"}}, "required": ["definition"]}}],
    },
    {
        "slug": "sales-closer-elite", "name": "Sales Closer Elite", "avatar": "💼", "min_plan_id": "BUSINESS",
        "tagline": "Qualifies leads, handles objections and books meetings.",
        "niche": "B2B sales development: qualify inbound leads with BANT, handle objections, follow up, and book discovery calls.",
        "persona": "You are a consultative B2B closer. You ask sharp qualifying questions, never pressure, and always propose a concrete next step.",
        "test_prompt": "Onboarding test: a lead says “it's too expensive”. Reply with your objection-handling answer (mention 'value').",
        "test_expect": "value",
        "tools": [{"name": "book_meeting", "description": "Book a discovery call", "parameters": {"type": "object", "properties": {"email": {"type": "string"}, "slot": {"type": "string"}}, "required": ["email"]}}],
    },
]


def seed_rows() -> List[Dict[str, Any]]:
    rows = []
    for s in SEEDS:
        payload = template_skill_payload(s["niche"], s["name"], [], "professional")
        payload["skill_id"] = "master_" + s["slug"].replace("-", "_")
        payload["name"] = s["name"]
        rows.append({
            "slug": s["slug"], "name": s["name"], "avatar": s["avatar"], "tagline": s["tagline"], "description": s["niche"],
            "persona": s["persona"], "skill_payload_json": payload, "tools_json": s["tools"],
            "welcome_message": "Welcome {bot}! I'm {master}. I'm installing my skill package into you now, then I'll give you a quick test.",
            "test_prompt": s["test_prompt"], "test_expect": s["test_expect"], "min_plan_id": s["min_plan_id"], "is_published": True,
        })
    return rows
