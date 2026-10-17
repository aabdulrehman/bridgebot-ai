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
BRIDGEBOT_SKILLS_DIR, BRIDGEBOT_ALLOW_ANY_COMMAND (1/0), BRIDGEBOT_CONFIG.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import http.client
import json
import logging
import os
import platform
import random
import re
import shlex
import signal
import socket
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import websockets

try:  # websockets >= 13: new asyncio implementation
    from websockets.asyncio.client import connect as ws_connect  # type: ignore

    _HEADER_KW = "additional_headers"
except ImportError:  # pragma: no cover — legacy websockets < 13
    from websockets import connect as ws_connect  # type: ignore

    _HEADER_KW = "extra_headers"

VERSION = "1.1.0"
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
    reconnect_max_s: float = 60.0
    allowed_commands: List[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED))
    allow_any_command: bool = False
    max_concurrent_tasks: int = 4
    max_output_lines: int = 5000
    max_line_chars: int = 4000
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

    # ------------------------------------------------------------------ transport
    async def send(self, message: Dict[str, Any]) -> bool:
        """Serialise and send; returns False if the socket is gone."""
        if self.ws is None:
            return False
        try:
            async with self._send_lock:
                await self.ws.send(json.dumps(message, default=str))
            return True
        except websockets.ConnectionClosed:
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("send failed: %s", exc)
            return False

    async def run_forever(self) -> None:
        """Connect / reconnect loop with exponential backoff + jitter."""
        attempt = 0
        url = self.cfg.ws_url
        headers = {"Authorization": f"Bearer {self.cfg.api_key}", "User-Agent": f"bridgebot-connector/{VERSION}"}
        while not self._stop.is_set():
            try:
                log.info("connecting to %s ...", url)
                async with ws_connect(url, **{_HEADER_KW: headers}, ping_interval=20, ping_timeout=20,
                                      max_size=32 * 1024 * 1024, open_timeout=30) as ws:
                    self.ws = ws
                    attempt = 0  # reset backoff after a successful handshake
                    await self._on_open()
                    heartbeat = asyncio.create_task(self._heartbeat_loop())
                    try:
                        async for raw in ws:
                            await self._dispatch(raw)
                    finally:
                        heartbeat.cancel()
                        self.ws = None
                    log.warning("connection closed (code=%s reason=%s)", ws.close_code, ws.close_reason)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — network errors, 403 bad key, DNS, ...
                log.error("connection error: %s", exc)
                self.ws = None
            if self._stop.is_set():
                break
            delay = min(self.cfg.reconnect_max_s, self.cfg.reconnect_base_s * (2 ** attempt))
            delay = delay * (0.8 + random.random() * 0.4)
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
        await self.send({
            "type": "HELLO",
            "machine": self.cfg.machine_name,
            "version": VERSION,
            "platform": f"{platform.system()} {platform.release()} / py{platform.python_version()}",
            "agents": self.cfg.agents,
            "allowed_commands": "*" if self.cfg.allow_any_command else self.cfg.allowed_commands,
            "features": ["tasks", "skills"] + (["tunnel"] if self.cfg.tunnels_enabled else []),
        })

    async def _heartbeat_loop(self) -> None:
        """Application-level PING so the cloud updates last_heartbeat."""
        while True:
            await asyncio.sleep(self.cfg.heartbeat_interval_s)
            if not await self.send({"type": "PING", "ts": time.time()}):
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
            log.info("✅ authenticated as connector %s (%s)", msg.get("connector_id"), msg.get("machine_name"))
        elif mtype == "PONG":
            log.debug("pong")
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
                proc = await asyncio.create_subprocess_exec(
                    *argv, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
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
                self._procs.pop(task_id, None)
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
        while True:
            line_b = await reader.readline()
            if not line_b:
                return
            counter["n"] += 1
            if counter["n"] > self.cfg.max_output_lines:
                if counter["n"] == self.cfg.max_output_lines + 1:
                    await self.send({"type": "LOG", "task_id": task_id, "stream": "stderr", "line": "[connector] output truncated", "ts": time.time()})
                continue
            line = line_b.decode("utf-8", errors="replace").rstrip("\r\n")[: self.cfg.max_line_chars]
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
    try:
        if proc.returncode is None:
            proc.kill()
    except ProcessLookupError:
        pass


def _post_json(url: str, data: Dict[str, Any]) -> int:
    """POST JSON to a local agent webhook (stdlib only). Returns HTTP status or -1."""
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — user-configured local URL
            return int(resp.status)
    except Exception as exc:  # noqa: BLE001
        log.warning("skill hook failed: %s", exc)
        return -1


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
