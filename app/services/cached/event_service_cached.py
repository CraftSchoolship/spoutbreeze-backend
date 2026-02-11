from typing import List, Dict, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.event_service import EventService
from app.models.event.event_models import EventStatus
from app.models.event.event_schemas import (
    EventCreate,
    EventUpdate,
    EventResponse,
)
from app.config.redis_config import cached_db, cache
from app.config.settings import get_settings
from app.config.logger_config import get_logger

settings = get_settings()
logger = get_logger("EventServiceCached")


class EventServiceCached(EventService):
    @cached_db(ttl=settings.cache_ttl_long, key_prefix="events_all")
    async def get_all_events(self, db: AsyncSession) -> List[EventResponse]:
        return await super().get_all_events(db)

    @cached_db(ttl=settings.cache_ttl_long, key_prefix="events_status")
    async def get_events_by_status(
        self,
        db: AsyncSession,
        status: EventStatus,
        user_id: Optional[UUID] = None,
    ) -> List[EventResponse]:
        return await super().get_events_by_status(db, status, user_id)

    @cached_db(ttl=settings.cache_ttl_long, key_prefix="events_upcoming")
    async def get_upcoming_events(
        self, db: AsyncSession, user_id: Optional[UUID] = None
    ) -> List[EventResponse]:
        return await super().get_upcoming_events(db, user_id)

    @cached_db(ttl=settings.cache_ttl_long, key_prefix="events_past")
    async def get_past_events(
        self, db: AsyncSession, user_id: Optional[UUID] = None
    ) -> List[EventResponse]:
        return await super().get_past_events(db, user_id)

    @cached_db(ttl=settings.cache_ttl_long, key_prefix="events_live")
    async def get_live_events(
        self, db: AsyncSession, user_id: Optional[UUID] = None
    ) -> List[EventResponse]:
        return await super().get_live_events(db, user_id)

    @cached_db(ttl=settings.cache_ttl_long, key_prefix="events_by_id")
    async def get_event_by_id(self, db: AsyncSession, event_id: UUID) -> EventResponse:
        return await super().get_event_by_id(db, event_id)

    @cached_db(ttl=settings.cache_ttl_long, key_prefix="events_channel")
    async def get_events_by_channel_id(
        self, db: AsyncSession, channel_id: UUID
    ) -> List[EventResponse]:
        return await super().get_events_by_channel_id(db, channel_id)

    @cached_db(ttl=settings.cache_ttl_short, key_prefix="events_join")
    async def join_event(
        self,
        db: AsyncSession,
        event_id: UUID,
        user_id: Optional[UUID] = None,
        full_name: Optional[str] = None,
    ) -> Dict[str, str]:
        return await super().join_event(db, event_id, user_id, full_name)

    async def create_event(
        self,
        db: AsyncSession,
        event: EventCreate,
        user_id: UUID,
    ) -> EventResponse:
        res = await super().create_event(db, event, user_id)
        await self._invalidate_after_change(event_id=res.id, channel_id=res.channel_id)
        # Also invalidate channels cache in case a new channel was created
        await cache.delete_pattern("channels_all:*")
        await cache.delete_pattern("channels_user:*")
        await cache.delete_pattern("channels_by_id:*")
        await cache.delete_pattern("channels_by_name:*")
        return res

    async def start_event(
        self,
        db: AsyncSession,
        event_id: UUID,
        user_id: UUID,
    ) -> Dict[str, str]:
        res = await super().start_event(db, event_id, user_id)
        await self._invalidate_after_change(event_id=event_id)
        return res

    async def end_event(
        self,
        db: AsyncSession,
        event_id: UUID,
        user_id: UUID,
    ) -> Dict[str, str]:
        res = await super().end_event(db, event_id, user_id)
        await self._invalidate_after_change(event_id=event_id)
        return res

    async def update_event(
        self,
        db: AsyncSession,
        event_id: UUID,
        event_update: EventUpdate,
        user_id: UUID,
    ) -> EventResponse:
        res = await super().update_event(db, event_id, event_update, user_id)
        await self._invalidate_after_change(
            event_id=event_id, channel_id=res.channel_id
        )
        return res

    async def delete_event(
        self,
        db: AsyncSession,
        event_id: UUID,
        user_id: UUID,
    ) -> bool:
        ok = await super().delete_event(db, event_id, user_id)
        if ok:
            await self._invalidate_after_change(event_id=event_id)
        return ok

    # ------------ Invalidation Helper ------------
    async def _invalidate_after_change(
        self,
        event_id: Optional[UUID] = None,
        channel_id: Optional[UUID] = None,
    ):
        # Broad invalidation (safe + simple)
        await cache.delete_pattern("events_all:*")
        await cache.delete_pattern("events_status:*")
        await cache.delete_pattern("events_upcoming:*")
        await cache.delete_pattern("events_past:*")
        await cache.delete_pattern("events_live:*")
        await cache.delete_pattern("events_channel:*")
        await cache.delete_pattern("events_join:*")
        if event_id:
            await cache.delete_pattern(f"events_by_id:*{event_id}*")
        logger.info(
            f"[Events Cache] Invalidated (event={event_id}, channel={channel_id})"
        )
