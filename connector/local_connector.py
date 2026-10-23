#!/usr/bin/env python3
"""
BridgeBot AI — Local Connector Daemon
=====================================

Runs on the user's home/office PC next to their AI agents (n8n, Hermes,
LangChain, Ollama, ...). It opens an outbound, persistent WebSocket tunnel to
the BridgeBot cloud hub (no port-forwarding needed) and:

* authenticates with the connector API key (`Authorization: Bearer bbk_...`),
* keeps the tunnel alive with heartbeats + exponential-backoff reconnects,
* executes TASK commands from the dashboard (allowlisted, no shell, timeout)
  and streams stdout/stderr line-by-line back in real time,
* receives SKILL_INJECT payloads, saves them to `skills/<agent>/<skill>.json`,
  optionally forwards them to a local agent webhook, and ACKs the cloud.

Usage:
    pip install websockets
    python local_connector.py --server wss://your-app.onrender.com --api-key bbk_xxx
    # or put settings in connector_config.json / environment variables.

Environment overrides: BRIDGEBOT_SERVER_URL, BRIDGEBOT_API_KEY, BRIDGEBOT_MACHINE_NAME,
BRIDGEBOT_SKILLS_DIR, BRIDGEBOT_ALLOW_ANY_COMMAND (1/0), BRIDGEBOT_CONFIG,
BRIDGEBOT_AUTH_RETRY_S, BRIDGEBOT_NO_TUNNELS, BRIDGEBOT_TUNNEL_PORTS.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import http.client
import json
import logging
import os
import platform
import random
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import websockets

try:  # websockets >= 13: new asyncio implementation
    from websockets.asyncio.client import connect as ws_connect  # type: ignore

    _HEADER_KW = "additional_headers"
except ImportError:  # pragma: no cover — legacy websockets < 13
    from websockets import connect as ws_connect  # type: ignore

    _HEADER_KW = "extra_headers"

VERSION = "1.3.1"
IS_WINDOWS = os.name == "nt"
log = logging.getLogger("bridgebot.connector")

# Commands considered safe by default. Interpreters such as python/node/bash
# are deliberately NOT included because they allow arbitrary code; add them to
# `allowed_commands` in your config only if you accept that risk.
DEFAULT_ALLOWED: List[str] = [
    "echo", "ls", "dir", "pwd", "whoami", "hostname", "uname", "date", "uptime",
    "df", "free", "nvidia-smi", "ollama", "n8n", "git", "ping", "docker", "ps",
]
WINDOWS_BUILTINS = {"echo", "dir", "type", "ver"}
# Task/skill events survive a network drop: they are queued while offline and flushed after reconnect.
RELIABLE_TYPES = {"TASK_STARTED", "LOG", "TASK_RESULT", "SKILL_ACK"}
OUTBOX_MAX = 20000


# =============================================================================
# Configuration
# =============================================================================
@dataclass
class ConnectorConfig:
    """Runtime configuration (file < env < CLI precedence)."""

    server_url: str = "ws://localhost:8000"
    api_key: str = ""
    machine_name: str = field(default_factory=socket.gethostname)
    heartbeat_interval_s: float = 20.0
    reconnect_base_s: float = 1.0
    reconnect_max_s: float = 30.0       # cap: after an outage the link is back within ≤30 s of the network
    auth_retry_s: float = 60.0           # wait after "invalid API key" / "account suspended" (doubles, max 4x)
    ws_ping_interval_s: float = 10.0     # WebSocket ping; a dead link is detected within interval + timeout
    ws_ping_timeout_s: float = 10.0
    allowed_commands: List[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED))
    allow_any_command: bool = False
    max_concurrent_tasks: int = 4
    max_output_lines: int = 5000
    max_line_chars: int = 4000
    unbuffered_children: bool = True     # PYTHONUNBUFFERED=1 etc. so child output streams line-by-line
    default_timeout_s: int = 120
    work_dir: str = "."
    skills_dir: str = "./skills"
    # Optional: local HTTP endpoint (e.g. an n8n webhook) to POST injected skills to.
    skill_hook_url: Optional[str] = None
    agents: List[Dict[str, str]] = field(default_factory=lambda: [{"name": "default", "type": "generic"}])
    # HTTP tunnels: the cloud forwards https://<host>/t/{slug}/… to http://127.0.0.1:<port>/… on THIS machine.
    tunnels_enabled: bool = True
    allowed_tunnel_ports: Optional[List[int]] = None     # e.g. [5678, 8080]; None = any port you create a tunnel for
    tunnel_host: str = "127.0.0.1"                       # only loopback by default
    max_concurrent_tunnel_requests: int = 16
    # Remote desktop (opt-in): stream this screen to YOUR dashboard and accept mouse/keyboard/file actions from it.
    remote_desktop: bool = False
    display: Optional[str] = None                        # e.g. ":0"; default = $DISPLAY or ":0"
    rd_files_root: str = "~"                             # file browser is jailed to this folder

    @property
    def ws_url(self) -> str:
        """Normalise http(s)/ws(s) base URLs to the /ws/connect endpoint."""
        url = self.server_url.strip().rstrip("/")
        url = re.sub(r"^http://", "ws://", url)
        url = re.sub(r"^https://", "wss://", url)
        if not re.match(r"^wss?://", url):
            url = "wss://" + url
        if not url.endswith("/ws/connect"):
            url += "/ws/connect"
        return url


def load_config(argv: Optional[List[str]] = None) -> ConnectorConfig:
    """Build config from JSON file, environment and CLI flags."""
    p = argparse.ArgumentParser(description="BridgeBot AI local connector daemon")
    p.add_argument("--config", default=os.getenv("BRIDGEBOT_CONFIG", "connector_config.json"))
    p.add_argument("--server", help="Cloud URL, e.g. wss://bridgebot.onrender.com or http://localhost:8000")
    p.add_argument("--api-key")
    p.add_argument("--machine-name")
    p.add_argument("--skills-dir")
    p.add_argument("--allow-any-command", action="store_true", help="DANGEROUS: disable the command allowlist")
    p.add_argument("--no-tunnels", action="store_true", help="refuse HTTP tunnel requests on this machine")
    p.add_argument("--tunnel-ports", help="comma-separated local ports tunnels may reach, e.g. 5678,8080")
    p.add_argument("--remote-desktop", action="store_true", help="allow YOUR dashboard to view/control this screen (X11)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    cfg = ConnectorConfig()
    cfg_path = Path(args.config)
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
                else:
                    log.warning("unknown config key ignored: %s", k)
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Invalid config file {cfg_path}: {exc}")

    env_map = {
        "BRIDGEBOT_SERVER_URL": "server_url",
        "BRIDGEBOT_API_KEY": "api_key",
        "BRIDGEBOT_MACHINE_NAME": "machine_name",
        "BRIDGEBOT_SKILLS_DIR": "skills_dir",
    }
    for env, attr in env_map.items():
        if os.getenv(env):
            setattr(cfg, attr, os.environ[env])
    if os.getenv("BRIDGEBOT_AUTH_RETRY_S"):
        cfg.auth_retry_s = float(os.environ["BRIDGEBOT_AUTH_RETRY_S"])
    if os.getenv("BRIDGEBOT_ALLOW_ANY_COMMAND", "").lower() in ("1", "true", "yes"):
        cfg.allow_any_command = True

    if args.server:
        cfg.server_url = args.server
    if args.api_key:
        cfg.api_key = args.api_key
    if args.machine_name:
        cfg.machine_name = args.machine_name
    if args.skills_dir:
        cfg.skills_dir = args.skills_dir
    if args.allow_any_command:
        cfg.allow_any_command = True
    if args.no_tunnels or os.getenv("BRIDGEBOT_NO_TUNNELS", "").lower() in ("1", "true", "yes"):
        cfg.tunnels_enabled = False
    if args.remote_desktop or os.getenv("BRIDGEBOT_REMOTE_DESKTOP", "").lower() in ("1", "true", "yes"):
        cfg.remote_desktop = True
    ports = args.tunnel_ports or os.getenv("BRIDGEBOT_TUNNEL_PORTS")
    if ports:
        cfg.allowed_tunnel_ports = [int(x) for x in str(ports).split(",") if x.strip()]

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not cfg.api_key:
        raise SystemExit("No API key. Pass --api-key, set BRIDGEBOT_API_KEY, or add api_key to the config file.")
    return cfg


# =============================================================================
# Connector
# =============================================================================
class LocalConnector:
    """Owns the WebSocket tunnel and all task / skill handling."""

    def __init__(self, cfg: ConnectorConfig) -> None:
        self.cfg = cfg
        self.ws: Any = None
        self._send_lock = asyncio.Lock()
        self._task_slots = asyncio.Semaphore(cfg.max_concurrent_tasks)
        self._procs: Dict[str, asyncio.subprocess.Process] = {}
        self._jobs: set[asyncio.Task[None]] = set()
        self._stop = asyncio.Event()
        self._tunnel_slots = asyncio.Semaphore(cfg.max_concurrent_tunnel_requests)
        self._welcomed = False
        self._outbox: "collections.deque[Dict[str, Any]]" = collections.deque(maxlen=OUTBOX_MAX)
        self._flush_lock = asyncio.Lock()
        # At-least-once delivery for task/skill events: every reliable event gets a sequence number; events stay
        # in _unacked until the cloud confirms them (ack on PONG/ACK) and are re-sent after a reconnect, so even
        # data written into a silently-dead socket is not lost. The cloud de-duplicates by (stream, seq).
        self._stream = uuid.uuid4().hex
        self._seq = 0
        self._unacked: "collections.deque[Dict[str, Any]]" = collections.deque(maxlen=OUTBOX_MAX)
        self._acks = False          # enabled when the cloud's WELCOME says it supports acks
        self._server_heartbeat_timeout_s = 90.0
        self._rd: Optional["RemoteDesktop"] = None

    # ------------------------------------------------------------------ transport
    async def send(self, message: Dict[str, Any]) -> bool:
        """Send a message. Task/skill events are queued (bounded) while offline and delivered in order on reconnect."""
        if message.get("type") in RELIABLE_TYPES:
            self._seq += 1
            message["seq"] = self._seq
            self._outbox.append(message)
            return await self._flush()
        return await self._raw_send(message)

    async def _flush(self) -> bool:
        if self.ws is None:
            return False
        async with self._flush_lock:
            while self._outbox:
                msg = self._outbox[0]
                if not await self._raw_send(msg):
                    return False
                self._outbox.popleft()
                if self._acks:
                    self._unacked.append(msg)
        return True

    def _on_ack(self, ack: Any) -> None:
        if not isinstance(ack, int):
            return
        while self._unacked and int(self._unacked[0].get("seq", 0)) <= ack:
            self._unacked.popleft()

    def _requeue_unacked(self) -> int:
        """After a reconnect: everything not confirmed by the cloud goes back to the front of the outbox."""
        n = len(self._unacked)
        if n:
            pending = list(self._unacked) + list(self._outbox)
            self._unacked.clear()
            self._outbox.clear()
            self._outbox.extend(pending)
        return n

    async def _raw_send(self, message: Dict[str, Any]) -> bool:
        """Serialise and send; returns False if the socket is gone."""
        ws = self.ws
        if ws is None:
            return False
        try:
            async with self._send_lock:
                await ws.send(json.dumps(message, default=str))
            return True
        except websockets.ConnectionClosed:
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("send failed: %s", exc)
            return False

    async def run_forever(self) -> None:
        """Connect / reconnect loop with exponential backoff + jitter.

        * backoff only resets after the cloud sent WELCOME (a socket that is accepted then closed counts as a failure),
        * auth failures (invalid/rotated key, deleted connector, suspended account) are logged once with a fix hint
          and retried slowly (auth_retry_s, doubling up to 4x) instead of hammering the server,
        * "replaced by a newer session" (same key running twice) backs off 30 s+ so two copies don't ping-pong.
        """
        attempt = 0
        auth_failures = 0
        url = self.cfg.ws_url
        headers = {"Authorization": f"Bearer {self.cfg.api_key}", "User-Agent": f"bridgebot-connector/{VERSION}"}
        while not self._stop.is_set():
            kind, detail = "network", ""
            self._welcomed = False
            self._goodbye = None
            try:
                log.info("connecting to %s ...", url)
                async with ws_connect(url, **{_HEADER_KW: headers}, ping_interval=self.cfg.ws_ping_interval_s,
                                      ping_timeout=self.cfg.ws_ping_timeout_s, max_size=32 * 1024 * 1024,
                                      open_timeout=30, close_timeout=5) as ws:
                    self.ws = ws
                    await self._on_open()
                    heartbeat = asyncio.create_task(self._heartbeat_loop(ws))
                    try:
                        async for raw in ws:
                            await self._dispatch(raw)
                    except websockets.ConnectionClosed:
                        pass
                    finally:
                        heartbeat.cancel()
                        self.ws = None
                    code, reason = self._goodbye or (ws.close_code, ws.close_reason or "")
                    kind, detail = _classify_close(code, reason)
                    if self._welcomed:
                        attempt = 0
                        auth_failures = 0
                    log.warning("connection closed (code=%s reason=%s)", code, reason or "-")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — network errors, DNS, TLS, HTTP 403 from old servers, ...
                self.ws = None
                code = getattr(getattr(exc, "response", None), "status_code", None)
                if code in (401, 403):
                    kind, detail = "auth", f"HTTP {code}"
                else:
                    log.error("connection error: %s: %s", type(exc).__name__, exc)
            if self._stop.is_set():
                break
            if kind == "auth":
                auth_failures += 1
                delay = min(self.cfg.auth_retry_s * (2 ** (auth_failures - 1)), self.cfg.auth_retry_s * 4)
                hint = ("your BridgeBot account is not active — contact support (WhatsApp +966 56 700 9846)"
                        if "account" in detail.lower() else
                        "copy a fresh API key from Dashboard → Control Room (Regenerate key) into --api-key / BRIDGEBOT_API_KEY")
                log.error("❌ the cloud refused this connector: %s. Fix: %s. Retrying in %.0fs.", detail, hint, delay)
            else:
                auth_failures = 0
                delay = min(self.cfg.reconnect_max_s, self.cfg.reconnect_base_s * (2 ** attempt))
                delay = delay * (0.8 + random.random() * 0.4)
                if kind == "replaced":
                    delay = max(delay, 30.0)
                    log.warning("another connector process is using this API key (newest wins). Retrying in %.0fs.", delay)
                attempt = min(attempt + 1, 16)
                log.info("reconnecting in %.1fs (attempt %d)", delay, attempt)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        """Graceful shutdown: kill tasks, close socket."""
        self._stop.set()
        for proc in list(self._procs.values()):
            _kill(proc)
        if self.ws is not None:
            await self.ws.close()

    async def _on_open(self) -> None:
        await self._raw_send({
            "type": "HELLO",
            "machine": self.cfg.machine_name,
            "version": VERSION,
            "platform": f"{platform.system()} {platform.release()} / py{platform.python_version()}",
            "agents": self.cfg.agents,
            "allowed_commands": "*" if self.cfg.allow_any_command else self.cfg.allowed_commands,
            "features": ["tasks", "skills", "acks"] + (["tunnel"] if self.cfg.tunnels_enabled else [])
                        + (["remote_desktop"] if self.cfg.remote_desktop else []),
            "stream": self._stream,
        })
        async with self._flush_lock:
            resent = self._requeue_unacked()
        if resent:
            log.info("re-sending %d task events not yet confirmed by the cloud", resent)
        if self._outbox:
            log.info("delivering %d queued task events from while offline", len(self._outbox))
            await self._flush()

    async def _heartbeat_loop(self, ws: Any) -> None:
        """Application-level PING (updates last_heartbeat in the cloud) + sleep/wake detection.

        If the wall clock jumped (laptop slept, process was SIGSTOPped) for longer than half the cloud's heartbeat
        timeout, the socket is almost certainly stale: drop it now and reconnect instead of waiting for ping timeouts.
        """
        last = time.time()
        while True:
            await asyncio.sleep(self.cfg.heartbeat_interval_s)
            now = time.time()
            gap = now - last - self.cfg.heartbeat_interval_s
            last = now
            if gap > self._server_heartbeat_timeout_s / 2:
                log.warning("⏰ woke up after %.0fs of sleep/suspend — reconnecting", gap)
                await ws.close(code=1001, reason="wake from sleep")
                return
            if not await self.send({"type": "PING", "ts": now}):
                return

    # ------------------------------------------------------------------ dispatch
    async def _dispatch(self, raw: Union[str, bytes]) -> None:
        try:
            msg = json.loads(raw)
            mtype = str(msg.get("type", "")).upper()
        except (ValueError, AttributeError):
            log.warning("ignoring malformed message")
            return
        if mtype == "WELCOME":
            self._welcomed = True
            self._acks = bool(msg.get("acks"))
            if not self._acks:
                self._unacked.clear()
            try:
                self._server_heartbeat_timeout_s = float(msg.get("heartbeat_timeout_s") or 90)
            except (TypeError, ValueError):
                pass
            log.info("✅ authenticated as connector %s (%s)", msg.get("connector_id"), msg.get("machine_name"))
        elif mtype in ("PONG", "ACK"):
            self._on_ack(msg.get("ack"))
        elif mtype == "TASK":
            self._spawn(self._handle_task(msg))
        elif mtype == "CANCEL":
            proc = self._procs.get(str(msg.get("task_id")))
            if proc:
                _kill(proc)
        elif mtype == "SKILL_INJECT":
            self._spawn(self._handle_skill(msg))
        elif mtype == "TUNNEL_REQUEST":
            self._spawn(self._handle_tunnel(msg))
        elif mtype.startswith("RD_"):
            if self._rd is None:
                self._rd = RemoteDesktop(self)
            self._spawn(self._rd.handle(mtype, msg))
        elif mtype == "GOODBYE":   # the cloud is closing us on purpose; remember why (close frames can get lost)
            self._goodbye = (int(msg.get("code") or 0), str(msg.get("reason") or ""))
            if self.ws is not None:
                await self.ws.close()
        elif mtype == "ERROR":
            log.error("cloud error: %s", msg.get("error"))
        else:
            log.debug("unhandled message type %s", mtype)

    def _spawn(self, coro: Any) -> None:
        """Run a handler without blocking the receive loop; keep a ref to avoid GC."""
        job = asyncio.create_task(coro)
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)

    # ------------------------------------------------------------------ tasks
    def _build_argv(self, command: Union[str, List[str]]) -> List[str]:
        """Parse + allowlist-check a command. Raises PermissionError / ValueError."""
        if isinstance(command, list):
            argv = [str(a) for a in command]
        elif isinstance(command, str):
            argv = shlex.split(command, posix=not IS_WINDOWS)
        else:
            raise ValueError("command must be a string or list")
        if not argv:
            raise ValueError("empty command")
        exe = Path(argv[0]).name.lower()
        if exe.endswith(".exe"):
            exe = exe[:-4]
        if not self.cfg.allow_any_command and exe not in {c.lower() for c in self.cfg.allowed_commands}:
            raise PermissionError(f"'{exe}' is not in allowed_commands {self.cfg.allowed_commands}")
        if IS_WINDOWS and exe in WINDOWS_BUILTINS:
            argv = ["cmd", "/c", *argv]
        return argv

    async def _handle_task(self, msg: Dict[str, Any]) -> None:
        task_id = str(msg.get("task_id") or "unknown")
        timeout = int(msg.get("timeout_s") or self.cfg.default_timeout_s)
        started = time.monotonic()
        try:
            argv = self._build_argv(msg.get("command", ""))
        except (PermissionError, ValueError) as exc:
            log.warning("task %s rejected: %s", task_id, exc)
            await self.send({"type": "LOG", "task_id": task_id, "stream": "stderr", "line": f"[connector] rejected: {exc}", "ts": time.time()})
            await self.send({"type": "TASK_RESULT", "task_id": task_id, "exit_code": -1, "duration_ms": 0, "error": str(exc)})
            return

        cwd = msg.get("cwd") or self.cfg.work_dir
        async with self._task_slots:
            log.info("task %s: %s", task_id, argv)
            await self.send({"type": "TASK_STARTED", "task_id": task_id, "argv": argv, "ts": time.time()})
            tail: List[str] = []
            exit_code: Optional[int] = None
            error: Optional[str] = None
            try:
                env = dict(os.environ)
                if self.cfg.unbuffered_children:
                    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING=env.get("PYTHONIOENCODING", "utf-8"))
                proc = await asyncio.create_subprocess_exec(
                    *argv, cwd=cwd, stdin=asyncio.subprocess.DEVNULL, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    **({} if IS_WINDOWS else {"start_new_session": True}),  # own process group → kill children too
                )
                self._procs[task_id] = proc
                counter = {"n": 0}
                streams = asyncio.gather(
                    self._pump(task_id, "stdout", proc.stdout, counter, tail),
                    self._pump(task_id, "stderr", proc.stderr, counter, tail),
                )
                try:
                    await asyncio.wait_for(streams, timeout=timeout)
                    exit_code = await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    _kill(proc)
                    error = f"timed out after {timeout}s"
                    exit_code = await proc.wait()
            except FileNotFoundError:
                error = f"executable not found: {argv[0]}"
                exit_code = 127
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                exit_code = -1
            finally:
                p_ = self._procs.pop(task_id, None)
                if p_ is not None and p_.returncode is None:   # never leave an orphan running
                    _kill(p_)
                    try:
                        await asyncio.wait_for(p_.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
            if error:
                await self.send({"type": "LOG", "task_id": task_id, "stream": "stderr", "line": f"[connector] {error}", "ts": time.time()})
            await self.send({
                "type": "TASK_RESULT", "task_id": task_id, "exit_code": exit_code,
                "duration_ms": int((time.monotonic() - started) * 1000), "error": error,
                "stdout_tail": tail[-20:],
            })
            log.info("task %s finished exit=%s", task_id, exit_code)

    async def _pump(self, task_id: str, stream: str, reader: Optional[asyncio.StreamReader],
                    counter: Dict[str, int], tail: List[str]) -> None:
        """Forward each output line as a LOG event, with caps on volume."""
        if reader is None:
            return
        async for line_b in _iter_lines(reader, self.cfg.max_line_chars * 4):
            counter["n"] += 1
            if counter["n"] > self.cfg.max_output_lines:
                if counter["n"] == self.cfg.max_output_lines + 1:
                    await self.send({"type": "LOG", "task_id": task_id, "stream": "stderr", "ts": time.time(),
                                     "line": f"[connector] output truncated: more than {self.cfg.max_output_lines} lines "
                                             "(max_output_lines); the task keeps running, further lines are dropped"})
                continue
            line = line_b.decode("utf-8", errors="replace").rstrip("\r\n")
            if len(line) > self.cfg.max_line_chars:
                line = line[: self.cfg.max_line_chars] + " …[line truncated]"
            if stream == "stdout":
                tail.append(line)
                del tail[:-50]
            await self.send({"type": "LOG", "task_id": task_id, "stream": stream, "line": line, "ts": time.time()})

    # ------------------------------------------------------------------ HTTP tunnel
    async def _handle_tunnel(self, msg: Dict[str, Any]) -> None:
        """Proxy one HTTP request from the cloud to a local port and send TUNNEL_RESPONSE."""
        rid = msg.get("request_id")
        resp: Dict[str, Any] = {"type": "TUNNEL_RESPONSE", "request_id": rid}
        async with self._tunnel_slots:
            try:
                if not self.cfg.tunnels_enabled:
                    raise PermissionError("tunnels are disabled on this machine (--no-tunnels)")
                port = int(msg.get("port", 0))
                if not 1 <= port <= 65535:
                    raise ValueError("invalid port")
                if self.cfg.allowed_tunnel_ports and port not in self.cfg.allowed_tunnel_ports:
                    raise PermissionError(f"port {port} is not in allowed_tunnel_ports {self.cfg.allowed_tunnel_ports}")
                path = str(msg.get("path") or "/")
                if not path.startswith("/") or path.startswith("//") or any(c in path for c in "\r\n "):
                    raise ValueError("invalid path")
                method = str(msg.get("method") or "GET").upper()
                if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                    raise ValueError(f"method {method} not allowed")
                body = base64.b64decode(msg.get("body_b64") or "")
                status, headers, data = await asyncio.to_thread(
                    self._local_http, port, method, path, msg.get("headers") or [], body,
                    float(msg.get("timeout_s") or 30), int(msg.get("max_response_bytes") or 8 * 1024 * 1024))
                resp.update(status=status, headers=headers, body_b64=base64.b64encode(data).decode())
                log.info("🌐 tunnel %s %s → :%d %s (%d bytes)", method, path[:80], port, status, len(data))
            except PermissionError as exc:
                resp.update(status=403, error=str(exc))
            except ConnectionRefusedError:
                resp.update(status=502, error=f"nothing is listening on {self.cfg.tunnel_host}:{msg.get('port')}")
            except (TimeoutError, socket.timeout):
                resp.update(status=504, error="local service timed out")
            except OverflowError as exc:
                resp.update(status=502, error=str(exc))
            except Exception as exc:  # noqa: BLE001
                resp.update(status=502, error=f"{type(exc).__name__}: {exc}")
        await self.send(resp)

    def _local_http(self, port: int, method: str, path: str, headers: List[List[str]], body: bytes,
                    timeout: float, max_bytes: int) -> "tuple[int, List[List[str]], bytes]":
        conn = http.client.HTTPConnection(self.cfg.tunnel_host, port, timeout=timeout)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"localhost:{port}")
            for pair in headers:
                k, v = str(pair[0]), str(pair[1])
                if k.lower() in ("host", "content-length", "connection", "transfer-encoding"):
                    continue
                conn.putheader(k, v)
            if body or method in ("POST", "PUT", "PATCH"):
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body or None)
            r = conn.getresponse()
            data = r.read(max_bytes + 1) if method != "HEAD" else b""
            if len(data) > max_bytes:
                raise OverflowError(f"local response exceeds {max_bytes // (1024 * 1024)} MB tunnel limit")
            return r.status, [[k, v] for k, v in r.getheaders()], data
        finally:
            conn.close()

    # ------------------------------------------------------------------ skills
    async def _handle_skill(self, msg: Dict[str, Any]) -> None:
        request_id = msg.get("request_id")
        agent = _safe_name(str(msg.get("agent_name") or "default"))
        payload = msg.get("payload")
        ack: Dict[str, Any] = {"type": "SKILL_ACK", "request_id": request_id, "agent_name": agent, "ok": False}
        try:
            if not isinstance(payload, dict) or not payload.get("skill_id"):
                raise ValueError("payload must be an object with a skill_id")
            skill_id = _safe_name(str(payload["skill_id"]))
            path = await asyncio.to_thread(self._write_skill, agent, skill_id, payload)
            ack.update(ok=True, skill_id=skill_id, path=str(path))
            log.info("🧠 skill %s injected into agent '%s' -> %s", skill_id, agent, path)
            if self.cfg.skill_hook_url:
                ack["hook_status"] = await asyncio.to_thread(_post_json, self.cfg.skill_hook_url, {"agent": agent, "skill": payload})
        except Exception as exc:  # noqa: BLE001
            ack["error"] = f"{type(exc).__name__}: {exc}"
            log.error("skill inject failed: %s", exc)
        await self.send(ack)

    def _write_skill(self, agent: str, skill_id: str, payload: Dict[str, Any]) -> Path:
        """Atomically write the skill file and update the agent's manifest."""
        agent_dir = Path(self.cfg.skills_dir).expanduser().resolve() / agent
        agent_dir.mkdir(parents=True, exist_ok=True)
        target = agent_dir / f"{skill_id}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
        manifest_path = agent_dir / "active_skills.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"skills": []}
        except json.JSONDecodeError:
            manifest = {"skills": []}
        manifest["skills"] = [s for s in manifest.get("skills", []) if s.get("skill_id") != skill_id]
        manifest["skills"].append({"skill_id": skill_id, "name": payload.get("name"), "file": target.name, "installed_at": time.time()})
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return target


