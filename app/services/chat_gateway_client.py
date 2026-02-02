import os
import httpx
import logging
from typing import Optional

logger = logging.getLogger("ChatGatewayClient")

CHAT_GATEWAY_URL = os.getenv("CHAT_GATEWAY_URL", "http://localhost:8081")
SHARED_SECRET = os.getenv("CHAT_GATEWAY_SHARED_SECRET", "dev-secret")


class ChatGatewayClient:
    def __init__(self) -> None:
        self.base_url = CHAT_GATEWAY_URL
        self.secret = SHARED_SECRET
        logger.info(f"[Gateway Client] Initialized with base_url: {self.base_url}")

    async def forward_message(
        self,
        platform: str,
        user_id: str,
        username: str,
        message: str,
        message_id: Optional[str] = None,
    ) -> None:
        """Forward incoming platform message to gateway for normalization"""
        url = f"{self.base_url}/messages/incoming"

        try:
            async with httpx.AsyncClient(timeout=5, verify=False) as client:
                await client.post(
                    url,
                    json={
                        "platform": platform,
                        "user_id": user_id,
                        "user_name": username,
                        "content": message,
                        "message_id": message_id,
                    },
                )
                logger.debug(f"[Gateway] Forwarded {platform} message from {username}")
        except Exception as e:
            logger.error(f"[Gateway] Failed to forward message: {e}")

    async def connect_twitch(self, user_id: str, meeting_id: str = None) -> None:
        """Start Twitch IRC connection for user"""
        url = f"{self.base_url}/platforms/twitch/connect"
        headers = {"X-Internal-Auth": self.secret}

        logger.info(
            f"[Gateway Client] Calling {url} with user_id={user_id}, meeting_id={meeting_id}"
        )

        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as client:
                response = await client.post(
                    url,
                    params={"user_id": user_id, "meeting_id": meeting_id},
                    headers=headers,
                )
                logger.info(f"[Gateway Client] Response status: {response.status_code}")
                response.raise_for_status()
                logger.info(f"[Gateway] ✅ Started Twitch for user {user_id}")
        except Exception as e:
            logger.error(f"[Gateway] ❌ Failed to start Twitch: {e}")
            raise

    async def disconnect_twitch(self, user_id: str) -> None:
        """Stop Twitch IRC connection for user"""
        url = f"{self.base_url}/platforms/twitch/disconnect"
        headers = {"X-Internal-Auth": self.secret}

        logger.info(f"[Gateway Client] Disconnecting Twitch for user {user_id}")

        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as client:
                response = await client.post(
                    url,
                    params={"user_id": user_id},
                    headers=headers,
                )
                response.raise_for_status()
                logger.info(f"[Gateway] ✅ Stopped Twitch for user {user_id}")
        except Exception as e:
            logger.error(f"[Gateway] Failed to stop Twitch: {e}")

    async def connect_youtube(self, user_id: str, meeting_id: str = None) -> None:
        """Start YouTube polling for user"""
        url = f"{self.base_url}/platforms/youtube/connect"
        headers = {"X-Internal-Auth": self.secret}

        logger.info(
            f"[Gateway Client] Calling {url} with user_id={user_id}, meeting_id={meeting_id}"
        )

        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as client:
                response = await client.post(
                    url,
                    params={"user_id": user_id, "meeting_id": meeting_id},
                    headers=headers,
                )
                logger.info(f"[Gateway Client] Response status: {response.status_code}")
                response.raise_for_status()
                logger.info(f"[Gateway] ✅ Started YouTube for user {user_id}")
        except Exception as e:
            logger.error(f"[Gateway] ❌ Failed to start YouTube: {e}")
            raise

    async def disconnect_youtube(self, user_id: str) -> None:
        """Stop YouTube polling for user"""
        url = f"{self.base_url}/platforms/youtube/disconnect"
        headers = {"X-Internal-Auth": self.secret}

        logger.info(f"[Gateway Client] Disconnecting YouTube for user {user_id}")

        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as client:
                response = await client.post(
                    url,
                    params={"user_id": user_id},
                    headers=headers,
                )
                response.raise_for_status()
                logger.info(f"[Gateway] ✅ Stopped YouTube for user {user_id}")
        except Exception as e:
            logger.error(f"[Gateway] Failed to stop YouTube: {e}")


chat_gateway_client = ChatGatewayClient()
