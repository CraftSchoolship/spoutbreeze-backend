import requests
from fastapi import HTTPException
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


class BroadcasterService:
    def __init__(self):
        self.settings = get_settings()
        self.broadcaster_api_url = self.settings.broadcaster_api_url
        self.plugin_manifests_url = self.settings.plugin_manifests_url

    async def start_broadcasting(
        self,
        meeting_id: str,
        rtmp_url: str,
        stream_key: str,
        password: str,
        platform: str,
        bbb_service: BBBService,
    ) -> Dict[str, Any]:
        """Start broadcasting a BBB meeting to RTMP/other platforms."""
        try:
            is_running_request = IsMeetingRunningRequest(meeting_id=meeting_id)
            is_running = bbb_service.is_meeting_running(request=is_running_request)

            # (Optional) enforce running state if needed
            # if is_running.get("running", "false").lower() != "true":
            #     raise HTTPException(status_code=400, detail="Meeting is not running")

            meeting_info_request = GetMeetingInfoRequest(
                meeting_id=meeting_id, password=password
            )
            meeting_info = bbb_service.get_meeting_info(request=meeting_info_request)

            plugin_manifests = [
                PluginManifests(url=f"{self.plugin_manifests_url}/manifest.json")
            ]
            join_request = JoinMeetingRequest(
                meeting_id=meeting_id,
                password=password,
                full_name="Broadcaster Bot",
                pluginManifests=plugin_manifests,
                user_id="broadcaster_bot",
            )
            join_url = bbb_service.get_join_url(request=join_request)

            is_meeting_running_url = bbb_service.get_is_meeting_running_url(meeting_id)

            broadcaster_response = await self._call_broadcaster_service(
                is_meeting_running_url=is_meeting_running_url,
                join_url=join_url,
                rtmp_url=rtmp_url,
                stream_key=stream_key,
                platform=platform,
            )

            return {
                "status": "success",
                "message": "Broadcaster started successfully",
                "join_url": join_url,
                "broadcaster_response": broadcaster_response,
                "meeting_info": meeting_info,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Error in broadcaster: {str(e)}"
            )

    async def _call_broadcaster_service(
        self,
        is_meeting_running_url: str,
        join_url: str,
        rtmp_url: str,
        stream_key: str,
        platform: str,
    ) -> Dict[str, Any]:
        """
        Call the external broadcaster service with new payload format.
        """
        try:
            payload = BroadcasterRequest(
                bbb_health_check_url=is_meeting_running_url,
                bbb_server_url=join_url,
                stream=StreamConfig(
                    platform=platform,
                    rtmp_url=rtmp_url,
                    stream_key=stream_key,
                ),
            )

            response = requests.post(
                self.broadcaster_api_url,
                json=payload.model_dump(),
                headers={
                    "Content-Type": "application/json",
                    "accept": "application/json",
                },
                timeout=15,
            )

            if response.status_code != 200:
                return {
                    "status": "error",
                    "message": f"Broadcaster service returned status code: {response.status_code}",
                    "details": response.text,
                }

            return response.json()
        except Exception as e:
            return {
                "status": "error",
                "message": f"Error calling broadcaster service: {str(e)}",
            }
