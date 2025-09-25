from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.config.database.session import get_db
from app.controllers.user_controller import get_current_user
from app.models.user_models import User
from app.models.channel.channels_schemas import (
    ChannelCreate,
    ChannelResponse,
    ChannelListResponse,
    ChannelUpdate,
)
from app.services.cached.channels_service_cached import ChannelsServiceCached
from app.config.logger_config import logger

router = APIRouter(prefix="/api/channels", tags=["Channels"])
channels_service = ChannelsServiceCached()


@router.post("/", response_model=ChannelResponse)
async def create_channel(
    channel_create: ChannelCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChannelResponse:
    """
    Create a new channel for the current user.

    Args:
        channel_create: The channel to create.
        db: The database session.
        current_user: The current user.

    Returns:
        The created channel.
    """
    try:
        new_channel = await channels_service.create_channel(
            channel_create=channel_create,
            user_id=UUID(str(current_user.id)),
            db=db,
        )
        if not new_channel:
            raise HTTPException(status_code=400, detail="Channel creation failed")
        return new_channel
    except HTTPException as failed:
        raise failed
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/all", response_model=ChannelListResponse)
async def get_all_channels(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChannelListResponse:
    """
    Get all channels for the current user.
    Args:
        db: The database session.
        current_user: The current user.
    Returns:
        A list of channels.
    """
    try:
        channels = await channels_service.get_channels(
            db=db,
        )
        # Return empty list instead of 404 when no channels found
        return ChannelListResponse(channels=channels or [], total=len(channels or []))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/", response_model=ChannelListResponse)
async def get_channels_by_user(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChannelListResponse:
    """
    Get all channels for the current user.

    Args:
        db: The database session.
        current_user: The current user.

    Returns:
        A list of channels.
    """
    try:
        channels = await channels_service.get_channels_by_user_id(
            user_id=UUID(str(current_user.id)),
            db=db,
        )
        if not channels:
            raise HTTPException(status_code=404, detail="No channels found")
        return ChannelListResponse(channels=channels, total=len(channels))
    except HTTPException as not_found:
        raise not_found
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel_by_id(
    channel_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChannelResponse:
    """
    Get a channel by its ID.

    Args:
        channel_id: The ID of the channel to retrieve.
        db: The database session.
        current_user: The current user.

    Returns:
        The channel with the specified ID.
    """
    try:
        channel = await channels_service.get_channel_by_id(
            channel_id=channel_id,
            db=db,
        )
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        return channel
    except HTTPException as not_found:
        raise not_found
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{channel_id}", response_model=ChannelResponse)
async def update_channel(
    channel_id: UUID,
    channel_update: ChannelUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChannelResponse:
    """
    Update a channel by its ID.

    Args:
        channel_id: The ID of the channel to update.
        channel_update: The updated channel data.
        db: The database session.
        current_user: The current user.

    Returns:
        The updated channel.
    """
    try:
        updated_channel = await channels_service.update_channel(
            channel_id=channel_id,
            channel_update=channel_update,
            user_id=UUID(str(current_user.id)),
            db=db,
        )
        if not updated_channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        return updated_channel
    except HTTPException as not_found:
        raise not_found
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{channel_id}")
async def delete_channel(
    channel_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Delete a channel by its ID.

    Args:
        channel_id: The ID of the channel to delete.
        db: The database session.
        current_user: The current user.

    Returns:
        None
    """
    try:
        await channels_service.delete_channel(
            channel_id=channel_id,
            user_id=UUID(str(current_user.id)),
            db=db,
        )
        return {"message": "Channel deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{channel_id}/recordings")
async def get_channel_recordings(
    channel_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all recordings for events in a specific channel.
    """
    try:
        result = await channels_service.get_channel_recordings(
            db=db,
            channel_id=channel_id,
            user_id=current_user.id,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting channel recordings: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
