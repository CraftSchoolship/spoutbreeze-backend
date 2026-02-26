import httpx
import secrets
from urllib.parse import urlencode
from app.config.settings import get_settings
from app.config.logger_config import get_logger

settings = get_settings()
logger = get_logger("FacebookAuth")

# Facebook Graph API version
GRAPH_API_VERSION = "v25.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


class FacebookAuth:
    def __init__(self):
        self.app_id = settings.facebook_app_id
        self.app_secret = settings.facebook_app_secret
        self.redirect_uri = settings.facebook_redirect_uri
        self.scopes = [
            "publish_video",
            "pages_manage_posts",
            "pages_read_engagement",
        ]

    def get_authorization_url(self) -> str:
        """Generate the Facebook OAuth dialog URL."""
        state = secrets.token_urlsafe(32)
        params = {
            "client_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": ",".join(self.scopes),
            "state": state,
        }
        return f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str) -> dict:
        """Exchange authorization code for a short-lived access token."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{GRAPH_BASE}/oauth/access_token",
                    params={
                        "client_id": self.app_id,
                        "client_secret": self.app_secret,
                        "redirect_uri": self.redirect_uri,
                        "code": code,
                    },
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Token exchange failed: {e.response.status_code} - {e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"Token exchange error: {e}")
            raise

    async def exchange_for_long_lived_token(self, short_lived_token: str) -> dict:
        """Exchange a short-lived token (~1h) for a long-lived token (~60 days).

        Returns:
            dict with access_token, token_type, expires_in
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{GRAPH_BASE}/oauth/access_token",
                    params={
                        "grant_type": "fb_exchange_token",
                        "client_id": self.app_id,
                        "client_secret": self.app_secret,
                        "fb_exchange_token": short_lived_token,
                    },
                )
                response.raise_for_status()
                token_data = response.json()
                logger.info("[FacebookAuth] Exchanged for long-lived token")
                return token_data
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Long-lived token exchange failed: {e.response.status_code} - {e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"Long-lived token exchange error: {e}")
            raise

    async def refresh_access_token(self, long_lived_token: str) -> dict:
        """Refresh a long-lived token.

        Facebook long-lived tokens can be refreshed by exchanging them
        again (same endpoint as long-lived exchange). The new token
        will have a fresh expiry (~60 days).

        Returns:
            dict with access_token, token_type, expires_in
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{GRAPH_BASE}/oauth/access_token",
                    params={
                        "grant_type": "fb_exchange_token",
                        "client_id": self.app_id,
                        "client_secret": self.app_secret,
                        "fb_exchange_token": long_lived_token,
                    },
                )
                response.raise_for_status()
                token_data = response.json()
                logger.info("[FacebookAuth] Token refreshed successfully")
                return token_data
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Token refresh failed: {e.response.status_code} - {e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            raise

    async def get_user_pages(self, access_token: str) -> list[dict]:
        """Fetch pages managed by the authenticated user.

        Returns:
            list of dicts with id, name, access_token (page token)
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{GRAPH_BASE}/me/accounts",
                    params={
                        "access_token": access_token,
                        "fields": "id,name,access_token",
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data.get("data", [])
        except Exception as e:
            logger.error(f"Failed to fetch user pages: {e}")
            raise

    async def create_live_video(
        self,
        access_token: str,
        target_id: str = "me",
        title: str = "SpoutBreeze Live",
        privacy: str = "EVERYONE",
    ) -> dict:
        """Create a LiveVideo and go live immediately.

        Calls POST /{target_id}/live_videos?status=LIVE_NOW which:
        - Creates the LiveVideo object
        - Returns the RTMP URL (replaces manual 'Go Live' button)

        Args:
            access_token: User or Page access token
            target_id: 'me' for user profile, or a Page ID
            title: Stream title
            privacy: EVERYONE, ALL_FRIENDS, or SELF

        Returns:
            dict with id (live_video_id), rtmp_url, stream_key
        """
        try:
            params = {
                "status": "LIVE_NOW",
                "title": title,
                "access_token": access_token,
            }
            # Privacy only applies to user profiles, not Pages
            if target_id == "me":
                params["privacy"] = f'{{"value":"{privacy}"}}'

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{GRAPH_BASE}/{target_id}/live_videos",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

                live_video_id = data["id"]
                stream_url = data.get("secure_stream_url") or data.get("stream_url", "")

                # Parse stream_url into rtmp_url + stream_key
                rtmp_url, stream_key = self._parse_stream_url(stream_url)

                logger.info(
                    f"[FacebookAuth] LiveVideo created: {live_video_id} on {target_id}"
                )

                return {
                    "live_video_id": live_video_id,
                    "stream_url": stream_url,
                    "rtmp_url": rtmp_url,
                    "stream_key": stream_key,
                }
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Create live video failed: {e.response.status_code} - {e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"Create live video error: {e}")
            raise

    async def end_live_video(self, access_token: str, live_video_id: str) -> dict:
        """End a live broadcast.

        Calls POST /{live_video_id}?end_live_video=true
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{GRAPH_BASE}/{live_video_id}",
                    params={
                        "end_live_video": "true",
                        "access_token": access_token,
                    },
                )
                response.raise_for_status()
                logger.info(f"[FacebookAuth] LiveVideo ended: {live_video_id}")
                return response.json()
        except Exception as e:
            logger.error(f"End live video failed: {e}")
            raise

    async def get_live_video_status(
        self, access_token: str, live_video_id: str
    ) -> dict:
        """Get status of a live video.

        Returns:
            dict with id, status, video (associated video post)
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{GRAPH_BASE}/{live_video_id}",
                    params={
                        "fields": "id,status,title,video,permalink_url",
                        "access_token": access_token,
                    },
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Get live video status failed: {e}")
            raise

    @staticmethod
    def _parse_stream_url(stream_url: str) -> tuple[str, str]:
        """Parse Facebook stream_url into (rtmp_url, stream_key).

        Input:  rtmps://live-api-s.facebook.com:443/rtmp/KEY?params...
        Output: ('rtmps://live-api-s.facebook.com:443/rtmp/', 'KEY?params...')
        """
        if not stream_url:
            return ("", "")

        # Find the last '/rtmp/' and split there
        rtmp_marker = "/rtmp/"
        idx = stream_url.rfind(rtmp_marker)
        if idx == -1:
            # Fallback: return the whole URL as rtmp_url
            return (stream_url, "")

        split_point = idx + len(rtmp_marker)
        rtmp_url = stream_url[:split_point]
        stream_key = stream_url[split_point:]
        return (rtmp_url, stream_key)

