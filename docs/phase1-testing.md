# Phase 1 — connector resilience test campaign (live)

Target: **https://bridgebot-ai.onrender.com** (Render free, single uvicorn worker, Supabase Postgres).
Date: 2026-10-17, times Asia/Riyadh (UTC+3). Connector: `connector/local_connector.py` 1.2.x on Linux, Python 3, websockets 17.
Test accounts: throw-away `p1*_xxxxxx@example.com` users created through the public signup API.

## Method

| Tool | What it does |
|---|---|
| `netsim.py` | local HTTP CONNECT proxy the connector uses via `HTTPS_PROXY`; `/drop` = RST all links, `/switch` = freeze old links silently (Wi-Fi→hotspot / IP change: old path dead, new connects work), `/down` = refuse all, `/blackhole` = accept but forward nothing |
| SIGSTOP / SIGCONT | simulates laptop sleep: the process (and its socket) is frozen while the cloud keeps running |
| dashboard recorder | a browser-equivalent dashboard WebSocket that timestamps every LOG/STATUS event on receipt (`_rx`); latency = `_rx` − time the child printed the line |
| `lib.py` helpers | signup, connector create, start connector, poll status, tunnel GETs |

"Reconnect" = time from the network event until the dashboard shows the machine ONLINE again; "tunnel" = until a GET through the public tunnel URL returns 200 again.

## Summary

| # | Test | Result | Key numbers (after fixes) |
|---|---|---|---|
| 1a | Abrupt network drop (RST) ×5 | **PASS** | back ONLINE median 1.92 s (max 1.93 s), tunnel 200 at 2.9 s (max 3.1 s) |
| 1b | Silent network switch (old path freezes) ×3 | **PASS** | ONLINE 26.0 s, tunnel 26.8 s (dead-path detection = 10 s ping + 10 s timeout) |
| 1c | Internet down 30 s / 120 s (refused) → restored | **PASS** | ONLINE 30.2 s / 22.3 s after restore; during the outage cloud marks OFFLINE ≈20 s, task API 409, tunnel 502 |
| 1d | 120 s silent blackhole → restored | **PASS** | ONLINE 8.6 s after restore; cloud OFFLINE after ≈81 s |
| 1e | Drop / switch **during a running task** | **PASS** (was FAIL) | RST: 1.73 s, switch: 18.0 s; **40/40 log lines + TASK_RESULT exit 0** (before: 30/40 lines, result lost) |
| 2 | Sleep/wake (SIGSTOP 2, 5, 20 min) | **PASS** | OFFLINE seen at 90–92 s each; ONLINE 2.4–3.7 s after wake; tunnel 502 + task 409 while asleep, 200/ok after; connector logs "woke up after 1184s" |
| 3a | 13.5-min streaming task (1 line / 2 s) | **PASS** | 405/405 lines, exit 0, 810.1 s, one WS for the whole run, 0 dashboard drops, max inter-line gap 2.81 s, latency p50 84 ms / p95 99 ms |
| 3b | Timeout kill (timeout 120 s, child spawns grandchild) | **PASS** | exit −9 at 120.005 s, grandchild also dead (process-group kill) |
| 3c | Output caps: 200 k-line flood / 200 KB single line | **PASS** (was FAIL) | flood → 5000 lines + truncation notice; 200 KB line → truncated to 4018 chars, task exit 0 (before: connector crashed with "Separator is not found") |
| 4a | Wrong API key | **PASS** (was FAIL) | clear "❌ the cloud refused this connector: Invalid API key … (code 4401). Fix: …"; retries 60 s → 120 s (2 attempts in 150 s, before: 7 attempts, 1 s start); 0 tracebacks |
| 4b | Key rotated while connected | **PASS** (was FAIL) | connector logs code 4001 "API key rotated" + Regenerate-key hint, backs off 60 s |
| 4c | Server unreachable / DNS fail / internet down | **PASS** | exponential backoff 1 → 2 → 4 → 9 → 18 → ≈30 s cap with jitter (unreachable 7 attempts / 100 s, DNS 8 / 100 s, internet down 6 / 60 s), 0 tracebacks, clean exit on SIGTERM |
| 4d | API with connector offline / bad ids | **PASS** | task → offline 409 "Connector is OFFLINE"; unknown id 404; malformed id 422; no login 401; tunnel URL → 502 branded "Tunnel offline" page; no 500s |
| 5 | Log latency + line buffering | **PASS** | 1 line/s: p50 94 / p95 105 / max 152 ms (n=64); 20 lines/s burst: 97 / 104 / 192 ms (n=320); un-flushed `python print`: 98 / 105 / 551 ms (n=42) — child output arrives line by line |

