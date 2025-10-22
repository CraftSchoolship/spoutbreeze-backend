import requests
from requests import Timeout as RequestsTimeout
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from typing import Dict, Any
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
import asyncio


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
    ) -> Dict[str, Any]:
        """
        Create a broadcaster stream (blocking until broadcaster responds) and return its real stream_id.
        """
        try:
            bbb_service.is_meeting_running(
                request=IsMeetingRunningRequest(meeting_id=meeting_id)
            )

            meeting_info = bbb_service.get_meeting_info(
                request=GetMeetingInfoRequest(meeting_id=meeting_id, password=password)
            )

            plugin_manifests = [PluginManifests(url=self.plugin_manifests_url)]
            join_request = JoinMeetingRequest(
                meeting_id=meeting_id,
                password=password,
                full_name="Broadcaster Bot",
                pluginManifests=plugin_manifests,
                user_id="broadcaster_bot",
            )
            join_url = bbb_service.get_join_url(request=join_request)
            health_url = bbb_service.get_is_meeting_running_url(meeting_id)

            broadcaster_payload = BroadcasterRequest(
                bbb_health_check_url=health_url,
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
                status_code=504,
                detail=f"Broadcaster timeout after {self.timeout}s",
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Error starting broadcaster: {str(e)}"
            )

    async def fetch_status(self, stream_id: str) -> Dict[str, Any]:
        """
        Proxy GET /streams/{id} and normalize response keys.
        """
        url = f"{self.broadcaster_api_url}/{stream_id}"

        def do_get():
            return requests.get(
                url, headers={"Accept": "application/json"}, timeout=self.timeout
            )

        try:
            resp = await run_in_threadpool(do_get)
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail="Stream not found")
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Broadcaster status error ({resp.status_code}): {resp.text}",
                )
            data = resp.json()
            return {
                "stream_id": data.get("id") or stream_id,
                "status": data.get("status"),
                "pod_name": data.get("pod_name"),
                "bbb_health_check_url": data.get("bbb_health_check_url"),
                "bbb_server_url": data.get("bbb_server_url"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "streams": data.get("streams"),
                "error": data.get("error"),
            }
        except RequestsTimeout:
            raise HTTPException(
                status_code=504,
                detail=f"Broadcaster status timeout after {self.timeout}s",
            )

    async def stop_broadcast(self, stream_id: str) -> Dict[str, Any]:
        """
        DELETE /streams/{id} at broadcaster with transient 404 retry (stream may not be ready instantly).
        """
        url = f"{self.broadcaster_api_url}/{stream_id}"

        def do_delete():
            return requests.delete(
                url, headers={"Accept": "application/json"}, timeout=self.timeout
            )

        max_attempts = 3
        delay_seconds = 2

        for attempt in range(1, max_attempts + 1):
            try:
                resp = await run_in_threadpool(do_delete)
            except RequestsTimeout:
                if attempt == max_attempts:
                    raise HTTPException(
                        status_code=504,
                        detail=f"Broadcaster delete timeout after {self.timeout}s",
                    )
                await asyncio.sleep(delay_seconds)
                continue

            # Stream not yet registered -> retry
            if resp.status_code == 404:
                if attempt < max_attempts:
                    await asyncio.sleep(delay_seconds)
                    continue
                raise HTTPException(status_code=404, detail="Stream not found")

            if resp.status_code not in (200, 202, 204):
                raise HTTPException(
                    status_code=502,
                    detail=f"Broadcaster delete error ({resp.status_code}): {resp.text}",
                )

            body = {}
            try:
                if resp.text:
                    body = resp.json()
            except Exception:
                pass

            return {
                "message": body.get("message", "stream deleted successfully"),
                "stream_id": stream_id,
                "status": "stopped",
            }

        # Fallback (should not reach)
        raise HTTPException(status_code=500, detail="Unexpected delete failure path")
