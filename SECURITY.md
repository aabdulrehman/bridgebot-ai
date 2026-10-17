# Security Policy

BridgeBot AI runs code on people's own computers, so we take security reports seriously.
The connector (`connector/local_connector.py`) is a single, readable, MIT-licensed file —
please audit it before you run it.

## Supported versions

| Component | Supported |
|---|---|
| `connector/local_connector.py` 1.2.x | ✅ security fixes |
| `connector/local_connector.py` < 1.2 | ⚠️ please upgrade (download the latest from `/downloads/local_connector.py`) |
| Cloud service (`backend/`, `frontend/`) | ✅ always the `main` branch as deployed |

## Reporting a vulnerability

* **Preferred:** GitHub → *Security* → *Report a vulnerability* (private advisory) on
  https://github.com/aabdulrehman/bridgebot-ai
* Or WhatsApp **+966 56 700 9846** with "SECURITY" in the first line (no exploit details in public issues, please).

We aim to acknowledge within 72 hours, ship a fix for critical issues within 7 days, and credit
reporters in the release notes (unless you prefer to stay anonymous).

## What the connector can and cannot do (threat model)

* It only makes an **outbound** WebSocket connection (`wss://…/ws/connect`), authenticated with a
  per-machine `bbk_…` key. Nothing listens on your machine.
* **Commands:** executed **without a shell** (`;`, `|`, `&&`, redirects don't work) and only if the
  executable is in `allowed_commands`. Interpreters (python, node, bash, powershell) are *not* allowed
  by default. Each task has a timeout; the whole process group is killed on timeout/cancel; output is capped.
* **HTTP tunnels:** forward only to `127.0.0.1`, optionally restricted with `--tunnel-ports` or turned off
  with `--no-tunnels`. Protect sensitive apps with a tunnel access token.
* **Skills:** injected skills are JSON files written under `skills_dir`; they are data, never executed.
* **Keys:** the cloud stores only SHA-256 hashes of connector (`bbk_`), user (`bbu_`), bot (`bbt_`) and tunnel
  (`tnl_`) keys. Rotate a machine key from the dashboard at any time — the old one stops working immediately.

## Scope

In scope: the connector, the cloud API/WebSockets, authentication, plan enforcement, tunnels, webhooks
(HMAC signatures), the marketplace/bot API.
Out of scope: denial of service against the free hosting tier, social engineering, issues requiring a
compromised local machine, and third-party services (Render, Supabase, PayPal, OpenRouter).
