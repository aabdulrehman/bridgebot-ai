"""
In-memory WebSocket session pool ("Messenger Hub").

* `connectors`: connector_id -> live WebSocket of the local daemon.
* `dashboards`: user_id      -> set of dashboard browser sockets.

Events from a connector are fanned out to every dashboard of its owner.
NOTE: state is per-process. Run a single worker (free tiers do anyway); for
horizontal scaling swap this for Redis pub/sub.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Set

from fastapi import WebSocket

log = logging.getLogger("bridgebot.hub")


@dataclass
class ConnectorSession:
    """A live connector connection."""

    connector_id: uuid.UUID
    user_id: uuid.UUID
    websocket: WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ConnectionHub:
    """Tracks live sockets and routes messages between them."""

    def __init__(self) -> None:
        self.connectors: Dict[uuid.UUID, ConnectorSession] = {}
        self.dashboards: Dict[uuid.UUID, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        # Bot API: bot_id -> live /ws/bot socket, and wake-up events for long polling.
        self.bots: Dict[uuid.UUID, WebSocket] = {}
        self.bot_events: Dict[uuid.UUID, asyncio.Event] = defaultdict(asyncio.Event)

    # ------------------------------------------------------------------ connectors
    async def register_connector(self, session: ConnectorSession) -> None:
        async with self._lock:
            old = self.connectors.get(session.connector_id)
            self.connectors[session.connector_id] = session
        if old is not None and old.websocket is not session.websocket:
            # Only one live session per connector: kick the stale one.
            try:
                await old.websocket.close(code=4000, reason="Replaced by a newer session")
            except Exception:  # noqa: BLE001
                pass

    async def unregister_connector(self, connector_id: uuid.UUID, websocket: WebSocket) -> bool:
        """Remove a session if it is still the current one. Returns True if removed."""
        async with self._lock:
            current = self.connectors.get(connector_id)
            if current is not None and current.websocket is websocket:
                del self.connectors[connector_id]
                return True
        return False

    def is_online(self, connector_id: uuid.UUID) -> bool:
        return connector_id in self.connectors

    async def send_to_connector(self, connector_id: uuid.UUID, message: Dict[str, Any]) -> bool:
        """Send a JSON message to a connector. Returns False if offline/failed."""
        session = self.connectors.get(connector_id)
        if session is None:
            return False
        try:
            async with session.send_lock:
                await session.websocket.send_json(message)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("send to connector %s failed: %s", connector_id, exc)
            return False

    # ------------------------------------------------------------------ dashboards
    async def add_dashboard(self, user_id: uuid.UUID, ws: WebSocket) -> None:
        async with self._lock:
            self.dashboards[user_id].add(ws)

    async def remove_dashboard(self, user_id: uuid.UUID, ws: WebSocket) -> None:
        async with self._lock:
            self.dashboards[user_id].discard(ws)
            if not self.dashboards[user_id]:
                self.dashboards.pop(user_id, None)

    async def broadcast_to_user(self, user_id: uuid.UUID, message: Dict[str, Any]) -> None:
        """Fan a message out to all of a user's dashboards, pruning dead sockets."""
        targets = list(self.dashboards.get(user_id, ()))
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            await self.remove_dashboard(user_id, ws)


    # ------------------------------------------------------------------ bots
    async def register_bot(self, bot_id: uuid.UUID, ws: WebSocket) -> None:
        old = self.bots.get(bot_id)
        self.bots[bot_id] = ws
        if old is not None and old is not ws:
            try:
                await old.close(code=4000, reason="Replaced by a newer session")
            except Exception:  # noqa: BLE001
                pass

    def unregister_bot(self, bot_id: uuid.UUID, ws: WebSocket) -> None:
        if self.bots.get(bot_id) is ws:
            del self.bots[bot_id]

    def bot_online(self, bot_id: uuid.UUID) -> bool:
        return bot_id in self.bots

    async def send_to_bot(self, bot_id: uuid.UUID, message: Dict[str, Any]) -> bool:
        ws = self.bots.get(bot_id)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:  # noqa: BLE001
            self.bots.pop(bot_id, None)
            return False

    def wake_bot(self, bot_id: uuid.UUID) -> None:
        self.bot_events[bot_id].set()


hub = ConnectionHub()
