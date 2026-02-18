import httpx
import ssl
from app.config.settings import get_settings
from urllib.parse import urlencode
import secrets
import logging

settings = get_settings()
logger = logging.getLogger(__name__)


class TwitchAuth:
    def __init__(self):
        self.client_id = settings.twitch_client_id
        self.client_secret = settings.twitch_client_secret
        self.redirect_uri = settings.twitch_redirect_uri

    def get_authorization_url(self) -> str:
        """Generate the URL for user authorization"""
        state = secrets.token_urlsafe(32)  # Store this securely
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "chat:read chat:edit",
            "state": state,
        }
        return f"https://id.twitch.tv/oauth2/authorize?{urlencode(params)}"

    def _get_public_ssl_context(self):
        """Create SSL context for public APIs (like Twitch) with system certificates"""
        ssl_context = ssl.create_default_context()

        # Try different system certificate locations
        cert_paths = [
            "/etc/ssl/certs/ca-certificates.crt",  # Debian/Ubuntu
            "/etc/pki/tls/certs/ca-bundle.crt",  # CentOS/RHEL
            "/etc/ssl/cert.pem",  # macOS
        ]

        for cert_path in cert_paths:
            try:
                ssl_context.load_verify_locations(cert_path)
                return ssl_context
            except FileNotFoundError:
                continue

        # Fallback to certifi if available
        try:
            import certifi

            ssl_context.load_verify_locations(certifi.where())
            return ssl_context
        except ImportError:
            pass

        # Last resort: use default context (might fail)
        return ssl.create_default_context()

    async def exchange_code_for_token(self, code: str) -> dict:
        """Exchange authorization code for access token"""
        # Use system certificates specifically for Twitch API
        ssl_context = self._get_public_ssl_context()

        async with httpx.AsyncClient(verify=ssl_context) as client:
            response = await client.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "SpoutBreeze/1.0",
                },
            )
            response.raise_for_status()
            return response.json()

    async def refresh_access_token(self, refresh_token: str) -> dict:
        """Refresh the access token using a refresh token.

        Twitch refresh tokens don't expire but are single-use — each refresh
        returns a new refresh_token that must be stored.

        Returns:
            dict with access_token, refresh_token, expires_in, token_type, scope
        """
        ssl_context = self._get_public_ssl_context()

        try:
            async with httpx.AsyncClient(verify=ssl_context, timeout=30.0) as client:
                response = await client.post(
                    "https://id.twitch.tv/oauth2/token",
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": "SpoutBreeze/1.0",
                    },
                )
                response.raise_for_status()
                token_data = response.json()
                logger.info("[TwitchAuth] Access token refreshed successfully")
                return token_data
        except httpx.HTTPStatusError as e:
            logger.error(
                f"[TwitchAuth] Token refresh failed: {e.response.status_code} - {e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"[TwitchAuth] Token refresh error: {e}")
            raise


# Keep the old function for backward compatibility but mark it as deprecated
async def fetch_twitch_token():
    """This generates app tokens which won't work for IRC chat"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.twitch_token_url,
            data={
                "client_id": settings.twitch_client_id,
                "client_secret": settings.twitch_client_secret,
                "grant_type": "client_credentials",
                "scope": "chat:read chat:edit",
            },
        )
        response.raise_for_status()
        token_data = response.json()
        return token_data["access_token"]
