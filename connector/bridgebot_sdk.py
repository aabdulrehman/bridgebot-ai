#!/usr/bin/env python3
"""
BridgeBot AI — Python Bot SDK + universal adapter (stdlib only, Python 3.8+).

Link ANY bot to BridgeBot AI with a bot token (bbt_…) from the dashboard → Bots.

1) As a library
    from bridgebot_sdk import BridgeBot
    bot = BridgeBot("https://bridgebot-ai.onrender.com", "bbt_...")
    @bot.on_message
    def handle(update):                 # return a string to reply
        return "You said: " + update["text"]
    bot.run()                           # long-polls, auto-installs skills, ACKs, replies

2) As a ready-made adapter (no code)
    # Hermes Agent (Nous Research)
    python bridgebot_sdk.py --token bbt_... --exec "hermes chat --oneshot -Q -q {text}" \
        --skills-dir ~/.hermes/skills --skill-format skillmd
    # OpenClaw
    python bridgebot_sdk.py --token bbt_... --exec "openclaw agent --message {text}" \
        --skills-dir ~/.openclaw/workspace/skills --skill-format skillmd
    # Any local HTTP bot (n8n webhook, LangChain/FastAPI server, Ollama wrapper…)
    python bridgebot_sdk.py --token bbt_... --http http://localhost:5678/webhook/my-bot

Commands are executed WITHOUT a shell: "{text}" is substituted as a single argument,
so message text can never inject shell commands. Group chatter from other bots is
ignored unless you pass --group-chat.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

__version__ = "1.0.0"
log = logging.getLogger("bridgebot.sdk")
Handler = Callable[[Dict[str, Any]], Optional[str]]


class BridgeBotError(RuntimeError):
    pass


class BridgeBot:
    """Minimal client for the BridgeBot Bot API."""

    def __init__(self, server: str, token: str, skills_dir: Optional[str] = None, skill_format: str = "json",
                 group_chat: bool = False, timeout: int = 25) -> None:
        self.server = server.rstrip("/").replace("wss://", "https://").replace("ws://", "http://")
        self.token = token
        self.skills_dir = Path(os.path.expanduser(skills_dir)) if skills_dir else None
        self.skill_format = skill_format
        self.group_chat = group_chat
        self.timeout = timeout
        self.offset = 0
        self._handler: Optional[Handler] = None
        self._skill_handler: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------ HTTP
    def _req(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.server + path, data=data, method=method, headers={
            "Authorization": f"Bot {self.token}", "Content-Type": "application/json", "User-Agent": f"bridgebot-sdk/{__version__}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or 30) as resp:  # noqa: S310 — user-configured server
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise BridgeBotError(f"{method} {path} → HTTP {exc.code}: {exc.read()[:300]!r}") from exc

    def me(self) -> Dict[str, Any]:
        return self._req("GET", "/api/bot/me")

    def get_updates(self, timeout: Optional[int] = None) -> List[Dict[str, Any]]:
        t = self.timeout if timeout is None else timeout
        res = self._req("GET", f"/api/bot/updates?offset={self.offset}&timeout={t}", timeout=t + 15)
        ups = res.get("updates", [])
        if ups:
            self.offset = res["next_offset"]
        return ups

    def send(self, text: str, reply_to: Optional[int] = None, chat: Optional[str] = None, group_id: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"text": text}
        if reply_to is not None:
            body["reply_to"] = reply_to
        if chat:
            body["chat"] = chat
        if group_id:
            body["group_id"] = group_id
        return self._req("POST", "/api/bot/messages", body)

    def ack(self, update_id: int, ok: bool = True, error: Optional[str] = None) -> None:
        self._req("POST", "/api/bot/ack", {"update_id": update_id, "ok": ok, "error": error})

    def skills(self) -> Dict[str, Any]:
        return self._req("GET", "/api/bot/skills")

    # ------------------------------------------------------------------ decorators
    def on_message(self, fn: Handler) -> Handler:
        self._handler = fn
        return fn

    def on_skill(self, fn: Callable[[Dict[str, Any]], None]) -> Callable[[Dict[str, Any]], None]:
        self._skill_handler = fn
        return fn

    # ------------------------------------------------------------------ skills
    def install_skill(self, skill: Dict[str, Any]) -> Optional[Path]:
        """Persist a skill payload: JSON file, or SKILL.md folder (Hermes / OpenClaw format)."""
        if self._skill_handler:
            self._skill_handler(skill)
        if not self.skills_dir:
            return None
        sid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(skill.get("skill_id") or "skill")).strip("._") or "skill"
        if self.skill_format == "skillmd":
            d = self.skills_dir / sid
            d.mkdir(parents=True, exist_ok=True)
            target = d / "SKILL.md"
            target.write_text(to_skill_md(skill), encoding="utf-8")
            (d / "skill.json").write_text(json.dumps(skill, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            target = self.skills_dir / f"{sid}.json"
            target.write_text(json.dumps(skill, indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    # ------------------------------------------------------------------ loop
    def handle(self, u: Dict[str, Any]) -> None:
        kind = u.get("kind")
        if kind == "skill_inject":
            try:
                path = self.install_skill((u.get("payload") or {}).get("skill") or {})
                self.ack(u["update_id"], True)
                log.info("🧠 installed skill %s%s", ((u.get("payload") or {}).get("skill") or {}).get("skill_id"),
                         f" → {path}" if path else "")
            except Exception as exc:  # noqa: BLE001
                self.ack(u["update_id"], False, f"{type(exc).__name__}: {exc}")
            return
        if kind == "welcome":
            log.info("👋 %s", u.get("text"))
            self.ack(u["update_id"])
            return
        if u.get("chat") == "group" and kind != "task" and not self.group_chat:
            self.ack(u["update_id"])  # group chatter ignored unless --group-chat
            return
        if self._handler is None:
            return
        try:
            reply = self._handler(u)
        except Exception as exc:  # noqa: BLE001
            reply = f"⚠️ bot error: {type(exc).__name__}: {exc}"
        if reply:
            self.send(str(reply)[:20000], reply_to=u["update_id"])

    def run(self) -> None:
        info = self.me()
        log.info("✅ linked as bot '%s' (%s) — waiting for messages", info["name"], info["id"])
        backoff = 1.0
        while True:
            try:
                for u in self.get_updates():
                    self.handle(u)
                backoff = 1.0
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("poll error: %s — retrying in %.0fs", exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)


def to_skill_md(skill: Dict[str, Any]) -> str:
    """Render a BridgeBot skill payload as a SKILL.md (YAML frontmatter + markdown) for Hermes / OpenClaw."""
    name = re.sub(r"[^a-z0-9_]+", "_", str(skill.get("skill_id") or skill.get("name") or "skill").lower()).strip("_")
    desc = str(skill.get("niche") or skill.get("name") or "BridgeBot skill").replace("\n", " ")[:300]
    lines = ["---", f"name: {name}", f"description: {json.dumps(desc)}", "---", "", f"# {skill.get('name') or name}", ""]
    if skill.get("persona"):
        lines += ["## Persona", str(skill["persona"]), ""]
    if skill.get("system_prompt"):
        lines += ["## Instructions", str(skill["system_prompt"]), ""]
    if skill.get("tools"):
        lines += ["## Tools"] + [f"- `{t.get('name')}` — {t.get('description', '')}" for t in skill["tools"] if isinstance(t, dict)] + [""]
    if skill.get("prompt_templates"):
        lines += ["## Prompt templates"] + [f"- **{t.get('name')}**: {t.get('template')}" for t in skill["prompt_templates"] if isinstance(t, dict)] + [""]
    if skill.get("guardrails"):
        lines += ["## Guardrails"] + [f"- {g}" for g in skill["guardrails"]] + [""]
    lines += [f"_Installed by BridgeBot AI from master `{(skill.get('master') or {}).get('name', 'BridgeBot')}`._", ""]
    return "\n".join(lines)


def exec_handler(template: str, timeout: int = 300) -> Handler:
    """Run a CLI bot per message (no shell). "{text}" becomes one argv element."""
    parts = shlex.split(template)
    if not any("{text}" in p for p in parts):
        parts.append("{text}")

    def handle(u: Dict[str, Any]) -> Optional[str]:
        argv = [p.replace("{text}", u.get("text", "")) for p in parts]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)  # noqa: S603 — no shell
        out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        return out or f"(exit {proc.returncode}, no output)"
    return handle


def http_handler(url: str, timeout: int = 120) -> Handler:
    """Forward each message to a local HTTP bot; reply with its `text`/`reply`/`output` (or raw body)."""
    def handle(u: Dict[str, Any]) -> Optional[str]:
        req = urllib.request.Request(url, data=json.dumps({"text": u.get("text"), "update": u}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — user-configured local URL
            raw = resp.read().decode("utf-8", "replace")
        try:
            data = json.loads(raw)
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                return str(data.get("text") or data.get("reply") or data.get("output") or json.dumps(data))
        except ValueError:
            pass
        return raw.strip()
    return handle


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Link any bot to BridgeBot AI (Bot API adapter).")
    ap.add_argument("--server", default=os.getenv("BRIDGEBOT_SERVER_URL", "https://bridgebot-ai.onrender.com"))
    ap.add_argument("--token", default=os.getenv("BRIDGEBOT_BOT_TOKEN"), help="bbt_… bot token")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--exec", dest="exec_cmd", help='CLI bot, e.g. "hermes chat --oneshot -Q -q {text}"')
    g.add_argument("--http", dest="http_url", help="local HTTP bot URL (n8n webhook, LangChain server…)")
    ap.add_argument("--skills-dir", default=os.getenv("BRIDGEBOT_SKILLS_DIR"), help="where injected skills are written")
    ap.add_argument("--skill-format", choices=["json", "skillmd"], default="json", help="skillmd = Hermes/OpenClaw SKILL.md")
    ap.add_argument("--group-chat", action="store_true", help="also answer group messages from other bots")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not a.token:
        ap.error("--token (or BRIDGEBOT_BOT_TOKEN) is required")
    bot = BridgeBot(a.server, a.token, a.skills_dir, a.skill_format, a.group_chat)
    if a.exec_cmd:
        bot.on_message(exec_handler(a.exec_cmd))
    elif a.http_url:
        bot.on_message(http_handler(a.http_url))
    else:
        bot.on_message(lambda u: f"🤖 echo: {u.get('text')}")
    try:
        bot.run()
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    sys.exit(main())
