from typing import List, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rtmp_service import RtmpEndpointService
from app.models.stream_schemas import (
    RtmpEndpointResponse,
    RtmpEndpointUpdate,
    CreateRtmpEndpointCreate,
    RtmpEndpointDeleteResponse,
)
from app.config.redis_config import cached_db, cache
from app.config.logger_config import get_logger
from app.config.settings import get_settings

logger = get_logger("RtmpServiceCached")
settings = get_settings()


class RtmpEndpointServiceCached(RtmpEndpointService):
    # READS: cache
    @cached_db(ttl=settings.cache_ttl_long, key_prefix="rtmp_all")  # 1 hour
    async def get_all_rtmp_endpoints(
        self, db: AsyncSession
    ) -> List[RtmpEndpointResponse]:
        return await super().get_all_rtmp_endpoints(db)

    @cached_db(ttl=settings.cache_ttl_long, key_prefix="rtmp_user")  # 1 hour
    async def get_rtmp_endpoints_by_user_id(
        self, user_id: UUID, db: AsyncSession
    ) -> List[RtmpEndpointResponse]:
        return await super().get_rtmp_endpoints_by_user_id(user_id, db)

    @cached_db(ttl=settings.cache_ttl_long, key_prefix="rtmp_by_id")  # 1 hour
    async def get_rtmp_endpoints_by_id(
        self, rtmp_endpoints_id: UUID, db: AsyncSession
    ) -> Optional[RtmpEndpointResponse]:
        return await super().get_rtmp_endpoints_by_id(rtmp_endpoints_id, db)

    # WRITES: invalidate affected caches
    async def create_rtmp_endpoints(
        self, rtmp_endpoints: CreateRtmpEndpointCreate, user_id: UUID, db: AsyncSession
    ) -> RtmpEndpointResponse:
        res = await super().create_rtmp_endpoints(rtmp_endpoints, user_id, db)
        await self._invalidate_after_change(user_id=user_id)
        return res

    async def update_rtmp_endpoints(
        self,
        rtmp_endpoints_id: UUID,
        rtmp_endpoints_update: RtmpEndpointUpdate,
        db: AsyncSession,
    ) -> Optional[RtmpEndpointResponse]:
        res = await super().update_rtmp_endpoints(
            rtmp_endpoints_id, rtmp_endpoints_update, db
        )
        # We don't know user_id here easily; nuke broad caches
        await self._invalidate_after_change()
        return res

    async def delete_rtmp_endpoints(
        self, rtmp_endpoints_id: UUID, user_id: UUID, db: AsyncSession
    ) -> Optional[RtmpEndpointDeleteResponse]:
        res = await super().delete_rtmp_endpoints(rtmp_endpoints_id, user_id, db)
        await self._invalidate_after_change(user_id=user_id)
        return res

    async def _invalidate_after_change(self, user_id: Optional[UUID] = None):
        # Broad but safe invalidation (keys are hashed, so we drop by prefix)
        await cache.delete_pattern("rtmp_all:*")
        await cache.delete_pattern("rtmp_by_id:*")
        await cache.delete_pattern("rtmp_user:*")  # drop all user lists
        if user_id:
            logger.info(f"[RTMP Cache] Invalidated after change for user {user_id}")
        else:
            logger.info("[RTMP Cache] Invalidated after change (global)")
