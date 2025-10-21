from typing import Dict, Optional
import asyncio
from app.config.youtube_chat_client import YouTubeChatClient

class YouTubeService:
    def __init__(self) -> None:
        self._connections: Dict[str, YouTubeChatClient] = {}

    def get_connection_for_user(self, user_id: str) -> Optional[YouTubeChatClient]:
        return self._connections.get(user_id)

    async def start_connection_for_user(self, user_id: str) -> None:
        if user_id in self._connections:
            return
        client = YouTubeChatClient(user_id=user_id)
        self._connections[user_id] = client
        asyncio.create_task(client.connect())

    async def start_with_chat_id(self, user_id: str, live_chat_id: str) -> None:
        client = self._connections.get(user_id)
        if not client:
            client = YouTubeChatClient(user_id=user_id)
            self._connections[user_id] = client
        asyncio.create_task(client.connect_with_known_chat_id(live_chat_id))

    async def stop_connection_for_user(self, user_id: str) -> None:
        client = self._connections.get(user_id)
        if client:
            await client.disconnect()
            self._connections.pop(user_id, None)

youtube_service = YouTubeService()