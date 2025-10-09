from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime

from app.models.bbb_schemas import JoinMeetingRequest
from app.models.user_models import User
from app.models.event.event_models import Event, EventStatus
from app.models.event.event_schemas import EventCreate, EventUpdate, EventResponse
from app.models.channel.channels_schemas import ChannelCreate, ChannelResponse
from app.services.channels_service import ChannelsService
from app.services.bbb_service import BBBService
from app.utils.event_helpers import EventHelpers


from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from sqlalchemy.orm import selectinload
from app.config.logger_config import logger
from app.config.settings import get_settings
import secrets


class EventService:
    """
    Service class for managing events
    """

    def __init__(self):
        self.bbb_service = BBBService()
        self.channel_service = ChannelsService()
        self.event_helpers = EventHelpers()
        self.settings = get_settings()
        self.plugin_manifests_url = self.settings.plugin_manifests_url

    def _create_event_response(self, event: Event) -> EventResponse:
        """
        Create an EventResponse with creator information from an Event model.
        """
        # Create organizers list
        organizers_list = []
        for organizer in event.organizers:
            if organizer is not None:
                organizers_list.append(
                    {
                        "id": organizer.id,
                        "username": organizer.username,
                        "email": organizer.email,
                        "first_name": organizer.first_name,
                        "last_name": organizer.last_name,
                    }
                )

        event_dict = {
            "id": event.id,
            "title": event.title,
            "description": event.description,
            "occurs": event.occurs,
            "start_date": event.start_date,
            "end_date": event.end_date,
            "start_time": event.start_time,
            "timezone": event.timezone,
            "creator_id": event.creator_id,
            "creator_first_name": event.creator.first_name,
            "creator_last_name": event.creator.last_name,
            "organizers": organizers_list,
            "channel_id": event.channel_id,
            "meeting_id": event.meeting_id,
            "attendee_pw": event.attendee_pw,
            "moderator_pw": event.moderator_pw,
            "created_at": event.created_at,
            "updated_at": event.updated_at,
            "meeting_created": event.meeting_created,
            "status": event.status,
            "actual_start_time": event.actual_start_time,
            "actual_end_time": event.actual_end_time,
        }

        return EventResponse.model_validate(event_dict)

    async def create_event(
        self,
        db: AsyncSession,
        event: EventCreate,
        user_id: UUID,
    ) -> EventResponse:
        """
        Create a new event.
        """
        try:
            # Check if event title already exists
            existing_event_result = await db.execute(
                select(Event).where(Event.title == event.title)
            )
            existing_event = existing_event_result.scalars().first()
            if existing_event:
                raise ValueError(f"Event with title '{event.title}' already exists.")

            channel = await self._get_or_create_channel(
                db=db,
                channel_name=event.channel_name,
                user_id=user_id,
            )

            # Create the event
            new_event = self.event_helpers.prepare_event_data(
                event=event,
                user_id=user_id,
                channel_id=channel.id,
            )

            # Collect organizers first if provided
            organizers = []
            if hasattr(event, "organizer_ids") and event.organizer_ids:
                for organizer_id in event.organizer_ids:
                    # Check if the user exists
                    result = await db.execute(
                        select(User).where(User.id == organizer_id)
                    )
                    organizer = result.scalars().first()
                    if not organizer:
                        raise ValueError(f"User with ID {organizer_id} does not exist.")
                    organizers.append(organizer)

            # Add the new event to the session
            db.add(new_event)
            # Flush to get the new event ID
            await db.flush()
            # Refresh the new event to get the ID and other defaults
            await db.refresh(new_event)
            await db.commit()

            # Generate unique meeting ID and passwords
            unique_meeting_id = new_event.title.replace(" ", "_")[:32]
            random_suffix = secrets.token_hex(4)
            unique_meeting_id = f"{unique_meeting_id}_{random_suffix}"
            moderator_pw = secrets.token_urlsafe(8)
            attendee_pw = secrets.token_urlsafe(8)

            # Set the meeting ID and passwords
            new_event.meeting_id = unique_meeting_id
            new_event.moderator_pw = moderator_pw
            new_event.attendee_pw = attendee_pw
            new_event.meeting_created = False

            if organizers:
                # Load the event with the organizers relationship
                result = await db.execute(
                    select(Event)
                    .options(selectinload(Event.organizers))
                    .where(Event.id == new_event.id)
                )
                event_with_organizers = result.scalars().first()
                if event_with_organizers:
                    event_with_organizers.organizers.extend(organizers)
                    await db.commit()

            # Refresh the event with eager loading of relationships
            result = await db.execute(
                select(Event)
                .options(
                    selectinload(Event.organizers),
                    selectinload(Event.channel),
                    selectinload(Event.creator),
                )
                .where(Event.id == new_event.id)
            )
            refreshed_event = result.scalars().first()

            if not refreshed_event:
                raise ValueError("Failed to retrieve created event")

            logger.info(
                f"Event {refreshed_event.title} created for user {user_id} in channel {channel.name}"
            )

            logger.info(f"User with ID {user_id} created event {refreshed_event.title}")

            event_response = self._create_event_response(refreshed_event)
            return event_response
        except Exception as e:
            logger.error(f"Error creating event: {e}")
            await db.rollback()
            raise

    async def start_event(
        self,
        db: AsyncSession,
        event_id: UUID,
        user_id: UUID,
    ) -> Dict[str, str]:
        """
        Start an event by ID.
        """
        try:
            # Check if the event exists
            select_stmt = (
                select(Event)
                .options(
                    selectinload(Event.organizers),
                    selectinload(Event.channel),
                    selectinload(Event.creator),
                )
                .where(Event.id == event_id, Event.creator_id == user_id)
            )
            result = await db.execute(select_stmt)
            event = result.scalars().first()
            if not event:
                raise ValueError(
                    f"Event with ID {event_id} does not exist or does not belong to user {user_id}."
                )

            if not event.meeting_created:
                # Create the meeting in BBB
                await self._create_bbb_meeting(
                    db=db,
                    event=event,  # event is an Event instance; _create_bbb_meeting accepts EventCreate but tolerates current usage
                    new_event=event,
                    user_id=user_id,
                )

                # Update the event with meeting details
                event.meeting_created = True
                event.status = EventStatus.LIVE
                event.actual_start_time = datetime.now()

                await db.commit()
                # Re-load with relationships to avoid async lazy-load (MissingGreenlet) on event.channel
                result_after = await db.execute(
                    select(Event)
                    .options(
                        selectinload(Event.organizers),
                        selectinload(Event.channel),
                        selectinload(Event.creator),
                    )
                    .where(Event.id == event.id)
                )
                reloaded = result_after.scalars().first()
                if reloaded is None:
                    raise ValueError("Failed to reload event after meeting creation.")
                event = reloaded  # Now guaranteed non-None for type checker

                logger.info(
                    f"Event {event.title} started for user {user_id} in channel "
                    f"{event.channel.name if getattr(event, 'channel', None) else 'unknown'}"
                )

            if not event.meeting_id or not event.moderator_pw:
                raise ValueError(
                    f"Event with ID {event_id} does not have a valid meeting ID or moderator password."
                )

            join_request = JoinMeetingRequest(
                meeting_id=event.meeting_id,
                password=event.moderator_pw,
                full_name=event.creator.first_name,
            )
            join_url = self.bbb_service.get_join_url(request=join_request)
            logger.info(f"Join URL for event {event.title} is {join_url}")
            return {"join_url": join_url}
        except Exception as e:
            logger.error(f"Error starting event with ID {event_id}: {e}")
            await db.rollback()
            raise

    async def end_event(
        self,
        db: AsyncSession,
        event_id: UUID,
        user_id: UUID,
    ) -> Dict[str, str]:
        """
        End an event by ID.
        """
        try:
            # Get the event
            select_stmt = (
                select(Event)
                .options(selectinload(Event.creator))
                .where(Event.id == event_id, Event.creator_id == user_id)
            )
            result = await db.execute(select_stmt)
            event = result.scalars().first()

            if not event:
                raise ValueError(
                    f"Event with ID {event_id} does not exist or you don't have permission."
                )

            if event.status != EventStatus.LIVE:
                raise ValueError("Event is not currently live.")

            # End the BBB meeting if it exists
            if event.meeting_id and event.moderator_pw:
                from app.models.bbb_schemas import EndMeetingRequest

                end_request = EndMeetingRequest(
                    meeting_id=event.meeting_id, password=event.moderator_pw
                )
                await self.bbb_service.end_meeting(request=end_request, db=db)

            # Update event status
            event.status = EventStatus.ENDED
            event.actual_end_time = datetime.now()

            await db.commit()
            logger.info(f"Event {event.title} ended at {event.actual_end_time}")

            return {"message": "Event ended successfully"}

        except Exception as e:
            logger.error(f"Error ending event with ID {event_id}: {e}")
            await db.rollback()
            raise

    async def get_events_by_status(
        self,
        db: AsyncSession,
        status: EventStatus,
        user_id: Optional[UUID] = None,
    ) -> List[EventResponse]:
        """
        Get events by status, optionally filtered by user.
        """
        try:
            query = (
                select(Event)
                .options(
                    selectinload(Event.organizers),
                    selectinload(Event.channel),
                    selectinload(Event.creator),
                )
                .where(Event.status == status)
            )

            if user_id:
                query = query.where(Event.creator_id == user_id)

            result = await db.execute(query)
            events = result.scalars().all()

            event_responses = [self._create_event_response(event) for event in events]

            logger.info(
                f"Retrieved {len(event_responses)} events with status {status.value}"
            )
            return event_responses

        except Exception as e:
            logger.error(f"Error retrieving events with status {status.value}: {e}")
            raise

    async def get_upcoming_events(
        self, db: AsyncSession, user_id: Optional[UUID] = None
    ) -> List[EventResponse]:
        """Get upcoming events (scheduled status)."""
        return await self.get_events_by_status(db, EventStatus.SCHEDULED, user_id)

    async def get_past_events(
        self, db: AsyncSession, user_id: Optional[UUID] = None
    ) -> List[EventResponse]:
        """Get past events (ended status)."""
        return await self.get_events_by_status(db, EventStatus.ENDED, user_id)

    async def get_live_events(
        self, db: AsyncSession, user_id: Optional[UUID] = None
    ) -> List[EventResponse]:
        """Get currently live events."""
        return await self.get_events_by_status(db, EventStatus.LIVE, user_id)

    async def join_event(
        self,
        db: AsyncSession,
        event_id: UUID,
        user_id: Optional[UUID] = None,
        full_name: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Join an event by ID.
        """
        try:
            # Check if the event exists
            select_stmt = (
                select(Event)
                .options(
                    selectinload(Event.organizers),
                    selectinload(Event.channel),
                    selectinload(Event.creator),
                )
                .where(Event.id == event_id)
            )
            result = await db.execute(select_stmt)
            event = result.scalars().first()
            if not event:
                raise ValueError(f"Event with ID {event_id} does not exist.")

            if not event.meeting_id or not event.attendee_pw or not event.moderator_pw:
                raise ValueError(
                    f"Event with ID {event_id} does not have a valid meeting ID or passwords."
                )

            attendee_join_request = JoinMeetingRequest(
                meeting_id=event.meeting_id,
                password=event.attendee_pw,
                full_name=full_name,
            )
            moderator_join_request = JoinMeetingRequest(
                meeting_id=event.meeting_id,
                password=event.moderator_pw,
                full_name=full_name,
            )

            attendee_join_url = self.bbb_service.get_join_url(
                request=attendee_join_request
            )
            moderator_join_url = self.bbb_service.get_join_url(
                request=moderator_join_request
            )
            return {
                "attendee_join_url": attendee_join_url,
                "moderator_join_url": moderator_join_url,
            }
        except Exception as e:
            logger.error(f"Error joining event with ID {event_id}: {e}")
            raise

    async def get_event_by_id(
        self,
        db: AsyncSession,
        event_id: UUID,
    ) -> EventResponse:
        """
        Get an event by ID.
        """
        try:
            result = await db.execute(
                select(Event)
                .options(
                    selectinload(Event.organizers),
                    selectinload(Event.channel),
                    selectinload(Event.creator),
                )
                .where(Event.id == event_id)
            )
            event = result.scalars().first()

            if not event:
                raise ValueError(f"Event with ID {event_id} does not exist.")

            logger.info(f"Event retrieved with ID {event_id}")
            return self._create_event_response(event)
        except Exception as e:
            logger.error(f"Error retrieving event with ID {event_id}: {e}")
            raise

    async def get_all_events(
        self,
        db: AsyncSession,
    ) -> List[EventResponse]:
        """
        Get all events.
        """
        try:
            result = await db.execute(
                select(Event).options(
                    selectinload(Event.organizers),
                    selectinload(Event.channel),
                    selectinload(Event.creator),
                )
            )
            events = result.scalars().all()

            if not events:
                raise ValueError("No events found.")

            event_responses = [self._create_event_response(event) for event in events]

            logger.info(f"Retrieved {len(event_responses)} events")
            return event_responses
        except Exception as e:
            logger.error(f"Error retrieving events: {e}")
            raise

    async def get_events_by_channel_id(
        self,
        db: AsyncSession,
        channel_id: UUID,
    ) -> List[EventResponse]:
        """
        Get events by channel ID.
        """
        try:
            # Check if the channel exists
            channel = await self.channel_service.get_channel_by_id(
                db=db,
                channel_id=channel_id,
            )
            if not channel:
                raise ValueError(f"Channel with ID {channel_id} does not exist.")

            result = await db.execute(
                select(Event)
                .options(
                    selectinload(Event.organizers),
                    selectinload(Event.channel),
                    selectinload(Event.creator),
                )
                .where(
                    Event.channel_id == channel_id,
                    Event.status.in_([EventStatus.SCHEDULED, EventStatus.LIVE]),
                )
            )
            events = result.scalars().all()

            if not events:
                # 404 Not Found
                raise ValueError(f"No events found for channel ID {channel_id}.")

            event_responses = [self._create_event_response(event) for event in events]

            logger.info(
                f"Retrieved {len(event_responses)} events for channel ID {channel_id}"
            )
            return event_responses
        except Exception as e:
            logger.error(f"Error retrieving events for channel ID {channel_id}: {e}")
            raise

    async def update_event(
        self,
        db: AsyncSession,
        event_id: UUID,
        event_update: EventUpdate,
        user_id: UUID,
    ) -> EventResponse:
        """
        Update an event by ID.
        """
        # Check if the event exists
        select_stmt = (
            select(Event)
            .options(
                selectinload(Event.organizers),
                selectinload(Event.channel),
                selectinload(Event.creator),
            )
            .where(Event.id == event_id, Event.creator_id == user_id)
        )
        result = await db.execute(select_stmt)
        event = result.scalars().first()
        if not event:
            raise ValueError(
                f"Event with ID {event_id} does not exist or does not belong to user {user_id}."
            )

        # Update the event
        try:
            # Handle basic fields
            update_data = {}
            for field, value in event_update.model_dump().items():
                if (
                    value is not None and field != "organizer_ids"
                ):  # Skip organizer_ids for direct update
                    update_data[field] = value

            if update_data:  # Only update if there are fields to update
                update_stmt = (
                    update(Event).where(Event.id == event_id).values(**update_data)
                )
                await db.execute(update_stmt)

            # Handle organizers separately if provided
            if event_update.organizer_ids is not None:
                # Clear existing organizers
                event.organizers = []

                # Add new organizers
                for organizer_id in event_update.organizer_ids:
                    # Verify user exists
                    organizer_result = await db.execute(
                        select(User).where(User.id == organizer_id)
                    )
                    organizer = organizer_result.scalars().first()
                    if organizer:
                        event.organizers.append(organizer)
                    else:
                        logger.warning(
                            f"User with ID {organizer_id} not found when updating event organizers"
                        )

            await db.commit()
            await db.refresh(event)
            return self._create_event_response(event)
        except Exception as e:
            logger.error(f"Error updating event with ID {event_id}: {e}")
            await db.rollback()
            raise

    async def delete_event(
        self,
        db: AsyncSession,
        event_id: UUID,
        user_id: UUID,
    ) -> bool:
        """
        Delete an event by ID.
        """
        try:
            # Check if the event exists and belongs to the user
            select_stmt = select(Event).where(
                Event.id == event_id, Event.creator_id == user_id
            )
            result = await db.execute(select_stmt)
            event = result.scalars().first()
            if not event:
                raise ValueError(
                    f"Event with ID {event_id} does not exist or does not belong to user {user_id}."
                )

            # Delete the event
            delete_stmt = delete(Event).where(Event.id == event_id)
            await db.execute(delete_stmt)
            await db.commit()

            logger.info(f"Event with ID {event_id} deleted for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting event with ID {event_id}: {e}")
            await db.rollback()
            raise

    async def _get_or_create_channel(
        self,
        db: AsyncSession,
        channel_name: str,
        user_id: UUID,
    ) -> ChannelResponse:
        """
        Get or create a channel.
        """
        channel = await self.channel_service.get_channel_by_name(
            db=db, channel_name=channel_name, user_id=user_id
        )
        if not channel:
            channel_create = ChannelCreate(name=channel_name)
            channel = await self.channel_service.create_channel(
                db=db, channel_create=channel_create, user_id=user_id
            )
        return channel

    async def _create_bbb_meeting(
        self,
        db: AsyncSession,
        event: EventCreate,
        new_event: Event,
        user_id: UUID,
    ) -> Dict[str, Any]:
        """
        Create a BBB meeting for the event.
        """
        # Create the meeting in BBB
        meeting_request = self.event_helpers.prepare_bbb_meeting_request(
            event=event,
            new_event=new_event,
            plugin_manifests=self.plugin_manifests_url,
        )

        bbb_meeting = await self.bbb_service.create_meeting(
            request=meeting_request,
            user_id=user_id,
            db=db,
            event_id=new_event.id,
        )

        return bbb_meeting
