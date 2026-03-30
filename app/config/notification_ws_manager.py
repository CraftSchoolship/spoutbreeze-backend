"""
WebSocket connection manager for the notification system.

Tracks per-user WebSocket connections and provides:
  - Online presence tracking via Redis sets
  - Per-user message delivery
  - Graceful disconnect / reconnect handling
"""

from __future__ import annotations

import json
from contextlib import suppress
from uuid import UUID

from fastapi import WebSocket

from app.config.logger_config import get_logger
from app.config.redis_config import cache

logger = get_logger("NotificationWSManager")

# Redis key for the set of online user IDs
_ONLINE_USERS_KEY = "notifications:online_users"
# TTL for the online set (refreshed on each connect; safety net for stale entries)
_ONLINE_SET_TTL = 3600  # 1 hour


class NotificationWSManager:
    """
    Manages per-user WebSocket connections for real-time notification delivery.

    Design notes:
      - One user may have multiple concurrent connections (e.g. multiple tabs).
      - Online status is tracked in Redis so any backend instance can query it.
      - The in-memory dict holds the *local* connections for this process.
    """

    def __init__(self) -> None:
        # user_id -> list of active WebSocket connections on this process
        self._connections: dict[str, list[WebSocket]] = {}

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, user_id: UUID, websocket: WebSocket) -> None:
        """Accept a WebSocket and register the user as online."""
        await websocket.accept()
        uid = str(user_id)

        if uid not in self._connections:
            self._connections[uid] = []
        self._connections[uid].append(websocket)

        # Mark user as online in Redis (shared across backend instances)
        await cache.sadd(_ONLINE_USERS_KEY, uid)
        await cache.expire(_ONLINE_USERS_KEY, _ONLINE_SET_TTL)

        logger.info(f"[WS] User {uid} connected (local connections: {len(self._connections[uid])})")

    async def disconnect(self, user_id: UUID, websocket: WebSocket) -> None:
        """Remove a WebSocket. If no connections remain, mark user offline."""
        uid = str(user_id)

        if uid in self._connections:
            with suppress(ValueError):
                self._connections[uid].remove(websocket)

            if not self._connections[uid]:
                del self._connections[uid]
                # Last local connection gone — remove from Redis
                await cache.srem(_ONLINE_USERS_KEY, uid)
                logger.info(f"[WS] User {uid} fully disconnected")
            else:
                logger.info(f"[WS] User {uid} tab disconnected (remaining: {len(self._connections[uid])})")

    # ------------------------------------------------------------------
    # Presence helpers
    # ------------------------------------------------------------------

    async def is_user_online(self, user_id: UUID) -> bool:
        """Check if a user has at least one active connection (across all instances)."""
        members = await cache.smembers(_ONLINE_USERS_KEY)
        return str(user_id) in members

    def is_user_connected_locally(self, user_id: UUID) -> bool:
        """Check if the user has a WebSocket on *this* process."""
        return str(user_id) in self._connections

    async def get_online_user_ids(self) -> set[str]:
        """Return all user IDs that are currently online."""
        return await cache.smembers(_ONLINE_USERS_KEY)

    # ------------------------------------------------------------------
    # Message delivery
    # ------------------------------------------------------------------

    async def send_to_user(self, user_id: UUID, payload: dict) -> bool:
        """
        Send a JSON message to all local WebSocket connections of a user.
        Returns True if at least one connection received the message.
        """
        uid = str(user_id)
        connections = self._connections.get(uid, [])
        if not connections:
            return False

        message = json.dumps(payload, default=str)
        delivered = False
        stale: list[WebSocket] = []

        for ws in connections:
            try:
                await ws.send_text(message)
                delivered = True
            except Exception as exc:
                logger.warning(f"[WS] Failed to send to user {uid}: {exc}")
                stale.append(ws)

        # Clean up broken connections
        for ws in stale:
            await self.disconnect(user_id, ws)

        return delivered

    async def broadcast(self, payload: dict) -> int:
        """
        Broadcast a message to all connected users.
        Returns the number of users who received it.
        """
        count = 0
        for uid_str in list(self._connections.keys()):
            uid = UUID(uid_str)
            if await self.send_to_user(uid, payload):
                count += 1
        return count


# Singleton instance shared across the application
notification_ws_manager = NotificationWSManager()
