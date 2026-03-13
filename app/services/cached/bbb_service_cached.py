from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logger_config import get_logger
from app.config.redis_config import cache, cached
from app.config.settings import get_settings
from app.models.bbb_schemas import (
    CreateMeetingRequest,
    EndMeetingRequest,
    GetMeetingInfoRequest,
    GetRecordingRequest,
    IsMeetingRunningRequest,
)
from app.services.bbb_service import BBBService

logger = get_logger("BBBServiceCached")
settings = get_settings()


class BBBServiceCached(BBBService):
    # READS (BBB API) → cached
    @cached(ttl=settings.cache_ttl_bbb, key_prefix="bbb:meeting_info")
    async def get_meeting_info_cached(self, request: GetMeetingInfoRequest) -> dict[str, Any]:
        return super().get_meeting_info(request)

    @cached(ttl=60, key_prefix="bbb:is_running")
    async def is_meeting_running_cached(self, request: IsMeetingRunningRequest) -> dict[str, Any]:
        return super().is_meeting_running(request)

    @cached(ttl=settings.cache_ttl_bbb, key_prefix="bbb:meetings")
    async def get_meetings_cached(self) -> dict[str, Any]:
        return super().get_meetings()

    @cached(ttl=settings.cache_ttl_medium, key_prefix="bbb:recordings")
    async def get_recordings_cached(self, request: GetRecordingRequest) -> dict[str, Any]:
        return super().get_recordings(request)

    # WRITES/STATE CHANGES → invalidate
    async def create_meeting(
        self,
        request: CreateMeetingRequest,
        user_id: UUID,
        db: AsyncSession,
        event_id: UUID | None = None,
    ) -> dict[str, Any]:
        resp = await super().create_meeting(request, user_id, db, event_id)
        meeting_id = resp.get("meetingID") or request.meeting_id
        await self._invalidate_after_change(meeting_id)
        return resp

    async def end_meeting(self, request: EndMeetingRequest, db: AsyncSession) -> dict[str, Any]:
        resp = await super().end_meeting(request, db)
        await self._invalidate_after_change(request.meeting_id)
        return resp

    async def update_meeting_status(self, meeting_id: str, db: AsyncSession, is_ended: bool = False) -> dict[str, Any]:
        resp = await super().update_meeting_status(meeting_id, db, is_ended=is_ended)
        await self._invalidate_after_change(meeting_id)
        return resp

    async def meeting_ended_callback(self, meeting_id: str, db: AsyncSession, event_id: UUID | None = None) -> dict[str, Any]:
        resp = await super().meeting_ended_callback(meeting_id, db, event_id)
        await self._invalidate_after_change(meeting_id)
        return resp

    async def _invalidate_after_change(self, meeting_id: str | None):
        # Broad + meeting-specific invalidation
        await cache.delete_pattern("bbb:meetings:*")
        if meeting_id:
            await cache.delete_pattern(f"bbb:meeting_info:*{meeting_id}*")
            await cache.delete_pattern(f"bbb:is_running:*{meeting_id}*")
            await cache.delete_pattern(f"bbb:recordings:*{meeting_id}*")
        logger.info(f"[BBB Cache] Invalidated (meeting_id={meeting_id})")
