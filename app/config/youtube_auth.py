import httpx
import secrets
from urllib.parse import urlencode
from app.config.settings import get_settings
from app.config.logger_config import get_logger

settings = get_settings()
logger = get_logger("YouTubeAuth")


class YouTubeAuth:
    def __init__(self):
        self.client_id = settings.youtube_client_id
        self.client_secret = settings.youtube_client_secret
        self.redirect_uri = settings.youtube_redirect_uri
        self.scopes = [
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube.force-ssl",
        ]

    def get_authorization_url(self) -> str:
        """Generate the URL for user authorization"""
        state = secrets.token_urlsafe(32)
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
            "access_type": "offline",  # Important for refresh token
            "prompt": "consent",  # Force consent to get refresh token
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str) -> dict:
        """Exchange authorization code for access token"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": self.redirect_uri,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
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

    async def refresh_access_token(self, refresh_token: str) -> dict:
        """Refresh the access token using refresh token"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                token_data = response.json()
                logger.info("Access token refreshed successfully")
                return token_data
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Token refresh failed: {e.response.status_code} - {e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            raise
