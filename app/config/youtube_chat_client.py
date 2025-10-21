import asyncio
import httpx
from httpx import HTTPStatusError
from datetime import datetime, timedelta
from sqlalchemy import select
from typing import Optional, List, Dict, Any
from app.config.settings import get_settings
from app.config.logger_config import get_logger
from app.config.database.session import get_db
from app.models.youtube_models import YouTubeToken
from app.services.chat_gateway_client import chat_gateway_client
from typing import Mapping, Union, Sequence

QueryParamsType = Mapping[
    str,
    Union[str, int, float, bool, None, Sequence[Union[str, int, float, bool, None]]],
]

logger = get_logger("YouTube")
settings = get_settings()


class YouTubeChatClient:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.token: Optional[str] = None
        self.live_chat_id: Optional[str] = None
        self.next_page_token: Optional[str] = None
        self.polling_interval = 5
        self.is_connected = False
        self._stop_polling = False
        self.authorized_channel_id: Optional[str] = None
        self.authorized_channel_title: Optional[str] = None
        self.last_error: Optional[str] = None

    async def get_active_token(self) -> str:
        async for db in get_db():
            import uuid

            user_uuid = uuid.UUID(self.user_id)
            stmt = (
                select(YouTubeToken)
                .where(
                    YouTubeToken.user_id == user_uuid,
                    YouTubeToken.is_active == True,
                    YouTubeToken.expires_at > datetime.now(),
                )
                .order_by(YouTubeToken.created_at.desc())
            )
            res = await db.execute(stmt)
            rec = res.scalars().first()
            if not rec:
                raise Exception(f"No valid YouTube token for user {self.user_id}")
            return rec.access_token
        raise Exception("DB session not available")

    async def refresh_token_if_needed(self):
        # no-op for now (assume valid token); add refresh if needed
        return

    async def log_channel_identity(self):
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            url = "https://www.googleapis.com/youtube/v3/channels"
            params = {"part": "id,snippet", "mine": "true"}
            async with httpx.AsyncClient() as client:
                params_typed: QueryParamsType = params
                r = await client.get(url, params=params_typed, headers=headers)
                r.raise_for_status()
                data = r.json()
                if data.get("items"):
                    ch = data["items"][0]
                    self.authorized_channel_id = ch["id"]
                    self.authorized_channel_title = ch["snippet"]["title"]
                    logger.info(
                        f"[YouTube] Authorized channel: {self.authorized_channel_title} ({self.authorized_channel_id})"
                    )
        except Exception as e:
            logger.warning(f"[YouTube] Channel identity failed: {e}")

    async def get_live_broadcast_id(self) -> Optional[str]:
        """Resolve active live chat ID for the authorized channel"""
        headers = {"Authorization": f"Bearer {self.token}"}

        # Attempt 1: liveBroadcasts.list (active, mine=true)
        try:
            url = "https://www.googleapis.com/youtube/v3/liveBroadcasts"
            params = {
                "part": "id,snippet,status",
                "broadcastStatus": "active",  # valid values: active|completed|upcoming
                "mine": "true",
                "maxResults": 5,
            }
            async with httpx.AsyncClient() as client:
                params_typed: QueryParamsType = params
                r = await client.get(url, params=params_typed, headers=headers)
                r.raise_for_status()
                data = r.json()
                if data.get("items"):
                    for item in data["items"]:
                        live_chat_id = item.get("snippet", {}).get("liveChatId")
                        if live_chat_id:
                            logger.info(
                                f"[YouTube] liveChatId from broadcasts: {live_chat_id}"
                            )
                            return live_chat_id
        except HTTPStatusError as e:
            logger.warning(
                f"[YouTube] broadcasts(active) failed: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            logger.warning(f"[YouTube] broadcasts(active) lookup failed: {e}")

        # Attempt 2: search live video for this channel → videos.liveStreamingDetails.activeLiveChatId
        try:
            # Ensure we know the authorized channel id
            if not self.authorized_channel_id:
                await self.log_channel_identity()

            if not self.authorized_channel_id:
                logger.warning(
                    "[YouTube] No authorized channel id; cannot search live video"
                )
                return None

            search_url = "https://www.googleapis.com/youtube/v3/search"
            search_params = {
                "part": "id",
                "channelId": self.authorized_channel_id,
                "eventType": "live",
                "type": "video",
                "maxResults": 1,
            }
            async with httpx.AsyncClient() as client:
                sr = await client.get(search_url, params=search_params, headers=headers)
                sr.raise_for_status()
                sdata = sr.json()
                if sdata.get("items"):
                    video_id = sdata["items"][0]["id"]["videoId"]
                    videos_url = "https://www.googleapis.com/youtube/v3/videos"
                    videos_params = {"part": "liveStreamingDetails", "id": video_id}
                    vr = await client.get(
                        videos_url, params=videos_params, headers=headers
                    )
                    vr.raise_for_status()
                    vdata = vr.json()
                    if vdata.get("items"):
                        live_chat_id = vdata["items"][0]["liveStreamingDetails"].get(
                            "activeLiveChatId"
                        )
                        if live_chat_id:
                            logger.info(
                                f"[YouTube] liveChatId from videos: {live_chat_id}"
                            )
                            return live_chat_id
        except HTTPStatusError as e:
            logger.warning(
                f"[YouTube] search/videos failed: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            logger.warning(f"[YouTube] videos/liveStreamingDetails lookup failed: {e}")

        logger.warning("[YouTube] No active live broadcast found (no liveChatId)")
        return None

    async def fetch_chat_messages(self) -> List[Dict[str, Any]]:
        if not self.live_chat_id:
            return []
        url = "https://www.googleapis.com/youtube/v3/liveChat/messages"
        params = {
            "liveChatId": self.live_chat_id,
            "part": "snippet,authorDetails",
            "maxResults": 200,
        }
        if self.next_page_token:
            params["pageToken"] = self.next_page_token
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with httpx.AsyncClient() as client:
                params_typed: QueryParamsType = params
                r = await client.get(url, params=params_typed, headers=headers)
                r.raise_for_status()
                data = r.json()
                self.next_page_token = data.get("nextPageToken")
                self.polling_interval = data.get("pollingIntervalMillis", 5000) / 1000
                return data.get("items", [])
        except Exception as e:
            logger.error(f"[YouTube] fetch messages failed: {e}")
            return []

    async def send_message(self, text: str):
        if not self.live_chat_id:
            raise Exception("No active live chat")
        url = "https://www.googleapis.com/youtube/v3/liveChat/messages"
        params = {"part": "snippet"}
        headers = {"Authorization": f"Bearer {self.token}"}
        body = {
            "snippet": {
                "liveChatId": self.live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {"messageText": text},
            }
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, params=params, headers=headers, json=body)
            r.raise_for_status()
            logger.info(f"[YouTube] → Sent: {text}")

    async def connect(self):
        try:
            await self.refresh_token_if_needed()
            self.token = await self.get_active_token()
            await self.log_channel_identity()

            # retry to allow API propagation
            live_id = None
            for _ in range(10):
                live_id = await self.get_live_broadcast_id()
                if live_id:
                    break
                await asyncio.sleep(0.5)

            if not live_id:
                self.last_error = "No live chat ID found"
                logger.error("[YouTube] No live chat ID found, cannot connect")
                return

            self.live_chat_id = live_id
            self.is_connected = True
            self._stop_polling = False
            logger.info(f"[YouTube] Polling started (chat: {self.live_chat_id})")
            await self._poll_messages()
        except HTTPStatusError as e:
            self.is_connected = False
            self.last_error = f"{e.response.status_code}: {e.response.text}"
            logger.error(
                f"[YouTube] connect HTTP error: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            self.is_connected = False
            self.last_error = str(e)
            logger.error(f"[YouTube] connect error: {e}")

    async def connect_with_known_chat_id(self, live_chat_id: str):
        """Force attach to a known live chat id (debug/unblock)"""
        try:
            await self.refresh_token_if_needed()
            self.token = await self.get_active_token()
            await self.log_channel_identity()
            self.live_chat_id = live_chat_id
            self.is_connected = True
            self._stop_polling = False
            logger.info(f"[YouTube] Polling (forced attach) chat: {self.live_chat_id}")
            await self._poll_messages()
        except Exception as e:
            self.is_connected = False
            self.last_error = str(e)
            logger.error(f"[YouTube] forced attach error: {e}")

    async def _poll_messages(self):
        while not self._stop_polling:
            try:
                items = await self.fetch_chat_messages()
                for msg in items:
                    snippet = msg.get("snippet", {})
                    author = msg.get("authorDetails", {})
                    text = (snippet.get("textMessageDetails") or {}).get(
                        "messageText"
                    ) or ""
                    if not text:
                        continue
                    # Keep: ignore messages authored by our authenticated channel
                    author_channel_id = author.get("channelId")
                    if (
                        author_channel_id
                        and self.authorized_channel_id
                        and author_channel_id == self.authorized_channel_id
                    ):
                        continue

                    username = author.get("displayName", "Unknown")
                    await chat_gateway_client.forward_message(
                        platform="youtube",
                        user_id=author_channel_id,
                        username=username,
                        message=text,
                        message_id=msg.get("id"),
                    )
                    logger.info(f"[YouTube] {username}: {text}")

                # removed: self._prune_sent_ids()
                await asyncio.sleep(self.polling_interval)
            except Exception as e:
                logger.error(f"[YouTube] poll error: {e}")
                await asyncio.sleep(5)

    async def disconnect(self):
        self._stop_polling = True
        self.is_connected = False
        logger.info(f"[YouTube] Disconnected for user {self.user_id}")
