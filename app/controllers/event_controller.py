from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict
from uuid import UUID
from sqlalchemy import select
from app.models.event.event_models import Event
from app.services.chat_context import set_user_mapping

from app.config.database.session import get_db
from app.controllers.user_controller import get_current_user
from app.models.user_models import User
from app.models.event.event_schemas import (
    EventCreate,
    EventResponse,
    EventListResponse,
    EventUpdate,
    JoinEventRequest,
)
from app.services.cached.event_service_cached import EventServiceCached
from app.services.bbb_service import BBBService

router = APIRouter(prefix="/api/events", tags=["Events"])
event_service = EventServiceCached()
bbb_service = BBBService()


@router.post("/", response_model=EventResponse)
async def create_event(
    event_create: EventCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EventResponse:
    """
    Create a new event for the current user.

    Args:
        event_create: The event to create.
        db: The database session.
        current_user: The current user.

    Returns:
        The created event.
    """
    try:
        new_event = await event_service.create_event(
            db=db,
            event=event_create,
            user_id=UUID(str(current_user.id)),
        )
        # Convert SQLAlchemy model to Pydantic model
        return EventResponse.model_validate(new_event.__dict__)
    except ValueError as e:
        # Handle the case where the event creation fails due to validation errors
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException as e:
        # Handle the case where the event creation fails due to HTTP errors
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{event_id}/start", response_model=Dict[str, str])
async def start_event(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, str]:
    """
    Start an event by ID for the current user.
    Args:
        event_id: The ID of the event to start.
        db: The database session.
        current_user: The current user.
    Returns:
        A message indicating the result of the start operation.
    """
    try:
        start_result = await event_service.start_event(
            db=db,
            event_id=event_id,
            user_id=UUID(str(current_user.id)),
        )
        if start_result:
            return start_result
        else:
            raise HTTPException(status_code=400, detail="Failed to start event")
    except ValueError as e:
        # Handle the case where the event ID is not found
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # Handle any other exceptions
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{event_id}/join-url", response_model=Dict[str, str])
async def join_event(
    event_id: UUID,
    request: JoinEventRequest,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, str]:
    """Get the join URL for an event by ID."""
    try:
        # Get the event to find the creator/organizer

        result = await db.execute(select(Event).where(Event.id == event_id))
        event = result.scalars().first()

        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        join_result = await event_service.join_event(
            db=db,
            event_id=event_id,
            full_name=request.full_name,
        )

        if join_result:
            # Set the meeting->user mapping for chat gateway
            # Use the event creator's id so chat works
            if event.meeting_id and event.creator_id:
                await set_user_mapping(
                    meeting_id=event.meeting_id,
                    user_id=str(event.creator_id),
                    ttl=86400,
                )
            return join_result
        else:
            raise HTTPException(status_code=400, detail="Failed to join event")
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/upcoming", response_model=EventListResponse)
async def get_upcoming_events(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EventListResponse:
    """Get upcoming events for the current user."""
    try:
        events = await event_service.get_upcoming_events(db=db, user_id=current_user.id)
        # Convert SQLAlchemy models to Pydantic models
        event_responses = [
            EventResponse.model_validate(event.__dict__) for event in events
        ]
        return EventListResponse(events=event_responses, total=len(event_responses))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/past", response_model=EventListResponse)
async def get_past_events(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EventListResponse:
    """Get past events for the current user."""
    try:
        events = await event_service.get_past_events(db=db, user_id=current_user.id)
        # Convert SQLAlchemy models to Pydantic models
        event_responses = [
            EventResponse.model_validate(event.__dict__) for event in events
        ]
        return EventListResponse(events=event_responses, total=len(event_responses))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/live", response_model=EventListResponse)
async def get_live_events(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EventListResponse:
    """Get currently live events for the current user."""
    try:
        events = await event_service.get_live_events(db=db, user_id=current_user.id)
        # Convert SQLAlchemy models to Pydantic models
        event_responses = [
            EventResponse.model_validate(event.__dict__) for event in events
        ]
        return EventListResponse(events=event_responses, total=len(event_responses))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{event_id}/end", response_model=Dict[str, str])
async def end_event(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, str]:
    """End an event by ID."""
    try:
        result = await event_service.end_event(
            db=db,
            event_id=event_id,
            user_id=UUID(str(current_user.id)),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/all", response_model=EventListResponse)
async def get_all_events(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EventListResponse:
    """
    Get all events for the current user.

    Args:
        db: The database session.
        current_user: The current user.

    Returns:
        A list of events.
    """
    try:
        events = await event_service.get_all_events(db=db)
        # Convert SQLAlchemy models to Pydantic models
        event_responses = [
            EventResponse.model_validate(event.__dict__) for event in events
        ]
        return EventListResponse(events=event_responses, total=len(event_responses))
    except ValueError as e:
        # Handle the case where no events are found
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EventResponse:
    """
    Get an event by ID for the current user.

    Args:
        event_id: The ID of the event to retrieve.
        db: The database session.
        current_user: The current user.

    Returns:
        The event with the specified ID.
    """
    try:
        event = await event_service.get_event_by_id(
            db=db,
            event_id=event_id,
        )
        # Convert SQLAlchemy model to Pydantic model
        return EventResponse.model_validate(event.__dict__)
    except ValueError as e:
        # Handle the case where the event ID is not found
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Get event by channel ID
@router.get("/channel/{channel_id}", response_model=EventListResponse)
async def get_events_by_channel(
    channel_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EventListResponse:
    """
    Get all events for a specific channel.

    Args:
        channel_id: The ID of the channel.
        db: The database session.
        current_user: The current user.

    Returns:
        A list of events for the specified channel.
    """
    try:
        events = await event_service.get_events_by_channel_id(
            db=db,
            channel_id=channel_id,
        )
        # Convert SQLAlchemy models to Pydantic models
        event_responses = [
            EventResponse.model_validate(event.__dict__) for event in events
        ]
        return EventListResponse(events=event_responses, total=len(event_responses))
    except ValueError as e:
        # Handle the case where the channel ID is not found
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # Handle any other exceptions
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: UUID,
    event_update: EventUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EventResponse:
    """
    Update an event by ID for the current user.

    Args:
        event_id: The ID of the event to update.
        event_update: The updated event data.
        db: The database session.
        current_user: The current user.

    Returns:
        The updated event.
    """
    try:
        updated_event = await event_service.update_event(
            db=db,
            event_id=event_id,
            event_update=event_update,
            user_id=UUID(str(current_user.id)),
        )
        # Convert SQLAlchemy model to Pydantic model
        return EventResponse.model_validate(updated_event.__dict__)
    except ValueError as e:
        # Handle the case where the event ID is not found
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{event_id}", response_model=Dict[str, str])
async def delete_event(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, str]:
    """
    Delete an event by ID for the current user.

    Args:
        event_id: The ID of the event to delete.
        db: The database session.
        current_user: The current user.

    Returns:
        A message indicating the result of the deletion.
    """
    try:
        await event_service.delete_event(
            db=db,
            event_id=event_id,
            user_id=UUID(str(current_user.id)),
        )
        return {"message": "Event deleted successfully"}
    except ValueError as e:
        # Handle the case where the event ID is not found
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
