import asyncio
import httpx
import ssl
from datetime import datetime, timedelta
from sqlalchemy import select
from fastapi import HTTPException
from typing import Optional, Dict, Any
import contextlib  # ADD

from app.config.chat_manager import chat_manager
from app.config.settings import get_settings
from app.config.logger_config import get_logger
from app.config.database.session import get_db
from app.models.twitch.twitch_models import TwitchToken
from app.services.chat_gateway_client import chat_gateway_client

logger = get_logger("Twitch")


class TwitchIRCClient:
    def __init__(
        self, user_id: Optional[str] = None
    ):  # Keep it optional but add user_id
        self.settings = get_settings()
        self.server = self.settings.twitch_server
        self.port = self.settings.twitch_port
        self.nickname = self.settings.twitch_nick
        self.channel = f"#{self.settings.twitch_channel}"
        self.reader = None
        self.writer = None
        self.token = None
        self.user_id = user_id  # Store user_id for user-specific connections
        self.is_connected: bool = False  # ADD

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

    async def get_active_token(self, user_id: Optional[str] = None) -> str:
        """Get the active token from database - user-specific if user_id provided"""
        try:
            target_user_id = user_id or self.user_id

            async for db in get_db():
                if target_user_id:
                    # User-specific token query
                    import uuid

                    user_uuid = (
                        uuid.UUID(target_user_id)
                        if isinstance(target_user_id, str)
                        else target_user_id
                    )

                    stmt = (
                        select(TwitchToken)
                        .where(
                            TwitchToken.user_id == user_uuid,
                            TwitchToken.is_active,
                            TwitchToken.expires_at > datetime.now(),
                        )
                        .order_by(TwitchToken.created_at.desc())
                    )

                    result = await db.execute(stmt)
                    token_record = result.scalars().first()

                    if token_record:
                        logger.info(
                            f"[TwitchIRC] Using database token for user {target_user_id}"
                        )
                        return token_record.access_token
                    else:
                        logger.warning(
                            f"[TwitchIRC] No valid token found for user {target_user_id}"
                        )
                        raise HTTPException(
                            status_code=401,
                            detail=f"No valid Twitch token found for user {target_user_id}. Please authenticate via /auth/twitch/login",
                        )
                else:
                    # Global token query (for backward compatibility)
                    stmt = (
                        select(TwitchToken)
                        .where(
                            TwitchToken.is_active,
                            TwitchToken.expires_at > datetime.now(),
                        )
                        .order_by(TwitchToken.created_at.desc())
                    )

                    result = await db.execute(stmt)
                    token_record = result.scalars().first()

                    if token_record:
                        logger.info(
                            f"[TwitchIRC] Using database token for user {token_record.user_id}"
                        )
                        return token_record.access_token
                    else:
                        logger.warning("[TwitchIRC] No valid token found")
                        raise HTTPException(
                            status_code=401,
                            detail="No valid Twitch token found. Please authenticate via /auth/twitch/login",
                        )

            # If we reach here, no database session was available
            logger.error("[TwitchIRC] No database session available")
            raise HTTPException(status_code=500, detail="Database connection error")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[TwitchIRC] Error fetching token from database: {e}")
            raise HTTPException(
                status_code=500, detail="Database error while fetching Twitch token"
            )

    async def refresh_token_if_needed(self, user_id: Optional[str] = None):
        """Check if token needs refresh - user-specific if user_id provided"""
        try:
            target_user_id = user_id or self.user_id

            async for db in get_db():
                if target_user_id:
                    # User-specific refresh
                    import uuid

                    user_uuid = (
                        uuid.UUID(target_user_id)
                        if isinstance(target_user_id, str)
                        else target_user_id
                    )

                    stmt = (
                        select(TwitchToken)
                        .where(
                            TwitchToken.user_id == user_uuid,
                            TwitchToken.is_active,
                        )
                        .order_by(TwitchToken.created_at.desc())
                    )
                else:
                    # Global refresh (backward compatibility)
                    stmt = (
                        select(TwitchToken)
                        .where(TwitchToken.is_active)
                        .order_by(TwitchToken.created_at.desc())
                    )

                result = await db.execute(stmt)
                token_record = result.scalars().first()

                if not token_record:
                    logger.warning("[TwitchIRC] No active token found")
                    break

                # Check if token expires within 5 minutes or has already expired
                expires_soon = datetime.now() + timedelta(minutes=5)

                if token_record.expires_at <= expires_soon:
                    logger.info(
                        "[TwitchIRC] Token expires soon or has expired, attempting refresh..."
                    )

                    if token_record.refresh_token:
                        new_token_data = await self._refresh_access_token(
                            token_record.refresh_token
                        )

                        if new_token_data:
                            # Update the existing token record
                            new_expires_at = datetime.now() + timedelta(
                                seconds=new_token_data.get("expires_in", 3600)
                            )

                            token_record.access_token = new_token_data["access_token"]
                            token_record.expires_at = new_expires_at
                            # Refresh token might be updated too
                            if new_token_data.get("refresh_token"):
                                token_record.refresh_token = new_token_data[
                                    "refresh_token"
                                ]

                            await db.commit()
                            logger.info("[TwitchIRC] Token refreshed successfully")

                            # Update the current token if we're using this one
                            if (
                                hasattr(self, "token")
                                and self.token == token_record.access_token
                            ):
                                self.token = new_token_data["access_token"]
                        else:
                            logger.error(
                                "[TwitchIRC] Failed to refresh token, marking as inactive"
                            )
                            token_record.is_active = False
                            await db.commit()
                    else:
                        logger.warning(
                            "[TwitchIRC] No refresh token available, marking as inactive"
                        )
                        token_record.is_active = False
                        await db.commit()
                break
        except Exception as e:
            logger.error(f"[TwitchIRC] Error checking/refreshing token: {e}")

    async def _refresh_access_token(
        self, refresh_token: str
    ) -> Optional[Dict[str, Any]]:
        """Refresh the access token using the refresh token"""
        try:
            # Use the same SSL context approach as in the connect method
            ssl_context = self._get_public_ssl_context()

            async with httpx.AsyncClient(verify=ssl_context) as client:
                response = await client.post(
                    "https://id.twitch.tv/oauth2/token",
                    data={
                        "client_id": self.settings.twitch_client_id,
                        "client_secret": self.settings.twitch_client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

                if response.status_code == 200:
                    token_data = response.json()
                    logger.info("[TwitchIRC] Access token refreshed successfully")
                    return token_data
                else:
                    logger.error(
                        f"[TwitchIRC] Token refresh failed: {response.status_code} - {response.text}"
                    )
                    return None

        except Exception as e:
            logger.error(f"[TwitchIRC] Exception during token refresh: {e}")
            return None

    async def connect(self):
        """Connect to Twitch IRC with database token"""
        while True:
            try:
                # Check and refresh token if needed before connecting
                await self.refresh_token_if_needed()

                # Get fresh token from database
                self.token = await self.get_active_token()

                if not self.token:
                    logger.error(
                        "[TwitchIRC] No token available, retrying in 30 seconds..."
                    )
                    await asyncio.sleep(30)
                    continue

                ssl_context = self._get_public_ssl_context()

                self.reader, self.writer = await asyncio.open_connection(
                    self.server, self.port, ssl=ssl_context
                )

                self.writer.write(f"PASS oauth:{self.token}\r\n".encode())
                self.writer.write(f"NICK {self.nickname}\r\n".encode())
                self.writer.write(f"JOIN {self.channel}\r\n".encode())
                await self.writer.drain()

                self.is_connected = True  # ADD
                logger.info("[TwitchIRC] Connected, listening for messages…")
                await self.listen()
            except Exception as e:
                self.is_connected = False  # ADD
                logger.info(f"[TwitchIRC] Connection error: {e!r}")
                await asyncio.sleep(5)

    async def listen(self):
        while True:
            line = await self.reader.readline()
            if not line:
                self.is_connected = False  # ADD
                raise ConnectionResetError("Stream closed")
            msg = line.decode(errors="ignore").strip()

            if msg.startswith("PING"):
                if self.writer is not None:
                    self.writer.write(b"PONG :tmi.twitch.tv\r\n")
                    await self.writer.drain()
                else:
                    logger.warning("[TwitchIRC] Writer is None during PING response.")
                continue

            await self._handle_message(msg)

    async def _handle_message(self, message: str) -> None:
        """Parse and handle incoming IRC messages"""
        if message.startswith("PING"):
            if self.writer is not None:
                self.writer.write(b"PONG :tmi.twitch.tv\r\n")
                await self.writer.drain()
            else:
                logger.warning("[TwitchIRC] Writer is None during PING response.")
            return

        if "PRIVMSG" in message:
            try:
                # Parse message
                parts = message.split(":", 2)
                if len(parts) >= 3:
                    username = parts[1].split("!")[0]
                    msg_content = parts[2].strip()

                    logger.info(f"[TwitchIRC] {username}: {msg_content}")

                    # Forward to Chat Gateway (simplified call)
                    try:
                        await chat_gateway_client.forward_message(
                            platform="twitch",
                            user_id=self.user_id or username,
                            username=username,
                            message=msg_content,
                            message_id=None,  # ADD
                        )
                    except Exception as e:
                        logger.error(f"[TwitchIRC] Failed to forward to gateway: {e}")

            except Exception as e:
                logger.error(f"[TwitchIRC] Error parsing message: {e}")

    async def send_message(self, message: str):
        if self.writer:
            full_message = f"PRIVMSG {self.channel} :{message}\r\n"
            self.writer.write(full_message.encode())
            await self.writer.drain()
            logger.info(f"[TwitchIRC] Sent: {message}")
        else:
            logger.info("[TwitchIRC] Writer not initialized, cannot send message.")

    async def send_chat_message(self, message: str) -> None:
        """Send a message to the Twitch chat"""
        if not self.writer:
            raise Exception("Not connected to Twitch IRC")
        full = f"PRIVMSG {self.channel} :{message}\r\n"
        self.writer.write(full.encode())
        await self.writer.drain()
        logger.info(f"[TwitchIRC] → Sent message: {message}")

    async def disconnect(self):
        """Gracefully close the IRC connection"""
        try:
            if self.writer:
                try:
                    self.writer.write("PART {0}\r\n".format(self.channel).encode())
                    await self.writer.drain()
                except Exception:
                    pass
                self.writer.close()
                with contextlib.suppress(Exception):
                    await self.writer.wait_closed()
        finally:
            self.reader = None
            self.writer = None
            self.is_connected = False

    async def start_token_refresh_scheduler(self):
        """Start a background task to periodically check and refresh tokens"""
        while True:
            try:
                await self.refresh_token_if_needed()
                # Check every 30 minutes
                await asyncio.sleep(1800)
            except Exception as e:
                logger.error(f"[TwitchIRC] Token refresh scheduler error: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes on error
