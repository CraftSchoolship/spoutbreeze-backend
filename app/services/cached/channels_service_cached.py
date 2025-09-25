from typing import List, Optional, Dict, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.channels_service import ChannelsService
from app.models.channel.channels_schemas import (
    ChannelCreate,
    ChannelResponse,
    ChannelUpdate,
)
from app.config.redis_config import cache, cached_db
from app.config.settings import get_settings
from app.config.logger_config import get_logger

logger = get_logger("ChannelsServiceCached")
settings = get_settings()


class ChannelsServiceCached(ChannelsService):
    @cached_db(ttl=settings.cache_ttl_medium, key_prefix="channels_all")
    async def get_channels(self, db: AsyncSession) -> List[ChannelResponse]:
        return await super().get_channels(db)

    @cached_db(ttl=settings.cache_ttl_medium, key_prefix="channels_user")
    async def get_channels_by_user_id(
        self, db: AsyncSession, user_id: UUID
    ) -> List[ChannelResponse]:
        return await super().get_channels_by_user_id(db, user_id)

    @cached_db(ttl=settings.cache_ttl_medium, key_prefix="channels_by_id")
    async def get_channel_by_id(
        self, db: AsyncSession, channel_id: UUID
    ) -> Optional[ChannelResponse]:
        return await super().get_channel_by_id(db, channel_id)

    @cached_db(ttl=settings.cache_ttl_medium, key_prefix="channels_by_name")
    async def get_channel_by_name(
        self, db: AsyncSession, channel_name: str, user_id: UUID
    ):
        return await super().get_channel_by_name(db, channel_name, user_id)

    @cached_db(ttl=settings.cache_ttl_short, key_prefix="channels_recordings")
    async def get_channel_recordings(
        self, db: AsyncSession, channel_id: UUID, user_id: UUID
    ) -> Dict[str, Any]:
        return await super().get_channel_recordings(db, channel_id, user_id)

    # WRITES (invalidate)
    async def create_channel(
        self, db: AsyncSession, channel_create: ChannelCreate, user_id: UUID
    ) -> ChannelResponse:
        res = await super().create_channel(db, channel_create, user_id)
        await self._invalidate_after_change()
        return res

    async def update_channel(
        self,
        db: AsyncSession,
        channel_id: UUID,
        channel_update: ChannelUpdate,
        user_id: UUID,
    ) -> Optional[ChannelResponse]:
        res = await super().update_channel(db, channel_id, channel_update, user_id)
        await self._invalidate_after_change()
        return res

    async def delete_channel(
        self, db: AsyncSession, channel_id: UUID, user_id: UUID
    ) -> bool:
        res = await super().delete_channel(db, channel_id, user_id)
        await self._invalidate_after_change()
        return res

    async def _invalidate_after_change(self):
        # Broad invalidation (keys are hashed)
        await cache.delete_pattern("channels_all:*")
        await cache.delete_pattern("channels_user:*")
        await cache.delete_pattern("channels_by_id:*")
        await cache.delete_pattern("channels_by_name:*")
        await cache.delete_pattern("channels_recordings:*")
        logger.info("[Channels Cache] Invalidated after change")
