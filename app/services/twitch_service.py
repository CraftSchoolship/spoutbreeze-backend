from typing import Dict, Optional
from app.config.twitch_irc import TwitchIRCClient
from app.config.logger_config import get_logger
import asyncio  # ADD

logger = get_logger("TwitchService")


class TwitchService:
    def __init__(self) -> None:
        self._connections: Dict[str, TwitchIRCClient] = {}  # key: user_id

    def get_connection_for_user(self, user_id: str) -> Optional[TwitchIRCClient]:
        return self._connections.get(user_id)

    async def start_connection_for_user(self, user_id: str) -> None:
        # Idempotent: don't create a second connection
        existing = self._connections.get(user_id)
        if existing:
            # If we already created a client, just return (listener runs in background)
            return

        client = TwitchIRCClient(user_id=user_id)
        # Register immediately, then run connect loop in background
        self._connections[user_id] = client
        asyncio.create_task(client.connect())  # DO NOT await (runs forever)

    async def stop_connection_for_user(self, user_id: str) -> None:
        client = self._connections.get(user_id)
        if client:
            await client.disconnect()
            self._connections.pop(user_id, None)


twitch_service = TwitchService()