# =============================================================================
# Helpers
# =============================================================================
def _safe_name(name: str) -> str:
    """Filesystem-safe identifier (prevents path traversal)."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("._")
    return cleaned[:100] or "default"


def _kill(proc: asyncio.subprocess.Process) -> None:
    """Kill a task and everything it spawned (its process group on POSIX)."""
    try:
        if proc.returncode is None:
            if not IS_WINDOWS:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                    return
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            proc.kill()
    except ProcessLookupError:
        pass


async def _iter_lines(reader: asyncio.StreamReader, max_line_bytes: int):  # type: ignore[no-untyped-def]
    """Yield lines as soon as they arrive. Unlike readline() this never fails on very long lines: a line longer
    than max_line_bytes is yielded in pieces (the caller truncates it for display)."""
    buf = b""
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            if buf:
                yield buf
            return
        buf += chunk
        while True:
            i = buf.find(b"\n")
            if i < 0:
                break
            yield buf[: i + 1]
            buf = buf[i + 1:]
        if len(buf) > max_line_bytes:
            yield buf[:max_line_bytes]
            buf = b""
            # skip the remainder of this overlong line
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    return
                i = chunk.find(b"\n")
                if i >= 0:
                    buf = chunk[i + 1:]
                    break
            while b"\n" in buf:
                i = buf.find(b"\n")
                yield buf[: i + 1]
                buf = buf[i + 1:]


def _classify_close(code: Optional[int], reason: str) -> "tuple[str, str]":
    """Map a WebSocket close code from the cloud to a reconnect policy."""
    if code in (4001, 4002, 4003, 4401, 4403, 1008):
        return "auth", f"{reason or 'unauthorized'} (code {code})"
    if code == 4000:
        return "replaced", reason
    return "network", reason


def _post_json(url: str, data: Dict[str, Any]) -> int:
    """POST JSON to a local agent webhook (stdlib only). Returns HTTP status or -1."""
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — user-configured local URL
            return int(resp.status)
    except Exception as exc:  # noqa: BLE001
        log.warning("skill hook failed: %s", exc)
        return -1



# =============================================================================
# Remote desktop (opt-in with --remote-desktop). Only the machine owner's dashboard
# can reach this. Kill switch file: ~/.bridgebot/remote_desktop_off (any OS).
# Capture: mss. Input: xdotool on Linux (pynput fallback), pynput on Win/Mac.
# Clipboard: pyperclip (xclip fallback on Linux). Apps + open: per-OS.
# =============================================================================
RD_KILL_FILE = Path.home() / ".bridgebot" / "remote_desktop_off"
RD_MAX_FILE = 12 * 1024 * 1024
_SYS = platform.system()  # "Linux" | "Darwin" | "Windows"


def _rd_apps() -> Dict[str, List[List[str]]]:
    if _SYS == "Windows":
        return {
            "chrome": [["cmd", "/c", "start", "", "chrome"], ["cmd", "/c", "start", "", "msedge"]],
            "firefox": [["cmd", "/c", "start", "", "firefox"]],
            "files": [["explorer", str(Path.home())]],
            "terminal": [["wt"], ["powershell"], ["cmd"]],
            "editor": [["notepad"]],
        }
    if _SYS == "Darwin":
        return {
            "chrome": [["open", "-a", "Google Chrome"], ["open", "-a", "Chromium"]],
            "firefox": [["open", "-a", "Firefox"]],
            "files": [["open", str(Path.home())]],
            "terminal": [["open", "-a", "Terminal"], ["open", "-a", "iTerm"]],
            "editor": [["open", "-a", "TextEdit"], ["open", "-a", "Visual Studio Code"]],
        }
    return {
        "chrome": [["google-chrome"], ["chromium"], ["chromium-browser"]],
        "firefox": [["firefox-esr"], ["firefox"]],
        "files": [["thunar"], ["nautilus"], ["pcmanfm"], ["dolphin"], ["xdg-open", str(Path.home())]],
        "terminal": [["qterminal"], ["xfce4-terminal"], ["gnome-terminal"], ["konsole"], ["x-terminal-emulator"], ["xterm"]],
        "editor": [["mousepad"], ["gedit"], ["kate"], ["code"]],
    }


class RemoteDesktop:
    def __init__(self, conn: "LocalConnector") -> None:
        self.c = conn
        self.env = dict(os.environ)
        if _SYS == "Linux":
            self.env["DISPLAY"] = conn.cfg.display or os.environ.get("DISPLAY") or ":0"
            if "XAUTHORITY" not in self.env and (Path.home() / ".Xauthority").exists():
                self.env["XAUTHORITY"] = str(Path.home() / ".Xauthority")
            os.environ.setdefault("DISPLAY", self.env["DISPLAY"])
            if "XAUTHORITY" in self.env:
                os.environ.setdefault("XAUTHORITY", self.env["XAUTHORITY"])
        self.viewers: Dict[str, Dict[str, Any]] = {}
        self.loop_task: Optional[asyncio.Task] = None
        self.size = (0, 0)
        self.root = Path(os.path.expanduser(conn.cfg.rd_files_root)).resolve()
        self._input_backend: Optional[str] = None   # "xdotool" | "pynput"
        self._mouse = self._kb = None

    async def reply(self, viewer: str, msg: Dict[str, Any]) -> None:
        msg["viewer"] = viewer
        await self.c._raw_send(msg)

    async def handle(self, mtype: str, msg: Dict[str, Any]) -> None:
        viewer = str(msg.get("viewer") or "")
        if not viewer:
            return
        if mtype == "RD_STOP":
            self.viewers.pop(viewer, None)
            return
        if not self.c.cfg.remote_desktop:
            await self.reply(viewer, {"type": "RD_ERROR", "error": "Remote desktop is off. Restart the connector with --remote-desktop."})
            return
        if RD_KILL_FILE.exists():
            self.viewers.clear()
            await self.reply(viewer, {"type": "RD_ERROR", "error": f"Remote desktop blocked (kill switch {RD_KILL_FILE})."})
            return
        try:
            if mtype == "RD_START":
                v = self.viewers.setdefault(viewer, {"inflight": 0})
                v.update(q=int(min(90, max(10, msg.get("quality", 55)))),
                         w=int(min(2560, max(320, msg.get("width", 1280)))),
                         fps=float(min(15, max(0.5, msg.get("fps", 6)))), last=time.time())
                v["inflight"] = 0
                log.warning("🖥️  remote desktop viewer connected (%s) on %s", viewer[:8], _SYS)
                self._notify("BridgeBot: remote desktop session started")
                if self.loop_task is None or self.loop_task.done():
                    self.loop_task = asyncio.create_task(self._frames())
            elif mtype == "RD_ACK":
                v = self.viewers.get(viewer)
                if v:
                    v["inflight"] = max(0, v["inflight"] - 1)
                    v["last"] = time.time()
            elif mtype == "RD_INPUT":
                await asyncio.to_thread(self._input_sync, msg)
                if viewer in self.viewers:
                    self.viewers[viewer]["last"] = time.time()
            elif mtype == "RD_CLIP_GET":
                await self.reply(viewer, {"type": "RD_CLIP", "text": (await asyncio.to_thread(self._clip_get))[:100000]})
            elif mtype == "RD_CLIP_SET":
                await asyncio.to_thread(self._clip_set, str(msg.get("text", ""))[:100000])
                await self.reply(viewer, {"type": "RD_OK", "what": "clipboard"})
            elif mtype == "RD_LAUNCH":
                await self.reply(viewer, {"type": "RD_OK", "what": "launch", "detail": self._launch(str(msg.get("app", "")), msg.get("url"))})
            elif mtype == "RD_LS":
                await self.reply(viewer, {"type": "RD_LS", **self._ls(str(msg.get("path", "")))})
            elif mtype == "RD_GET":
                await self.reply(viewer, {"type": "RD_FILE", **self._get(str(msg.get("path", "")))})
            elif mtype == "RD_PUT":
                await self.reply(viewer, {"type": "RD_OK", "what": "upload", "detail": self._put(str(msg.get("path", "")), str(msg.get("data", "")))})
            elif mtype == "RD_OPEN":
                await self.reply(viewer, {"type": "RD_OK", "what": "open", "detail": self._open_path(self._safe(str(msg.get("path", ""))))})
        except Exception as exc:  # noqa: BLE001
            await self.reply(viewer, {"type": "RD_ERROR", "error": f"{mtype}: {exc}"})

    def _notify(self, text: str) -> None:
        try:
            if _SYS == "Darwin":
                subprocess.Popen(["osascript", "-e", f'display notification "{text.replace(chr(34), "")}" with title "BridgeBot"'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif _SYS == "Windows":
                subprocess.Popen(["powershell", "-NoProfile", "-Command",
                                  f"[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]> $null; Write-Host '{text}'"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["notify-send", "-a", "BridgeBot", text], env=self.env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def _grab(self, width: int, quality: int) -> Tuple[bytes, int, int]:
        import io
        try:
            import mss  # type: ignore
            from PIL import Image  # type: ignore
        except ImportError as exc:
            raise RuntimeError("missing packages: pip install mss pillow") from exc
        if not hasattr(self, "_mss"):
            self._mss = getattr(mss, "MSS", mss.mss)()
        mon = self._mss.monitors[0]
        shot = self._mss.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        self.size = shot.size
        if img.width > width:
            img = img.resize((width, int(img.height * width / img.width)), Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=False)
        return buf.getvalue(), shot.size[0], shot.size[1]

    async def _frames(self) -> None:
        last_hash: Dict[str, Any] = {}
        tip = ("On Windows/Mac install mss pillow. On Linux X11 also need xdotool (or pynput). "
               "macOS: allow Screen Recording for Terminal/Python in System Settings → Privacy.")
        while self.viewers:
            now = time.time()
            for vid, v in list(self.viewers.items()):
                if now - v.get("last", now) > 30:
                    self.viewers.pop(vid, None)
            ready = [(vid, v) for vid, v in self.viewers.items() if v["inflight"] < 2]
            if not ready:
                await asyncio.sleep(0.03)
                continue
            fps = max(v["fps"] for _, v in ready)
            t0 = time.time()
            for vid, v in ready:
                try:
                    jpg, sw, sh = await asyncio.to_thread(self._grab, v["w"], v["q"])
                except Exception as exc:  # noqa: BLE001
                    await self.reply(vid, {"type": "RD_ERROR", "error": f"screen capture failed: {exc}. {tip}"})
                    self.viewers.pop(vid, None)
                    continue
                h = hash(jpg)
                if last_hash.get(vid) == h and now - v.get("sent", 0) < 2:
                    continue
                last_hash[vid] = h
                v["inflight"] += 1
                v["sent"] = time.time()
                await self.reply(vid, {"type": "RD_FRAME", "jpg": base64.b64encode(jpg).decode(), "sw": sw, "sh": sh})
            await asyncio.sleep(max(0.0, 1.0 / fps - (time.time() - t0)))
        log.warning("🖥️  remote desktop: no viewers, streaming stopped")

    def _ensure_pynput(self) -> None:
        if self._mouse is not None:
            return
        try:
            from pynput.mouse import Controller as MC, Button  # type: ignore
            from pynput.keyboard import Controller as KC, Key  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pip install pynput  (macOS: also grant Accessibility to Terminal/Python)") from exc
        self._mouse, self._kb = MC(), KC()
        self._Button, self._Key = Button, Key

    def _backend(self) -> str:
        if self._input_backend:
            return self._input_backend
        if _SYS == "Linux" and shutil.which("xdotool"):
            self._input_backend = "xdotool"
        else:
            self._ensure_pynput()
            self._input_backend = "pynput"
        return self._input_backend

    def _xy(self, m: Dict[str, Any]) -> Tuple[int, int]:
        sw, sh = self.size if self.size[0] else (1920, 1080)
        x = int(float(m.get("x", 0)) * sw)
        y = int(float(m.get("y", 0)) * sh)
        return max(0, min(sw - 1, x)), max(0, min(sh - 1, y))

    def _run_sync(self, argv: List[str], capture: bool = False, stdin: Optional[str] = None) -> Optional[str]:
        r = subprocess.run(argv, env=self.env, input=stdin.encode() if stdin is not None else None,
                           capture_output=True, timeout=2)
        if r.returncode and not capture:
            raise RuntimeError((r.stderr or b"").decode(errors="replace").strip()[:300] or f"{argv[0]} failed")
        return r.stdout.decode(errors="replace") if capture and r.stdout else None

    def _input_sync(self, m: Dict[str, Any]) -> None:
        if self._backend() == "xdotool":
            self._input_xdotool(m)
        else:
            self._input_pynput(m)

    def _input_xdotool(self, m: Dict[str, Any]) -> None:
        act = str(m.get("action", ""))
        x, y = self._xy(m)
        btn = str(int(m.get("button", 1))) if str(m.get("button", 1)).isdigit() and int(m.get("button", 1)) in (1, 2, 3) else "1"
        if act == "move":
            self._run_sync(["xdotool", "mousemove", str(x), str(y)])
        elif act == "click":
            self._run_sync(["xdotool", "mousemove", str(x), str(y), "click", "--repeat",
                            str(min(3, max(1, int(m.get("count", 1))))), btn])
        elif act == "down":
            self._run_sync(["xdotool", "mousemove", str(x), str(y), "mousedown", btn])
        elif act == "up":
            self._run_sync(["xdotool", "mousemove", str(x), str(y), "mouseup", btn])
        elif act == "scroll":
            dy, dx = int(m.get("dy", 0)), int(m.get("dx", 0))
            args = ["xdotool", "mousemove", str(x), str(y)] if "x" in m else ["xdotool"]
            if dy:
                args += ["click", "--repeat", str(min(20, abs(dy))), "5" if dy > 0 else "4"]
            if dx:
                args += ["click", "--repeat", str(min(20, abs(dx))), "7" if dx > 0 else "6"]
            if len(args) > 1:
                self._run_sync(args)
        elif act == "key":
            keys = str(m.get("keys", ""))
            if not re.fullmatch(r"[A-Za-z0-9_+ ]{1,80}", keys):
                raise ValueError("bad key name")
            self._run_sync(["xdotool", "key", "--clearmodifiers", *keys.split()])
        elif act == "type":
            self._run_sync(["xdotool", "type", "--delay", "8", "--", str(m.get("text", ""))[:2000]])
        else:
            raise ValueError(f"unknown input action {act!r}")

    def _pynput_btn(self, button: Any):
        b = int(button) if str(button).isdigit() else 1
        return {1: self._Button.left, 2: self._Button.middle, 3: self._Button.right}.get(b, self._Button.left)

    def _pynput_keys(self, keys: str) -> list:
        Key = self._Key
        alias = {
            "return": Key.enter, "enter": Key.enter, "backspace": Key.backspace, "escape": Key.esc, "esc": Key.esc,
            "tab": Key.tab, "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
            "delete": Key.delete, "home": Key.home, "end": Key.end, "prior": Key.page_up, "next": Key.page_down,
            "space": Key.space, "super": Key.cmd if _SYS == "Darwin" else Key.cmd_l,
            "ctrl": Key.ctrl, "alt": Key.alt, "shift": Key.shift, "cmd": Key.cmd,
            "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4, "f5": Key.f5, "f6": Key.f6,
            "f7": Key.f7, "f8": Key.f8, "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
        }
        out = []
        for part in keys.replace(" ", "+").split("+"):
            p = part.strip().lower()
            if not p:
                continue
            if p in alias:
                out.append(alias[p])
            elif len(p) == 1:
                out.append(p)
            else:
                raise ValueError(f"unsupported key {part!r}")
        return out

    def _input_pynput(self, m: Dict[str, Any]) -> None:
        self._ensure_pynput()
        act = str(m.get("action", ""))
        x, y = self._xy(m)
        if act == "move":
            self._mouse.position = (x, y)
        elif act == "click":
            self._mouse.position = (x, y)
            self._mouse.click(self._pynput_btn(m.get("button", 1)), min(3, max(1, int(m.get("count", 1)))))
        elif act == "down":
            self._mouse.position = (x, y)
            self._mouse.press(self._pynput_btn(m.get("button", 1)))
        elif act == "up":
            self._mouse.position = (x, y)
            self._mouse.release(self._pynput_btn(m.get("button", 1)))
        elif act == "scroll":
            if "x" in m:
                self._mouse.position = (x, y)
            self._mouse.scroll(int(m.get("dx", 0)), -int(m.get("dy", 0)))  # pynput: +dy = up
        elif act == "key":
            keys = str(m.get("keys", ""))
            if not re.fullmatch(r"[A-Za-z0-9_+ ]{1,80}", keys):
                raise ValueError("bad key name")
            parts = self._pynput_keys(keys)
            mods, finals = parts[:-1], parts[-1:]
            for k in mods:
                self._kb.press(k)
            try:
                for k in finals:
                    self._kb.press(k)
                    self._kb.release(k)
            finally:
                for k in reversed(mods):
                    self._kb.release(k)
        elif act == "type":
            self._kb.type(str(m.get("text", ""))[:2000])
        else:
            raise ValueError(f"unknown input action {act!r}")

    def _clip_get(self) -> str:
        if _SYS == "Darwin":
            return self._run_sync(["pbpaste"], capture=True) or ""
        if _SYS == "Linux" and shutil.which("xclip"):
            try:
                return self._run_sync(["xclip", "-selection", "clipboard", "-o"], capture=True) or ""
            except Exception:
                pass
        try:
            import pyperclip  # type: ignore
            return pyperclip.paste() or ""
        except Exception as exc:
            raise RuntimeError("clipboard read failed (pip install pyperclip; on Linux also: apt install xclip)") from exc

    def _clip_set(self, text: str) -> None:
        if _SYS == "Darwin":
            self._run_sync(["pbcopy"], stdin=text)
            return
        if _SYS == "Linux" and shutil.which("xclip"):
            try:
                # -loops 1 returns after one paste request; avoids hanging on headless X
                self._run_sync(["xclip", "-selection", "clipboard", "-loops", "1"], stdin=text)
                return
            except Exception:
                pass
        try:
            import pyperclip  # type: ignore
            pyperclip.copy(text)
        except Exception as exc:
            raise RuntimeError("clipboard write failed (pip install pyperclip; on Linux also: apt install xclip)") from exc

    def _launch(self, app: str, url: Any = None) -> str:
        cands = _rd_apps().get(app)
        if not cands:
            raise ValueError(f"unknown app {app!r}")
        for argv in cands:
            if argv[0] not in ("cmd", "open", "explorer") and not shutil.which(argv[0]):
                continue
            cmd = list(argv)
            if url and app in ("chrome", "firefox") and re.match(r"^https?://", str(url)):
                cmd.append(str(url))
            try:
                kwargs: Dict[str, Any] = {"env": self.env, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
                if _SYS != "Windows":
                    kwargs["start_new_session"] = True
                subprocess.Popen(cmd, **kwargs)
                return " ".join(cmd[:3])
            except OSError:
                continue
        raise RuntimeError(f"no {app} program installed")

    def _open_path(self, p: Path) -> str:
        if _SYS == "Windows":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif _SYS == "Darwin":
            subprocess.Popen(["open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(p)], env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        return p.name

    def _safe(self, rel: str) -> Path:
        p = (self.root / rel.lstrip("/\\")).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError("outside the allowed folder")
        return p

    def _ls(self, rel: str) -> Dict[str, Any]:
        p = self._safe(rel)
        items = []
        for e in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))[:500]:
            try:
                st = e.stat()
                items.append({"name": e.name, "dir": e.is_dir(), "size": st.st_size, "mtime": int(st.st_mtime)})
            except OSError:
                continue
        return {"path": str(p.relative_to(self.root)).replace("\\", "/") if p != self.root else "", "items": items}

    def _get(self, rel: str) -> Dict[str, Any]:
        p = self._safe(rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        if p.stat().st_size > RD_MAX_FILE:
            raise ValueError("file larger than 12 MB")
        return {"name": p.name, "data": base64.b64encode(p.read_bytes()).decode()}

    def _put(self, rel: str, data: str) -> str:
        p = self._safe(rel)
        raw = base64.b64decode(data)
        if len(raw) > RD_MAX_FILE:
            raise ValueError("file larger than 12 MB")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return p.name



async def _main() -> None:
    cfg = load_config()
    connector = LocalConnector(cfg)
    loop = asyncio.get_running_loop()
    if not IS_WINDOWS:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(connector.stop()))
    log.info("BridgeBot connector v%s — machine '%s' — allowlist %s", VERSION, cfg.machine_name,
             "DISABLED (any command)" if cfg.allow_any_command else cfg.allowed_commands)
    await connector.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(0)