Other live numbers (before → after): tunnel GET p50 2454 → 193 ms (p95 209 ms); connector handshake ≈4.6 s → ≈1 s; `/api/plans` 1.2 → 0.55 s; DB pool checkout 560 → 0.2 ms.

## Bugs found and fixed

| Bug (found live) | Fix | Commit |
|---|---|---|
| A single output line > 64 KB crashed the task reader (`ValueError: Separator is not found`) | read child output in chunks (`_iter_lines`), cap line length | 545132b |
| Timeout killed only the shell, grandchildren survived | `start_new_session` + `killpg`, cleanup in `finally` | 545132b |
| Python children block-buffered their output | `PYTHONUNBUFFERED=1` for children | 545132b |
| Events produced while offline were lost | outbox for TASK_STARTED / LOG / TASK_RESULT / SKILL_ACK | 545132b |
| After laptop sleep the connector sat on a dead socket | wall-clock gap detection → close and reconnect at once | 545132b |
| Wrong key: vague "HTTP 403", retry every ~1 s | server closes with 4401/4403; connector auth backoff 60 s ×2 (max 4×), clear fix hint | 545132b |
| Silent network switch mid-task lost lines and the result | at-least-once delivery: `seq` on reliable events, ACK in PONG, server-side de-dupe | 2295142 |
| Heartbeat DB writes blocked the WS receive loop (remote Postgres) | DB side effects on a per-connection worker, WELCOME first, heartbeat writes throttled to 60 s | 2295142 |
| Tunnel requests 2.4 s (3 DB queries per request) | 5 s lookup cache, background request logging | 2295142 |
| Dead path detection took ≈35 s | ping 10 s / timeout 10 s, backoff cap 30 s | 2295142 |
| Every request paid an extra DB round trip (pool pre-ping) | ping only connections idle > 30 s | c213f88 |
| Render's edge drops server-initiated WS close frames → connector saw 1006 and treated an auth refusal as a network blip | server sends a `GOODBYE {code, reason}` frame before every close (bad key, rotated, deleted, suspended, replaced, heartbeat); connector uses it | 23a9ebb |

Rerun on the final deploy (23a9ebb, 20:37–20:44): 4a–4d all re-verified live.

Regression suite after all fixes: `tests/e2e_test.py` 145 checks pass on SQLite and on three Postgres schemas (fresh / upgraded / empty); `test_master_bot` passes.

## Remaining limitations

- Silent dead-path detection (Wi-Fi switch with no RST) takes ≈18–26 s: 10 s ping + 10 s timeout + reconnect.
- The cloud marks a frozen or sleeping machine OFFLINE only after ≈90 s (`heartbeat_timeout_s`). Until then tasks are accepted and queued to the dead socket (delivered by the outbox / ACK path or failed on reconnect).
- After a long outage, reconnect can take up to the 30 s backoff cap.
- Render ↔ Supabase is ≈139 ms per round trip. Co-locating the DB region would cut first-request latency further.
- Single worker and in-memory connection hub: no horizontal scaling yet, and a deploy restarts all connections (connectors reconnect in ≈2–4 s).
- The outbox is bounded at 20 000 events (very long offline periods with heavy output can exceed it).
- Non-Python programs may still block-buffer stdout when not on a TTY; wrap them in `stdbuf -oL`.
- Render free instances sleep after inactivity; the first request after idle has a cold start (~30–60 s). Connected connectors keep the instance awake.
