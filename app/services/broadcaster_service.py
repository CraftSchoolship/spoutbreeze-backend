from requests import Timeout as RequestsTimeout
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from typing import Dict, Any
import requests
import logging
from app.models.bbb_schemas import (
    BroadcasterRequest,
    IsMeetingRunningRequest,
    GetMeetingInfoRequest,
    JoinMeetingRequest,
    PluginManifests,
    StreamConfig,
)
from app.config.settings import get_settings
from app.services.bbb_service import BBBService
from app.services.chat_context import set_user_mapping
from app.services.chat_gateway_client import chat_gateway_client

logger = logging.getLogger("BroadcasterService")


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
    ) -> Dict[str, Any]:
        """
        Create a broadcaster stream (blocking until broadcaster responds) and return its real stream_id.
        """
        try:
            # 1. Write meeting_id → user_id to Redis FIRST
            await set_user_mapping(meeting_id=meeting_id, user_id=user_id, ttl=86400)
            logger.info(f"[Broadcaster] Mapped meeting {meeting_id} → user {user_id}")

            # 2. Verify meeting is running
            bbb_service.is_meeting_running(
                request=IsMeetingRunningRequest(meeting_id=meeting_id)
            )

            meeting_info = bbb_service.get_meeting_info(
                request=GetMeetingInfoRequest(meeting_id=meeting_id, password=password)
            )

            # 3. Join meeting as bot
            plugin_manifests = [PluginManifests(url=self.plugin_manifests_url)]
            join_request = JoinMeetingRequest(
                meeting_id=meeting_id,
                password=password,
                full_name="SpoutBreeze Bot",
                pluginManifests=plugin_manifests,
                user_id="spoutbreeze_bot",
            )
            join_url = bbb_service.get_join_url(request=join_request)

            # 4. Start broadcaster pod
            broadcaster_payload = BroadcasterRequest(
                close_popups=True,
                is_basic_plan=True,
                resolution="720p",
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
                raise HTTPException(
                    status_code=502, detail="Broadcaster response missing stream_id"
                )

            logger.info(f"[Broadcaster] Started stream {stream_id}")

            # 5. Connect to platform chat AFTER broadcaster starts
            platform_lower = platform.lower()
            if "twitch" in platform_lower:
                try:
                    await chat_gateway_client.connect_twitch(user_id)
                    logger.info(
                        f"[Broadcaster] Connected Twitch chat for user {user_id}"
                    )
                except Exception as e:
                    logger.error(f"[Broadcaster] Failed to connect Twitch: {e}")
                    # Don't fail the broadcast if chat connection fails
            elif "youtube" in platform_lower:
                try:
                    await chat_gateway_client.connect_youtube(user_id)
                    logger.info(
                        f"[Broadcaster] Connected YouTube chat for user {user_id}"
                    )
                except Exception as e:
                    logger.error(f"[Broadcaster] Failed to connect YouTube: {e}")

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
            raise HTTPException(
                status_code=504, detail="Broadcaster API timed out (network issue)"
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Broadcaster] Start failed: {e}")
            raise HTTPException(
                status_code=500, detail=f"Broadcaster start failed: {str(e)}"
            )

    async def fetch_status(self, stream_id: str) -> Dict[str, Any]:
        """
        Proxy GET /streams/{id} and normalize response keys.
        """
        url = f"{self.broadcaster_api_url}/{stream_id}"

        def do_get():
            return requests.get(url, timeout=self.timeout)

        try:
            response = await run_in_threadpool(do_get)
            response.raise_for_status()
            return response.json()
        except RequestsTimeout:
            raise HTTPException(
                status_code=504, detail="Broadcaster status check timed out"
            )

    async def stop_broadcast(self, stream_id: str) -> Dict[str, Any]:
        """
        DELETE /streams/{id} at broadcaster with transient 404 retry (stream may not be ready instantly).
        """
        url = f"{self.broadcaster_api_url}/{stream_id}"

        def do_delete():
            return requests.delete(url, timeout=self.timeout)

        try:
            response = await run_in_threadpool(do_delete)
            response.raise_for_status()
            return {"message": "Stream stopped", "stream_id": stream_id}
        except Exception as e:
            logger.error(f"[Broadcaster] Stop failed: {e}")
            raise HTTPException(status_code=500, detail=f"Stop failed: {str(e)}")
