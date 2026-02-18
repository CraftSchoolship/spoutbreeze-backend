"""
Shared service for managing platform connections (Twitch, YouTube, etc.).

Handles token encryption/decryption, automatic refresh, and CRUD operations
against the unified `connections` table.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import update, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection_model import Connection
from app.utils.token_encryption import encrypt_token, decrypt_token

logger = logging.getLogger(__name__)

# Refresh the token if it expires within this many seconds
REFRESH_THRESHOLD_SECONDS = 300  # 5 minutes


class ConnectionService:
    """Service layer for unified platform connections."""

    # --- Provider-specific refresh helpers ---

    @staticmethod
    async def _refresh_twitch(refresh_token: str) -> dict:
        from app.config.twitch_auth import TwitchAuth

        auth = TwitchAuth()
        return await auth.refresh_access_token(refresh_token)

    @staticmethod
    async def _refresh_youtube(refresh_token: str) -> dict:
        from app.config.youtube_auth import YouTubeAuth

        auth = YouTubeAuth()
        return await auth.refresh_access_token(refresh_token)

    _REFRESHERS = {
        "twitch": _refresh_twitch.__func__,
        "youtube": _refresh_youtube.__func__,
    }

    # --- Public API ---

    @classmethod
    async def save_connection(
        cls,
        db: AsyncSession,
        user_id,
        provider: str,
        token_data: dict,
        scopes: list[str],
        provider_user_id: Optional[str] = None,
    ) -> Connection:
        """Create or replace a platform connection (soft-revoke the old one first)."""

        # Soft-revoke any existing active connection for this provider
        stmt = (
            update(Connection)
            .where(
                Connection.user_id == user_id,
                Connection.provider == provider,
                Connection.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now())
        )
        await db.execute(stmt)

        expires_at = datetime.now() + timedelta(
            seconds=token_data.get("expires_in", 3600)
        )

        connection = Connection(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            access_token=encrypt_token(token_data["access_token"]),
            refresh_token=encrypt_token(token_data["refresh_token"])
            if token_data.get("refresh_token")
            else None,
            scopes=json.dumps(scopes),
            expires_at=expires_at,
        )
        db.add(connection)
        await db.commit()

        logger.info(f"[{provider}] Connection saved for user {user_id}")
        return connection

    @classmethod
    async def get_active_connection(
        cls,
        db: AsyncSession,
        user_id,
        provider: str,
    ) -> Optional[Connection]:
        """Return the active (non-revoked) connection for a user + provider."""
        stmt = (
            select(Connection)
            .where(
                Connection.user_id == user_id,
                Connection.provider == provider,
                Connection.revoked_at.is_(None),
            )
            .order_by(Connection.created_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    @classmethod
    async def get_valid_token(
        cls,
        db: AsyncSession,
        user_id,
        provider: str,
    ) -> Optional[dict]:
        """Return a valid (decrypted) access token, auto-refreshing if needed.

        Returns dict with access_token, refresh_token, expires_at or None.
        """
        connection = await cls.get_active_connection(db, user_id, provider)
        if not connection:
            return None

        now = datetime.now()
        time_left = (connection.expires_at - now).total_seconds()

        # If token is expired or about to expire, try to refresh
        if time_left < REFRESH_THRESHOLD_SECONDS and connection.refresh_token:
            refresher = cls._REFRESHERS.get(provider)
            if refresher:
                try:
                    decrypted_refresh = decrypt_token(connection.refresh_token)
                    token_data = await refresher(decrypted_refresh)

                    # Update the connection with the new tokens
                    connection.access_token = encrypt_token(token_data["access_token"])
                    if token_data.get("refresh_token"):
                        connection.refresh_token = encrypt_token(
                            token_data["refresh_token"]
                        )
                    connection.expires_at = now + timedelta(
                        seconds=token_data.get("expires_in", 3600)
                    )
                    connection.updated_at = now
                    await db.commit()

                    logger.info(
                        f"[{provider}] Token auto-refreshed for user {user_id}"
                    )
                except Exception as e:
                    logger.error(
                        f"[{provider}] Token refresh failed for user {user_id}: {e}"
                    )
                    # If refresh fails and token is fully expired, return None
                    if connection.expires_at <= now:
                        return None

        # If fully expired with no refresh token, return None
        if connection.expires_at <= now and not connection.refresh_token:
            return None

        return {
            "access_token": decrypt_token(connection.access_token),
            "refresh_token": decrypt_token(connection.refresh_token)
            if connection.refresh_token
            else None,
            "expires_at": connection.expires_at.isoformat(),
        }

    @classmethod
    async def revoke_connection(
        cls,
        db: AsyncSession,
        user_id,
        provider: str,
    ) -> int:
        """Soft-revoke all active connections for a user + provider.

        Returns the number of connections revoked.
        """
        stmt = (
            update(Connection)
            .where(
                Connection.user_id == user_id,
                Connection.provider == provider,
                Connection.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now())
        )
        result = await db.execute(stmt)
        await db.commit()
        logger.info(
            f"[{provider}] Connection revoked for user {user_id} "
            f"({result.rowcount} rows)"
        )
        return result.rowcount

    @classmethod
    async def get_connection_status(
        cls,
        db: AsyncSession,
        user_id,
        provider: str,
    ) -> dict:
        """Return a safe status dict for a connection (no raw tokens)."""
        connection = await cls.get_active_connection(db, user_id, provider)

        if not connection:
            return {
                "user_id": str(user_id),
                "provider": provider,
                "has_token": False,
                "error": "No active connection found",
            }

        now = datetime.now()
        time_until_expiry = connection.expires_at - now

        return {
            "user_id": str(user_id),
            "provider": provider,
            "has_token": True,
            "expires_at": connection.expires_at.isoformat(),
            "is_expired": connection.expires_at <= now,
            "expires_soon": time_until_expiry.total_seconds() < 3600,
            "has_refresh_token": connection.refresh_token is not None,
            "scopes": connection.get_scopes_list(),
            "created_at": connection.created_at.isoformat(),
        }
