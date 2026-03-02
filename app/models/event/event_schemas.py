from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.event.event_models import EventStatus


class EventBase(BaseModel):
    """
    Base model for event
    """

    title: str
    description: str | None = None
    occurs: str
    start_date: datetime
    end_date: datetime
    start_time: datetime
    timezone: str = "UTC"


class EventCreate(EventBase):
    """
    Create model for event
    """

    organizer_ids: list[UUID] | None = []
    channel_name: str


class EventUpdate(BaseModel):
    """
    Update model for event
    """

    title: str | None = None
    description: str | None = None
    occurs: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    start_time: datetime | None = None
    organizer_ids: list[UUID] | None = None
    channel_id: UUID | None = None
    timezone: str | None = None


class OrganizerResponse(BaseModel):
    """
    Response model for organizer
    """

    id: UUID
    username: str
    email: str
    first_name: str
    last_name: str

    model_config = ConfigDict(from_attributes=True)


class EventResponse(EventBase):
    """
    Response model for event
    """

    id: UUID
    creator_id: UUID
    creator_first_name: str
    creator_last_name: str
    organizers: list[OrganizerResponse] = []
    channel_id: UUID
    meeting_id: str | None = None
    attendee_pw: str | None = None
    moderator_pw: str | None = None
    meeting_created: bool
    timezone: str
    created_at: datetime
    updated_at: datetime
    status: EventStatus
    actual_start_time: datetime | None = None
    actual_end_time: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class EventListResponse(BaseModel):
    """
    List response model for event
    """

    events: list[EventResponse]
    total: int

    model_config = ConfigDict(from_attributes=True)


class JoinEventRequest(BaseModel):
    full_name: str
