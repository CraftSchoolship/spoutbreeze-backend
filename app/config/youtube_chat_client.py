import asyncio
import httpx
from httpx import HTTPStatusError
from datetime import datetime, timedelta
from sqlalchemy import select
from typing import Optional, List, Dict, Any
import uuid

from app.config.settings import get_settings
from app.config.logger_config import get_logger
from app.config.database.session import get_db
from app.models.youtube_models import YouTubeToken
from app.services.chat_gateway_client import chat_gateway_client
from app.config.youtube_auth import YouTubeAuth

logger = get_logger("YouTube")
settings = get_settings()


class YouTubeChatClient:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None
        self.live_chat_id: Optional[str] = None
        self.next_page_token: Optional[str] = None
        self.polling_interval = 5
        self.is_connected = False
        self._stop_polling = False
        self.authorized_channel_id: Optional[str] = None
        self.authorized_channel_title: Optional[str] = None
        self.last_error: Optional[str] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create persistent HTTP client"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def get_active_token(self) -> tuple[str, Optional[str], Optional[datetime]]:
        """Get token from DB - returns (access_token, refresh_token, expires_at)"""
        async for db in get_db():
            user_uuid = uuid.UUID(self.user_id)
            stmt = (
                select(YouTubeToken)
                .where(
                    YouTubeToken.user_id == user_uuid,
                    YouTubeToken.is_active == True,
                )
                .order_by(YouTubeToken.created_at.desc())
            )
            res = await db.execute(stmt)
            rec = res.scalars().first()
            if not rec:
                raise Exception(f"No valid YouTube token for user {self.user_id}")
            return rec.access_token, rec.refresh_token, rec.expires_at
        raise Exception("DB session not available")

    async def refresh_token_if_needed(self) -> bool:
        """Check if token is expired/expiring and refresh if needed"""
        if not self.token_expires_at:
            return False

        # Refresh if token expires within next 5 minutes
        time_until_expiry = (self.token_expires_at - datetime.now()).total_seconds()

        if time_until_expiry > 300:  # More than 5 minutes left
            return False

        if not self.refresh_token:
            logger.warning(
                f"[YouTube] Token expiring but no refresh token available for user {self.user_id}"
            )
            return False

        try:
            logger.info(f"[YouTube] Refreshing token for user {self.user_id}")
            youtube_auth = YouTubeAuth()
            token_data = await youtube_auth.refresh_access_token(self.refresh_token)

            # Update instance variables
            self.token = token_data.get("access_token")
            new_expires_in = token_data.get("expires_in", 3600)
            self.token_expires_at = datetime.now() + timedelta(seconds=new_expires_in)

            # If new refresh token provided, update it (usually not provided on refresh)
            if token_data.get("refresh_token"):
                self.refresh_token = token_data["refresh_token"]

            # Update database
            await self._save_refreshed_token(
                self.token, self.refresh_token, self.token_expires_at
            )

            logger.info(
                f"[YouTube] Token refreshed successfully for user {self.user_id}"
            )
            return True

        except Exception as e:
            logger.error(f"[YouTube] Token refresh failed for user {self.user_id}: {e}")
            self.last_error = f"Token refresh failed: {str(e)}"
            return False

    async def _save_refreshed_token(
        self, access_token: str, refresh_token: Optional[str], expires_at: datetime
    ):
        """Save refreshed token back to database"""
        async for db in get_db():
            user_uuid = uuid.UUID(self.user_id)
            stmt = select(YouTubeToken).where(
                YouTubeToken.user_id == user_uuid, YouTubeToken.is_active == True
            )
            res = await db.execute(stmt)
            rec = res.scalars().first()

            if rec:
                rec.access_token = access_token
                if refresh_token:
                    rec.refresh_token = refresh_token
                rec.expires_at = expires_at
                rec.updated_at = datetime.now()
                await db.commit()
                logger.debug(f"[YouTube] Updated token in DB for user {self.user_id}")
            break

    async def _handle_quota_exceeded(self):
        """Handle quota exceeded error - stop all operations"""
        self.is_connected = False
        self.last_error = "YouTube API quota exceeded (10,000 units/day). Resets at midnight Pacific Time."
        self._stop_polling = True
        logger.error(
            f"[YouTube] QUOTA EXCEEDED for user {self.user_id}. Service paused until quota reset."
        )
        # Optionally: notify user via gateway
        # await chat_gateway_client.notify_user(self.user_id, "YouTube quota exceeded")

    async def _make_api_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None,
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        """Make YouTube API request with quota handling"""
        client = await self._get_http_client()

        for attempt in range(max_retries + 1):
            await self.refresh_token_if_needed()
            headers = {"Authorization": f"Bearer {self.token}"}

            try:
                if method == "GET":
                    response = await client.get(url, params=params, headers=headers)
                elif method == "POST":
                    response = await client.post(
                        url, params=params, headers=headers, json=json
                    )
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                response.raise_for_status()
                return response.json()

            except HTTPStatusError as e:
                status_code = e.response.status_code
                error_reason = ""
                try:
                    error_data = e.response.json()
                    error_reason = (
                        error_data.get("error", {})
                        .get("errors", [{}])[0]
                        .get("reason", "")
                    )
                except Exception:
                    error_reason = e.response.text

                # CRITICAL: Handle quota exceeded - DO NOT RETRY
                if error_reason == "quotaExceeded":
                    logger.error(
                        "[YouTube] ⚠️ QUOTA EXCEEDED - Daily limit of 10,000 units reached"
                    )
                    await self._handle_quota_exceeded()
                    raise Exception(
                        "YouTube API quota exceeded. Resets at midnight Pacific Time."
                    )

                # Handle 401 Unauthorized
                if status_code == 401 and attempt < max_retries:
                    logger.warning(
                        "[YouTube] 401 Unauthorized, attempting token refresh"
                    )
                    # ... existing 401 handling ...
                    continue

                # Handle rate limiting (different from quota)
                if status_code == 429:
                    retry_after = int(e.response.headers.get("Retry-After", 10))
                    logger.warning(
                        f"[YouTube] Rate limited (429), waiting {retry_after}s"
                    )
                    await asyncio.sleep(retry_after)
                    if attempt < max_retries:
                        continue

                logger.error(
                    f"[YouTube] API request failed: {status_code} - {error_reason}"
                )
                raise

            except Exception as e:
                logger.error(f"[YouTube] Request error: {e}")
                if attempt == max_retries:
                    raise
                await asyncio.sleep(2**attempt)

    async def log_channel_identity(self):
        """Get and log the authorized channel identity"""
        try:
            data = await self._make_api_request(
                "GET",
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "id,snippet", "mine": "true"},
            )

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
        # Attempt 1: liveBroadcasts.list (mine=true)
        try:
            data = await self._make_api_request(
                "GET",
                "https://www.googleapis.com/youtube/v3/liveBroadcasts",
                params={
                    "part": "id,snippet,status",
                    "mine": "true",
                    "maxResults": 5,
                },
            )

            if data.get("items"):
                for item in data["items"]:
                    live_chat_id = item.get("snippet", {}).get("liveChatId")
                    if live_chat_id:
                        logger.info(
                            f"[YouTube] liveChatId from broadcasts: {live_chat_id}"
                        )
                        return live_chat_id
        except Exception as e:
            logger.warning(f"[YouTube] broadcasts lookup failed: {e}")
            return None

        # Attempt 2: search live video for this channel
        # will not use this method for now it uses 100 quota units
        # try:
        #     if not self.authorized_channel_id:
        #         await self.log_channel_identity()

        #     if not self.authorized_channel_id:
        #         logger.warning(
        #             "[YouTube] No authorized channel id; cannot search live video"
        #         )
        #         return None

        #     search_data = await self._make_api_request(
        #         "GET",
        #         "https://www.googleapis.com/youtube/v3/search",
        #         params={
        #             "part": "id",
        #             "channelId": self.authorized_channel_id,
        #             "eventType": "live",
        #             "type": "video",
        #             "maxResults": 1,
        #         },
        #     )

        #     if search_data.get("items"):
        #         video_id = search_data["items"][0]["id"]["videoId"]

        #         video_data = await self._make_api_request(
        #             "GET",
        #             "https://www.googleapis.com/youtube/v3/videos",
        #             params={"part": "liveStreamingDetails", "id": video_id},
        #         )

        #         if video_data.get("items"):
        #             live_chat_id = video_data["items"][0]["liveStreamingDetails"].get(
        #                 "activeLiveChatId"
        #             )
        #             if live_chat_id:
        #                 logger.info(f"[YouTube] liveChatId from videos: {live_chat_id}")
        #                 return live_chat_id
        # except Exception as e:
        #     logger.warning(f"[YouTube] search/videos lookup failed: {e}")

        # logger.warning("[YouTube] No active live broadcast found (no liveChatId)")
        # return None

    async def fetch_chat_messages(self) -> List[Dict[str, Any]]:
        """Fetch new messages from live chat"""
        if not self.live_chat_id:
            return []

        params = {
            "liveChatId": self.live_chat_id,
            "part": "snippet,authorDetails",
            "maxResults": 200,
        }
        if self.next_page_token:
            params["pageToken"] = self.next_page_token

        try:
            data = await self._make_api_request(
                "GET",
                "https://www.googleapis.com/youtube/v3/liveChat/messages",
                params=params,
            )

            self.next_page_token = data.get("nextPageToken")
            self.polling_interval = data.get("pollingIntervalMillis", 5000) / 1000
            return data.get("items", [])

        except HTTPStatusError as e:
            status = e.response.status_code
            reason = ""
            try:
                reason = (
                    e.response.json()
                    .get("error", {})
                    .get("errors", [{}])[0]
                    .get("reason", "")
                )
            except Exception:
                reason = e.response.text

            # Chat ended or unavailable
            if status in (403, 404) or reason in (
                "liveChatEnded",
                "liveChatNotFound",
                "forbidden",
                "liveChatDisabled",
            ):
                self.is_connected = False
                self.last_error = f"Live chat ended or unavailable ({status}: {reason})"
                self._stop_polling = True
                self.live_chat_id = None
                self.next_page_token = None
                logger.warning(
                    f"[YouTube] Live chat ended/unavailable; stopping. {status}: {reason}"
                )
                return []

            logger.error(f"[YouTube] fetch messages failed: {status} - {reason}")
            return []
        except Exception as e:
            logger.error(f"[YouTube] fetch messages failed: {e}")
            return []

    async def send_message(self, text: str):
        """Send a message to the live chat"""
        if not self.live_chat_id:
            raise Exception("No active live chat")

        body = {
            "snippet": {
                "liveChatId": self.live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {"messageText": text},
            }
        }

        try:
            await self._make_api_request(
                "POST",
                "https://www.googleapis.com/youtube/v3/liveChat/messages",
                params={"part": "snippet"},
                json=body,
            )
            logger.info(f"[YouTube] → Sent: {text}")
        except HTTPStatusError as e:
            status = e.response.status_code
            reason = ""
            try:
                reason = (
                    e.response.json()
                    .get("error", {})
                    .get("errors", [{}])[0]
                    .get("reason", "")
                )
            except Exception:
                reason = e.response.text

            logger.error(f"[YouTube] Send message failed: {status} - {reason}")
            raise Exception(f"Failed to send message: {status} - {reason}")

    async def connect(self):
        """Connect to YouTube live chat and start polling"""
        try:
            # Load tokens from DB
            (
                self.token,
                self.refresh_token,
                self.token_expires_at,
            ) = await self.get_active_token()

            # Refresh if needed
            await self.refresh_token_if_needed()

            # Get channel identity
            await self.log_channel_identity()

            live_id = await self.get_live_broadcast_id()

            if not live_id:
                self.last_error = (
                    "No live chat ID found. Use /auth/youtube/attach-by-video endpoint."
                )
                logger.error(
                    "[YouTube] ⚠️ No liveChatId found. User must provide video ID manually."
                )
                return

            self.live_chat_id = live_id

            # Probe once to verify chat is accessible
            await self.fetch_chat_messages()
            if not self.live_chat_id:  # May be cleared if chat ended
                logger.warning(
                    "[YouTube] Live chat unavailable after discovery; not connecting"
                )
                return

            self.is_connected = True
            self._stop_polling = False
            logger.info(f"[YouTube] Polling started (chat: {self.live_chat_id})")
            await self._poll_messages()

        except Exception as e:
            self.is_connected = False
            self.last_error = str(e)
            logger.error(f"[YouTube] connect error: {e}")

    async def connect_with_known_chat_id(self, live_chat_id: str):
        """Force attach to a known live chat id (debug/unblock)"""
        try:
            (
                self.token,
                self.refresh_token,
                self.token_expires_at,
            ) = await self.get_active_token()
            await self.refresh_token_if_needed()
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
        """Main polling loop for chat messages"""
        consecutive_errors = 0
        while not self._stop_polling:
            try:
                items = await self.fetch_chat_messages()
                consecutive_errors = 0

                for msg in items:
                    snippet = msg.get("snippet", {})
                    author = msg.get("authorDetails", {})
                    text = (snippet.get("textMessageDetails") or {}).get(
                        "messageText"
                    ) or ""

                    if not text:
                        continue

                    # Ignore messages from authenticated channel
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

                await asyncio.sleep(self.polling_interval)

            except Exception as e:
                if "quota exceeded" in str(e).lower():
                    logger.error("[YouTube] Stopping polling due to quota exceeded.")
                    break
                consecutive_errors += 1
                logger.error(f"[YouTube] poll error: {e}")

                backoff_time = min(
                    5 * (2**consecutive_errors), 60
                )  # Exponential backoff up to 60s
                await asyncio.sleep(backoff_time)

    async def disconnect(self):
        """Disconnect from YouTube chat and cleanup"""
        self._stop_polling = True
        self.is_connected = False
        self.live_chat_id = None
        self.next_page_token = None

        # Close HTTP client
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

        logger.info(f"[YouTube] Disconnected for user {self.user_id}")
