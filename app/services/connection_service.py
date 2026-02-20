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

    @staticmethod
    async def _refresh_facebook(refresh_token: str) -> dict:
        from app.config.facebook_auth import FacebookAuth

        auth = FacebookAuth()
        token_data = await auth.refresh_access_token(refresh_token)
        # Facebook returns a new long-lived token; store it as refresh_token too
        if "refresh_token" not in token_data:
            token_data["refresh_token"] = token_data["access_token"]
        return token_data

    _REFRESHERS = {
        "twitch": _refresh_twitch.__func__,
        "youtube": _refresh_youtube.__func__,
        "facebook": _refresh_facebook.__func__,
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
        """Create or update a platform connection (upsert)."""

        now = datetime.now()
        expires_at = now + timedelta(
            seconds=token_data.get("expires_in", 3600)
        )

        encrypted_access = encrypt_token(token_data["access_token"])
        encrypted_refresh = (
            encrypt_token(token_data["refresh_token"])
            if token_data.get("refresh_token")
            else None
        )

        # Check for ANY existing connection for this user+provider (regardless of revoked status)
        stmt = (
            select(Connection)
            .where(
                Connection.user_id == user_id,
                Connection.provider == provider,
            )
            .order_by(Connection.created_at.desc())
        )
        result = await db.execute(stmt)
        existing = result.scalars().first()

        if existing:
            # Update in-place (also re-activate if previously revoked)
            existing.access_token = encrypted_access
            existing.refresh_token = encrypted_refresh
            existing.scopes = json.dumps(scopes)
            existing.expires_at = expires_at
            existing.updated_at = now
            existing.revoked_at = None  # Re-activate if it was revoked
            if provider_user_id is not None:
                existing.provider_user_id = provider_user_id
            await db.commit()
            logger.info(f"[{provider}] Connection updated for user {user_id}")
            return existing

        # First-time connection — create new row
        connection = Connection(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            access_token=encrypted_access,
            refresh_token=encrypted_refresh,
            scopes=json.dumps(scopes),
            expires_at=expires_at,
        )
        db.add(connection)
        await db.commit()

        logger.info(f"[{provider}] Connection created for user {user_id}")
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
    async def refresh_connection(
        cls,
        db: AsyncSession,
        connection: Connection,
    ) -> bool:
        """Refresh a connection's access token using its refresh token.

        Updates the connection in-place and commits to DB.
        Returns True on success, False on failure.
        """
        provider = connection.provider
        user_id = connection.user_id

        if not connection.refresh_token:
            logger.warning(
                f"[{provider}] Cannot refresh — no refresh token for user {user_id}"
            )
            return False

        refresher = cls._REFRESHERS.get(provider)
        if not refresher:
            logger.warning(f"[{provider}] No refresher registered for provider")
            return False

        try:
            decrypted_refresh = decrypt_token(connection.refresh_token)
            token_data = await refresher(decrypted_refresh)

            now = datetime.now()
            connection.access_token = encrypt_token(token_data["access_token"])
            if token_data.get("refresh_token"):
                connection.refresh_token = encrypt_token(token_data["refresh_token"])
            connection.expires_at = now + timedelta(
                seconds=token_data.get("expires_in", 3600)
            )
            connection.updated_at = now
            await db.commit()

            logger.info(f"[{provider}] Token refreshed for user {user_id}")
            return True
        except Exception as e:
            logger.error(
                f"[{provider}] Token refresh failed for user {user_id}: {e}"
            )
            return False

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

        # If token is expired or about to expire, try to refresh (lazy safety net)
        if time_left < REFRESH_THRESHOLD_SECONDS and connection.refresh_token:
            success = await cls.refresh_connection(db, connection)
            if not success and connection.expires_at <= now:
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
