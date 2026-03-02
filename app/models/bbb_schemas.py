from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    resolution: str | None = Field(
        default=None,
        description="Requested stream resolution (e.g. 360p, 480p, 720p, 1080p, 1440p, 4K)",
    )


class PluginManifests(BaseModel):
    url: str


# BBB related request/response models (trimmed to what is currently used)
class CreateMeetingRequest(BaseModel):
    name: str
    meeting_id: str | None = None
    record_id: str | None = None
    attendee_pw: str | None = None
    moderator_pw: str | None = None
    welcome: str | None = None
    max_participants: int | None = None
    duration: int | None = None
    record: bool | None = None
    auto_start_recording: bool | None = None
    allow_start_stop_recording: bool | None = None
    moderator_only_message: str | None = None
    logo_url: str | None = None
    pluginManifests: list[PluginManifests] | None = None

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
    full_name: str | None = None
    password: str
    user_id: str | None = None
    redirect: bool | None = True
    pluginManifests: list[PluginManifests] | None = None

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
    pluginManifests: list[PluginManifests] | None = None

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
    pluginManifests: list[PluginManifests] | None = None

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
    userID: str | None = None
    fullName: str | None = None
    role: str | None = None
    isPresenter: bool | None = None
    isListeningOnly: bool | None = None
    hasJoinedVoice: bool | None = None
    hasVideo: bool | None = None
    clientType: str | None = None


class Meeting(BaseModel):
    meetingID: str
    meetingName: str
    createTime: str | None = None
    createDate: str | None = None
    voiceBridge: str | None = None
    dialNumber: str | None = None
    attendeePW: str | None = None
    moderatorPW: str | None = None
    running: bool | None = None
    duration: int | None = None
    hasUserJoined: bool | None = None
    recording: bool | None = None
    hasBeenForciblyEnded: bool | None = None
    startTime: int | None = None
    endTime: int | None = None
    participantCount: int | None = None
    listenerCount: int | None = None
    voiceParticipantCount: int | None = None
    videoCount: int | None = None
    maxUsers: int | None = None
    moderatorCount: int | None = None
    attendees: list[MeetingAttendee] | None = None


class BroadcasterStreamInfo(BaseModel):
    stream_id: str
    pod_name: str | None = None
    status: str
    created_at: str | None = None


class StartBroadcastResponse(BaseModel):
    status: str
    message: str
    join_url: str
    stream: BroadcasterStreamInfo
    meeting_info: dict[str, Any]


class BroadcastStatusResponse(BaseModel):
    stream_id: str
    status: str
    pod_name: str | None = None
    bbb_health_check_url: str | None = None
    bbb_server_url: str | None = None
    created_at: str | None = None
    streams: list[StreamConfig] | None = None
    video_bitrate: str | None = None
    audio_bitrate: str | None = None
    fps: int | None = None
    resolution: str | None = None
