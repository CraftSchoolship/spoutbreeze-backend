from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any


# Payload sent to external broadcaster
class BroadcasterRequest(BaseModel):
    bbb_server_url: str
    close_popups: bool = True
    fps: int = 16
    is_basic_plan: bool = True
    resolution: str = "1080p"
    stream: "StreamConfig"


class StreamConfig(BaseModel):
    platform: str
    rtmp_url: str
    stream_key: str


# Request body accepted by our API to start a broadcast
class BroadcasterRobot(BaseModel):
    meeting_id: str
    rtmp_url: str
    stream_key: str
    password: str
    platform: str


class PluginManifests(BaseModel):
    url: str


# BBB related request/response models (trimmed to what is currently used)
class CreateMeetingRequest(BaseModel):
    name: str
    meeting_id: Optional[str] = None
    record_id: Optional[str] = None
    attendee_pw: Optional[str] = None
    moderator_pw: Optional[str] = None
    welcome: Optional[str] = None
    max_participants: Optional[int] = None
    duration: Optional[int] = None
    record: Optional[bool] = None
    auto_start_recording: Optional[bool] = None
    allow_start_stop_recording: Optional[bool] = None
    moderator_only_message: Optional[str] = None
    logo_url: Optional[str] = None
    pluginManifests: Optional[List[PluginManifests]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Test Meeting",
                "meeting_id": "test-meeting-123",
                "record_id": "record-123",
                "attendee_pw": "attendPW",
                "moderator_pw": "modPW",
                "welcome": "Welcome to the meeting!",
                "max_participants": 100,
                "duration": 60,
                "record": True,
                "auto_start_recording": False,
                "allow_start_stop_recording": True,
                "moderator_only_message": "This is a private message for moderators.",
                "logo_url": "https://avatars.githubusercontent.com/u/77354007?v=4",
                "pluginManifests": [{"url": "http://example.com/manifest.json"}],
            }
        }
    )


class JoinMeetingRequest(BaseModel):
    meeting_id: str
    full_name: Optional[str] = None
    password: str
    user_id: Optional[str] = None
    redirect: Optional[bool] = True
    pluginManifests: Optional[List[PluginManifests]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "meeting_id": "test-meeting-123",
                "full_name": "John Doe",
                "password": "modPW",
                "user_id": "user-123",
                "redirect": False,
                "PluginManifests": [{"url": "http://example.com/manifest.json"}],
            }
        }
    )


class EndMeetingRequest(BaseModel):
    meeting_id: str
    password: str
    pluginManifests: Optional[List[PluginManifests]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "meeting_id": "test-meeting-123",
                "password": "modPW",
                "pluginManifests": [{"url": "http://example.com/manifest.json"}],
            }
        }
    )


class GetMeetingInfoRequest(BaseModel):
    meeting_id: str
    password: str
    # pluginManifests: Optional[List[PluginManifests]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "meeting_id": "test-meeting-123",
                "password": "modPW",
                # "pluginManifests": [{"url": "http://example.com/manifest.json"}]
            }
        }
    )


class IsMeetingRunningRequest(BaseModel):
    meeting_id: str
    pluginManifests: Optional[List[PluginManifests]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "meeting_id": "test-meeting-123",
                "PluginManifests": [{"url": "http://example.com/manifest.json"}],
            }
        }
    )


class GetRecordingRequest(BaseModel):
    meeting_id: str


class MeetingAttendee(BaseModel):
    userID: Optional[str] = None
    fullName: Optional[str] = None
    role: Optional[str] = None
    isPresenter: Optional[bool] = None
    isListeningOnly: Optional[bool] = None
    hasJoinedVoice: Optional[bool] = None
    hasVideo: Optional[bool] = None
    clientType: Optional[str] = None


class Meeting(BaseModel):
    meetingID: str
    meetingName: str
    createTime: Optional[str] = None
    createDate: Optional[str] = None
    voiceBridge: Optional[str] = None
    dialNumber: Optional[str] = None
    attendeePW: Optional[str] = None
    moderatorPW: Optional[str] = None
    running: Optional[bool] = None
    duration: Optional[int] = None
    hasUserJoined: Optional[bool] = None
    recording: Optional[bool] = None
    hasBeenForciblyEnded: Optional[bool] = None
    startTime: Optional[int] = None
    endTime: Optional[int] = None
    participantCount: Optional[int] = None
    listenerCount: Optional[int] = None
    voiceParticipantCount: Optional[int] = None
    videoCount: Optional[int] = None
    maxUsers: Optional[int] = None
    moderatorCount: Optional[int] = None
    attendees: Optional[List[MeetingAttendee]] = None


class BroadcasterStreamInfo(BaseModel):
    stream_id: str
    pod_name: Optional[str] = None
    status: str
    created_at: Optional[str] = None


class StartBroadcastResponse(BaseModel):
    status: str
    message: str
    join_url: str
    stream: BroadcasterStreamInfo
    meeting_info: Dict[str, Any]


class BroadcastStatusResponse(BaseModel):
    stream_id: str
    status: str
    pod_name: Optional[str] = None
    bbb_health_check_url: Optional[str] = None
    bbb_server_url: Optional[str] = None
    created_at: Optional[str] = None
    streams: Optional[List[StreamConfig]] = None
    video_bitrate: Optional[str] = None
    audio_bitrate: Optional[str] = None
    fps: Optional[int] = None
    resolution: Optional[str] = None
