from uuid import UUID
from datetime import datetime, timezone
from typing import Optional

from app.models.event.event_models import Event
from app.models.event.event_schemas import EventCreate
from app.models.bbb_schemas import CreateMeetingRequest, PluginManifests


class EventHelpers:
    """
    Helper class for managing events
    """

    @staticmethod
    def prepare_event_data(
        event: EventCreate,
        user_id: UUID,
        channel_id: UUID,
    ) -> Event:
        """
        Prepare event data for creation.
        """
        # Ensure all dates are timezone-aware
        start_date = EventHelpers._ensure_timezone_aware(event.start_date)
        end_date = EventHelpers._ensure_timezone_aware(event.end_date)
        start_time = EventHelpers._ensure_timezone_aware(event.start_time)

        # Prepare the event data
        new_event = Event(
            title=event.title,
            description=event.description,
            occurs=event.occurs,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            timezone=event.timezone,
            creator_id=user_id,
            channel_id=channel_id,
        )
        return new_event

    @staticmethod
    def prepare_bbb_meeting_request(
        event: EventCreate,
        new_event: Event,
        plugin_manifests: Optional[str] = None,
    ) -> CreateMeetingRequest:
        """
        Prepare BBB meeting request data.
        """
        # Create the meeting request
        meeting_request = CreateMeetingRequest(
            name=event.title,
            meeting_id=new_event.meeting_id,
            attendee_pw=new_event.attendee_pw,
            moderator_pw=new_event.moderator_pw,
            welcome=f"Welcome to {event.title}",
            record=True,
            allow_start_stop_recording=True,
            pluginManifests=(
                [PluginManifests(url=plugin_manifests)] if plugin_manifests else None
            ),
        )
        return meeting_request

    @staticmethod
    def _ensure_timezone_aware(
        dt: datetime,
    ) -> datetime:
        """
        Ensure the given datetime is timezone-aware.
        """
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
