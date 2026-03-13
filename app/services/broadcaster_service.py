import logging
from collections import defaultdict
from typing import Any

import requests
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from requests import Timeout as RequestsTimeout
from sqlalchemy import select

from app.config.redis_config import cache
from app.config.settings import get_settings
from app.models.bbb_schemas import (
    BroadcasterRequest,
    GetMeetingInfoRequest,
    IsMeetingRunningRequest,
    JoinMeetingRequest,
    PluginManifests,
    StreamConfig,
)
from app.models.user_models import User
from app.services.bbb_service import BBBService
from app.services.chat_gateway_client import chat_gateway_client
from app.services.payment_service import PaymentService

logger = logging.getLogger("BroadcasterService")

_STREAM_TTL = 86400  # 24 hours


class StreamTracker:
    """Track active streams using Redis with in-memory fallback"""

    # In-memory fallback (used when Redis is unavailable)
    _fallback_user_streams: dict[str, set[str]] = defaultdict(set)
    _fallback_stream_to_user: dict[str, str] = {}
    _fallback_stream_platforms: dict[str, str] = {}

    @staticmethod
    async def add_stream(user_id: str, stream_id: str, platform: str | None = None) -> None:
        """Register a new active stream"""
        try:
            if cache.redis_client:
                await cache.sadd(f"streams:user:{user_id}", stream_id)
                await cache.expire(f"streams:user:{user_id}", _STREAM_TTL)
                await cache.set(f"streams:stream_to_user:{stream_id}", user_id, ttl=_STREAM_TTL)
                if platform:
                    await cache.set(f"streams:platform:{stream_id}", platform, ttl=_STREAM_TTL)
                return
        except Exception as e:
            logger.warning(f"Redis stream tracking failed, using fallback: {e}")

        # Fallback to in-memory
        StreamTracker._fallback_user_streams[user_id].add(stream_id)
        StreamTracker._fallback_stream_to_user[stream_id] = user_id
        if platform:
            StreamTracker._fallback_stream_platforms[stream_id] = platform

    @staticmethod
    async def remove_stream(stream_id: str) -> tuple[str | None, str | None]:
        """Remove a stream, returns (user_id, platform)"""
        user_id = None
        platform = None

        try:
            if cache.redis_client:
                raw_user = await cache.get(f"streams:stream_to_user:{stream_id}")
                user_id = raw_user.decode() if isinstance(raw_user, bytes) else raw_user if isinstance(raw_user, str) else None
                raw_platform = await cache.get(f"streams:platform:{stream_id}")
                platform = (
                    raw_platform.decode()
                    if isinstance(raw_platform, bytes)
                    else raw_platform
                    if isinstance(raw_platform, str)
                    else None
                )

                if user_id:
                    await cache.srem(f"streams:user:{user_id}", stream_id)
                await cache.delete(f"streams:stream_to_user:{stream_id}")
                await cache.delete(f"streams:platform:{stream_id}")
                return user_id, platform
        except Exception as e:
            logger.warning(f"Redis stream removal failed, using fallback: {e}")

        # Fallback
        user_id = StreamTracker._fallback_stream_to_user.pop(stream_id, None)
        platform = StreamTracker._fallback_stream_platforms.pop(stream_id, None)
        if user_id and stream_id in StreamTracker._fallback_user_streams.get(user_id, set()):
            StreamTracker._fallback_user_streams[user_id].discard(stream_id)
            if not StreamTracker._fallback_user_streams[user_id]:
                del StreamTracker._fallback_user_streams[user_id]
        return user_id, platform

    @staticmethod
    async def get_active_stream_count(user_id: str) -> int:
        """Get count of active streams for a user"""
        try:
            if cache.redis_client:
                return await cache.scard(f"streams:user:{user_id}")
        except Exception as e:
            logger.warning(f"Redis stream count failed, using fallback: {e}")
        return len(StreamTracker._fallback_user_streams.get(user_id, set()))

    @staticmethod
    async def get_user_streams(user_id: str) -> set[str]:
        """Get set of active stream IDs for a user"""
        try:
            if cache.redis_client:
                return await cache.smembers(f"streams:user:{user_id}")
        except Exception as e:
            logger.warning(f"Redis stream members failed, using fallback: {e}")
        return StreamTracker._fallback_user_streams.get(user_id, set()).copy()


# Quality order helper
_QUALITY_ORDER: dict[str, int] = {
    "360p": 0,
    "480p": 1,
    "720p": 2,
    "1080p": 3,
    "1440p": 4,
    "4K": 5,
}


def _clamp_resolution(requested: str | None, max_quality: str) -> str:
    """
    Return the requested resolution if it is <= max_quality; otherwise return max_quality.
    If requested is None/invalid, fall back to max_quality.
    """
    if max_quality not in _QUALITY_ORDER:
        max_quality = "720p"

    if not requested or requested not in _QUALITY_ORDER:
        return max_quality

    if _QUALITY_ORDER[requested] <= _QUALITY_ORDER[max_quality]:
        return requested

    return max_quality


