from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any


class BroadcasterRequest(BaseModel):
    """
    Broadcaster request sent to external broadcaster service
    """
    bbb_health_check_url: str
    bbb_server_url: str
    stream: "StreamConfig"


class StreamConfig(BaseModel):
    platform: str
    rtmp_url: str
    stream_key: str


class BroadcasterRobot(BaseModel):
    meeting_id: str
    rtmp_url: str
    stream_key: str
    password: str
    platform: str


class BroadcasterResponse(BaseModel):
    """
    Response model from the broadcaster service
    """

    status: str
    message: str
    details: Optional[Dict[str, Any]] = None


class PluginManifests(BaseModel):
    """
    Model for the plugin
    """

    url: str


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
    """
    Model for an attendee in a BBB meeting
    """

    userID: Optional[str] = None
    fullName: Optional[str] = None
    role: Optional[str] = None
    isPresenter: Optional[bool] = None
    isListeningOnly: Optional[bool] = None
    hasJoinedVoice: Optional[bool] = None
    hasVideo: Optional[bool] = None
    clientType: Optional[str] = None


class Meeting(BaseModel):
    """
    Model for a BBB meeting
    """

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
