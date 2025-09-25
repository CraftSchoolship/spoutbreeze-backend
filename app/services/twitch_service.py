from typing import Dict, Optional
from app.config.twitch_irc import TwitchIRCClient
from app.config.logger_config import get_logger

logger = get_logger("TwitchService")


class TwitchService:
    def __init__(self) -> None:
        self._user_connections: Dict[str, TwitchIRCClient] = {}

    async def start_connection_for_user(self, user_id: str) -> bool:
        """Start a Twitch IRC connection for a specific user"""
        try:
            if user_id in self._user_connections:
                logger.info(f"TwitchIRC connection already exists for user {user_id}")
                return True

            # Create user-specific client with required user_id
            client = TwitchIRCClient(user_id=user_id)

            # Start connection in background
            import asyncio

            asyncio.create_task(client.connect())

            self._user_connections[user_id] = client
            logger.info(f"Started TwitchIRC connection for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to start TwitchIRC for user {user_id}: {e}")
            return False

    async def stop_connection_for_user(self, user_id: str) -> bool:
        """Stop the Twitch IRC connection for a specific user"""
        if user_id in self._user_connections:
            try:
                client = self._user_connections[user_id]
                if client.writer and not client.writer.is_closing():
                    client.writer.close()
                    await client.writer.wait_closed()
                del self._user_connections[user_id]
                logger.info(f"Stopped TwitchIRC connection for user {user_id}")
                return True
            except Exception as e:
                logger.error(f"Error stopping TwitchIRC for user {user_id}: {e}")
                return False
        return False

    def get_connection_for_user(self, user_id: str) -> Optional[TwitchIRCClient]:
        """Get existing connection for a user"""
        return self._user_connections.get(user_id)

    async def disconnect_all(self) -> None:
        """Disconnect all user connections"""
        for user_id, client in list(self._user_connections.items()):
            try:
                if client.writer and not client.writer.is_closing():
                    client.writer.close()
                    await client.writer.wait_closed()
            except Exception as e:
                logger.error(f"Error disconnecting user {user_id}: {e}")
        self._user_connections.clear()
        logger.info("All Twitch connections disconnected")


# Global instance
twitch_service = TwitchService()
