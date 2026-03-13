"""
Background service to proactively refresh expiring OAuth tokens.

Runs via APScheduler every 30 minutes.  Refreshes any active connection
whose access token expires within the next 30 minutes, using the
provider's refresh-token endpoint.

Twitch tokens last ~4 hours, YouTube ~1 hour, so a 30-minute cycle
gives at least 2 refresh opportunities before any token expires.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection_model import Connection
from app.services.connection_service import ConnectionService

logger = logging.getLogger("TokenRefresh")

# Refresh tokens that expire within this window
BACKGROUND_REFRESH_THRESHOLD_SECONDS = 1800  # 30 minutes


class TokenRefreshService:
    """Proactively refreshes tokens that are about to expire."""

    @staticmethod
    async def refresh_expiring_tokens(db: AsyncSession) -> None:
        """Scan for active connections expiring soon and refresh them."""
        threshold = datetime.now() + timedelta(seconds=BACKGROUND_REFRESH_THRESHOLD_SECONDS)

        stmt = (
            select(Connection)
            .where(
                Connection.revoked_at.is_(None),
                Connection.expires_at <= threshold,
                Connection.refresh_token.isnot(None),
            )
            .order_by(Connection.expires_at.asc())
        )
        result = await db.execute(stmt)
        connections = result.scalars().all()

        if not connections:
            logger.debug("[TokenRefresh] No tokens need refreshing")
            return

        logger.info(f"[TokenRefresh] Found {len(connections)} token(s) to refresh")

        success_count = 0
        fail_count = 0

        for conn in connections:
            ok = await ConnectionService.refresh_connection(db, conn)
            if ok:
                success_count += 1
            else:
                fail_count += 1

        logger.info(f"[TokenRefresh] Refresh complete: {success_count} succeeded, {fail_count} failed")
