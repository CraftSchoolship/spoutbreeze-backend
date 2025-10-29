import os
import httpx
import logging

logger = logging.getLogger("ChatGatewayClient")

CHAT_GATEWAY_URL = os.getenv("CHAT_GATEWAY_URL", "http://localhost:8800")
SHARED_SECRET = os.getenv("CHAT_GATEWAY_SHARED_SECRET", "dev-secret")


class ChatGatewayClient:
    def __init__(self) -> None:
        self.base_url = CHAT_GATEWAY_URL
        self.secret = SHARED_SECRET

    async def forward_message(
        self,
        platform: str,
        user_id: str,
        username: str,
        message: str,
        message_id: str = None,
    ) -> None:
        """Forward incoming platform message to gateway for normalization"""
        url = f"{self.base_url}/messages/incoming"

        try:
            async with httpx.AsyncClient(timeout=5) as client:
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

    async def register_platform(self, platform: str, user_id: str) -> None:
        """Notify gateway that a platform connection is active"""
        url = f"{self.base_url}/platforms/register"
        headers = {"X-Internal-Auth": self.secret}

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    url,
                    params={"platform": platform, "user_id": user_id},
                    headers=headers,
                )
                response.raise_for_status()
                logger.info(f"[Gateway] Registered {platform} for user {user_id}")
        except Exception as e:
            logger.error(f"[Gateway] Failed to register platform: {e}")


chat_gateway_client = ChatGatewayClient()
