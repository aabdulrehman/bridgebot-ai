# BridgeBot connector (open source, MIT)

`local_connector.py` is the small daemon that links **your** computer to the BridgeBot AI cloud
(https://bridgebot-ai.onrender.com). It is one readable Python file with a single dependency
(`websockets`). Read it, audit it, fork it — that's the point.

```bash
pip install "websockets>=13"
python local_connector.py --server https://bridgebot-ai.onrender.com --api-key bbk_YOUR_KEY --machine-name home-pc
```

Get the `bbk_` key from Dashboard → Control Room → **Add machine**.

## What it does

| Feature | How |
|---|---|
| Remote tasks | `TASK` → runs an **allowlisted** executable *without a shell*, streams every stdout/stderr line live (`LOG`), returns `TASK_RESULT` |
| Skill injection | `SKILL_INJECT` → writes `skills/<agent>/<skill_id>.json` (+ optional POST to `skill_hook_url`), replies `SKILL_ACK` |
| HTTP tunnels | `TUNNEL_REQUEST` → `http://127.0.0.1:<port>` → `TUNNEL_RESPONSE` (base64 bodies, 8 MB, 30 s) |
| Linked bots | Bot API "connector" delivery for Hermes / OpenClaw / n8n style agents |

## Reliability (tested against the live service — see `docs/phase1-testing.md`)

* **Reconnects forever** with exponential backoff + jitter (1 s → 30 s cap). Backoff resets only after the
  cloud's `WELCOME`, so an accept-then-close loop can't become a tight retry loop.
* **Dead-link detection:** WebSocket ping every 10 s, 10 s timeout → a silently dead network path
  (Wi-Fi switch, VPN change) is detected in ≤ 20 s. App-level `PING` every 20 s keeps proxies busy.
* **Sleep/wake:** a wall-clock jump larger than half the server heartbeat timeout (laptop lid, `SIGSTOP`)
  drops the stale socket immediately and reconnects.
* **No lost task output on reconnect:** `TASK_STARTED`/`LOG`/`TASK_RESULT`/`SKILL_ACK` go through a bounded
  outbox (20 000 events) that is flushed in order after reconnecting.
* **Auth failures** (invalid/rotated key, deleted machine, suspended account — close codes 4401/4403/4001-4003)
  are logged once with a fix hint and retried slowly (60 s, doubling to 4 min) instead of hammering the server.
* **Line-buffered streaming:** children get `PYTHONUNBUFFERED=1`; output is read in chunks so lines are
  forwarded the moment they're printed. For other programs that block-buffer when piped use
  `stdbuf -oL <cmd>` (Linux) or the program's own "line-buffered" flag.
* **Caps:** `max_output_lines` (5000, then one "output truncated" notice, the task keeps running),
  `max_line_chars` (4000, very long lines — even multi-MB without newline — are truncated, never crash),
  per-task `timeout_s` (default 120 s, up to 3600 s) kills the **whole process group**.

## Configuration

`connector_config.json` (next to the script, or `--config path`) < environment < CLI flags.

| Key | Default | Env / flag |
|---|---|---|
| `server_url` | `ws://localhost:8000` | `BRIDGEBOT_SERVER_URL`, `--server` (http(s)/ws(s) all accepted) |
| `api_key` | — | `BRIDGEBOT_API_KEY`, `--api-key` |
| `machine_name` | hostname | `BRIDGEBOT_MACHINE_NAME`, `--machine-name` |
| `allowed_commands` | echo, ls, dir, pwd, whoami, hostname, uname, date, uptime, df, free, nvidia-smi, ollama, n8n, git, ping, docker, ps | config only |
| `allow_any_command` | `false` | `BRIDGEBOT_ALLOW_ANY_COMMAND=1`, `--allow-any-command` (dangerous) |
| `default_timeout_s` / `max_output_lines` / `max_line_chars` | 120 / 5000 / 4000 | config |
| `max_concurrent_tasks` | 4 | config |
| `heartbeat_interval_s` | 20 | config |
| `ws_ping_interval_s` / `ws_ping_timeout_s` | 10 / 10 | config |
| `reconnect_base_s` / `reconnect_max_s` | 1 / 30 | config |
| `auth_retry_s` | 60 | `BRIDGEBOT_AUTH_RETRY_S` |
| `tunnels_enabled` | `true` | `--no-tunnels`, `BRIDGEBOT_NO_TUNNELS=1` |
| `allowed_tunnel_ports` | any | `--tunnel-ports 5678,8000`, `BRIDGEBOT_TUNNEL_PORTS` |
| `skills_dir` / `skill_hook_url` / `agents` | `./skills` / none / `[default]` | `BRIDGEBOT_SKILLS_DIR`, `--skills-dir` |

Corporate proxy? `HTTPS_PROXY=http://proxy:3128` is honoured (websockets ≥ 15).

## Wire protocol (JSON over one WebSocket)

Connector → cloud: `HELLO`, `PING`, `TASK_STARTED`, `LOG`, `TASK_RESULT`, `SKILL_ACK`, `TUNNEL_RESPONSE`, bot-API frames.
Cloud → connector: `WELCOME {heartbeat_timeout_s}`, `PONG`, `TASK`, `CANCEL`, `SKILL_INJECT`, `TUNNEL_REQUEST`, `ERROR`.
Close codes: `4000` replaced by a newer session (same key twice), `4001` key rotated, `4002` machine deleted,
`4003`/`4403` account not active, `4401` invalid/missing key, `4008` heartbeat timeout.

## Security

See [`../SECURITY.md`](../SECURITY.md). Report vulnerabilities privately via GitHub Security Advisories.

## License

MIT — see [`../LICENSE`](../LICENSE).
