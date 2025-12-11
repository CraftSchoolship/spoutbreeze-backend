import logging
from app.services.chat_context import get_user_streams, remove_user_stream
from app.services.broadcaster_service import BroadcasterService
from app.models.user_models import User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger("StreamCleanupService")


class StreamCleanupService:
    """Background service to clean up stale stream entries in Redis"""

    @staticmethod
    async def cleanup_stale_streams(db: AsyncSession):
        """
        Check all users' active streams and remove ones that no longer exist in broadcaster
        Run this periodically (e.g., every 5 minutes)
        """
        try:
            broadcaster_service = BroadcasterService()

            # Get all users
            result = await db.execute(select(User))
            users = result.scalars().all()

            for user in users:
                user_id = str(user.id)
                stream_ids = await get_user_streams(user_id)

                for stream_id in stream_ids:
                    try:
                        # Check if stream still exists
                        await broadcaster_service.fetch_status(stream_id)
                    except Exception:
                        # Stream doesn't exist or failed, remove from Redis
                        await remove_user_stream(stream_id)
                        logger.info(
                            f"Cleaned up stale stream {stream_id} for user {user_id}"
                        )

            logger.info("Stream cleanup completed")
        except Exception as e:
            logger.error(f"Stream cleanup error: {e}")
