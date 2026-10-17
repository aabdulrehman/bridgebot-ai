#!/usr/bin/env python3
"""
Offline unit test for the OpenRouter code path of the Master Bot.

Mocks the OpenRouter HTTP API with httpx.MockTransport to verify:
  * request shape (model, auth header, messages)
  * JSON extraction from fenced/prose LLM replies
  * fallback to the next free model on HTTP 429
  * template fallback when every model fails
Run: python tests/test_master_bot.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
os.environ["OPENROUTER_MODEL"] = "openrouter/free"
os.environ["OPENROUTER_FALLBACK_MODELS"] = "google/gemma-4-31b-it:free"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app import master_bot  # noqa: E402

LLM_JSON = {
    "name": "Clinic Concierge", "niche": "dental clinic",
    "system_prompt": "You are a dental clinic concierge..." * 5,
    "prompt_templates": [{"name": "greet", "template": "Hi {name}", "variables": ["name"]}],
    "tools": [{"name": "book_slot", "description": "Book", "parameters": {"type": "object", "properties": {}, "required": []}}],
    "memory": {"type": "buffer", "window": 10, "seed_facts": []},
    "guardrails": ["no medical diagnosis"],
    "evaluation": [],
}
calls: list[str] = []


def handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    calls.append(body["model"])
    assert request.headers["authorization"] == "Bearer sk-or-test"
    assert body["messages"][0]["role"] == "system"
    if body["model"] == "openrouter/free":
        return httpx.Response(429, json={"error": {"message": "rate limited"}})
    content = "Sure! Here is your skill:\n```json\n" + json.dumps(LLM_JSON) + "\n```"
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 1234}})


_Real = httpx.AsyncClient


class _Mock(_Real):
    def __init__(self, *a, **kw):  # type: ignore[no-untyped-def]
        kw["transport"] = httpx.MockTransport(handler)
        super().__init__(*a, **kw)


async def main() -> None:
    master_bot.httpx.AsyncClient = _Mock  # type: ignore[misc]
    payload, gen, cost = await master_bot.generate_skill_payload("Dental clinic in Jeddah, book appointments", "Smile", ["book"], "warm")
    print("generator:", gen, "| cost:", cost, "| skill_id:", payload["skill_id"], "| models tried:", calls)
    assert gen == "openrouter:google/gemma-4-31b-it:free" and cost == 1234 and payload["name"] == "Clinic Concierge"

    def always_fail(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})

    class _Bad(_Real):
        def __init__(self, *a, **kw):  # type: ignore[no-untyped-def]
            kw["transport"] = httpx.MockTransport(always_fail)
            super().__init__(*a, **kw)

    master_bot.httpx.AsyncClient = _Bad  # type: ignore[misc]
    payload, gen, cost = await master_bot.generate_skill_payload("Dental clinic in Jeddah, book appointments")
    print("all models bad -> generator:", gen, "| tools:", len(payload["tools"]))
    assert gen == "template-fallback"
    print("✅ master bot tests passed")


if __name__ == "__main__":
    asyncio.run(main())