class BroadcasterService:
    def __init__(self):
        settings = get_settings()
        self.broadcaster_api_url = settings.broadcaster_api_url.rstrip("/")
        self.plugin_manifests_url = settings.plugin_manifests_url
        self.timeout = getattr(settings, "broadcaster_api_timeout", 30)

    async def start_broadcasting(
        self,
        meeting_id: str,
        rtmp_url: str,
        stream_key: str,
        password: str,
        platform: str,
        bbb_service: BBBService,
        user_id: str,
        db,
        requested_resolution: str | None = None,  # <-- NEW parameter
    ) -> dict[str, Any]:
        try:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            subscription = await PaymentService.get_user_subscription(user, db)
            if not subscription:
                subscription = await PaymentService.create_free_subscription(user, db)

            limits = subscription.get_plan_limits()
            max_quality = limits.get("max_quality", "720p")
            max_duration = limits.get("max_stream_duration_hours")
            max_concurrent_streams = limits.get("max_concurrent_streams")
            is_basic_plan = max_duration == 1

            # Clamp requested resolution to the plan's max
            effective_resolution = _clamp_resolution(
                requested_resolution or user.default_resolution,
                max_quality,
            )

            # Concurrent stream check via StreamTracker
            active_stream_count = await StreamTracker.get_active_stream_count(user_id)
            if max_concurrent_streams is not None and active_stream_count >= max_concurrent_streams:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Concurrent stream limit reached. Your plan allows "
                        f"{max_concurrent_streams} concurrent stream(s). "
                        f"Please upgrade your plan or stop an existing stream."
                    ),
                )

            # Meeting running check
            bbb_service.is_meeting_running(request=IsMeetingRunningRequest(meeting_id=meeting_id))
            meeting_info = bbb_service.get_meeting_info(
                request=GetMeetingInfoRequest(meeting_id=meeting_id, password=password)
            )

            plugin_manifests = [PluginManifests(url=self.plugin_manifests_url)]
            join_request = JoinMeetingRequest(
                meeting_id=meeting_id,
                password=password,
                full_name="SpoutBreeze Bot",
                pluginManifests=plugin_manifests,
                user_id="spoutbreeze_bot",
            )
            join_url = bbb_service.get_join_url(request=join_request)

            broadcaster_payload = BroadcasterRequest(
                close_popups=True,
                is_basic_plan=is_basic_plan,
                fps=16,
                resolution=effective_resolution,  # <-- use clamped resolution
                bbb_server_url=join_url,
                stream=StreamConfig(
                    platform=platform,
                    rtmp_url=rtmp_url,
                    stream_key=stream_key,
                ),
            )

            def do_post():
                return requests.post(
                    self.broadcaster_api_url,
                    json=broadcaster_payload.model_dump(),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    timeout=self.timeout,
                )

            response = await run_in_threadpool(do_post)
            if response.status_code not in (200, 201):
                raise HTTPException(
                    status_code=502,
                    detail=f"Broadcaster error ({response.status_code}): {response.text}",
                )

            data = response.json()
            stream_id = data.get("stream_id")
            if not stream_id:
                raise HTTPException(status_code=502, detail="Broadcaster response missing stream_id")

            platform_lower = platform.lower()
            platform_connected = None

            if "twitch" in platform_lower:
                try:
                    await chat_gateway_client.connect_twitch(user_id, meeting_id)
                    platform_connected = "twitch"
                except Exception as e:
                    logger.error(f"Twitch connect failed: {e}")
            elif "youtube" in platform_lower:
                try:
                    await chat_gateway_client.connect_youtube(user_id, meeting_id)
                    platform_connected = "youtube"
                except Exception as e:
                    logger.error(f"YouTube connect failed: {e}")

            # Track stream (after platform connection so platform is tracked too)
            await StreamTracker.add_stream(user_id, stream_id, platform_connected)

            return {
                "status": data.get("status", "running"),
                "message": "Broadcaster started successfully",
                "join_url": join_url,
                "stream": {
                    "stream_id": stream_id,
                    "pod_name": data.get("pod_name"),
                    "status": data.get("status"),
                    "created_at": data.get("created_at"),
                },
                "meeting_info": meeting_info,
            }

        except RequestsTimeout:
            raise HTTPException(status_code=504, detail="Broadcaster API timed out (network issue)")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Start failed: {e}")
            raise HTTPException(status_code=500, detail=f"Broadcaster start failed: {str(e)}")

    async def fetch_status(self, stream_id: str) -> dict[str, Any]:
        url = f"{self.broadcaster_api_url}/{stream_id}"

        def do_get():
            return requests.get(url, timeout=self.timeout)

        try:
            response = await run_in_threadpool(do_get)
            response.raise_for_status()
            return response.json()
        except RequestsTimeout:
            raise HTTPException(status_code=504, detail="Broadcaster status check timed out")

    async def stop_broadcast(self, stream_id: str) -> dict[str, Any]:
        url = f"{self.broadcaster_api_url}/{stream_id}"

        def do_delete():
            return requests.delete(url, timeout=self.timeout)

        try:
            response = await run_in_threadpool(do_delete)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Stop failed: {e}")
            raise HTTPException(status_code=500, detail=f"Stop failed: {str(e)}")

        # Remove stream from tracker and get associated user/platform
        user_id, platform = await StreamTracker.remove_stream(stream_id)

        # Disconnect platform chat
        if user_id and platform:
            try:
                logger.info(f"[Broadcaster] Disconnecting {platform} for user {user_id}")
                if platform == "twitch":
                    await chat_gateway_client.disconnect_twitch(user_id)
                elif platform == "youtube":
                    await chat_gateway_client.disconnect_youtube(user_id)
                logger.info(f"[Broadcaster] {platform.capitalize()} disconnected")
            except Exception as e:
                logger.error(f"[Broadcaster] Failed to disconnect {platform}: {e}")

        return {"message": "Stream stopped", "stream_id": stream_id}
