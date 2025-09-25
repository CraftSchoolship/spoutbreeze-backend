from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from uuid import UUID

from app.config.database.session import get_db
from app.controllers.user_controller import get_current_user
from app.models.user_models import User
from app.models.stream_schemas import (
    RtmpEndpointResponse,
    RtmpEndpointUpdate,
    CreateRtmpEndpointCreate,
    RtmpEndpointDeleteResponse,
)
from app.services.cached.rtmp_service_cached import RtmpEndpointServiceCached

router = APIRouter(prefix="/api/stream-endpoint", tags=["Stream Endpoints"])
rtmp_service = RtmpEndpointServiceCached()


@router.post("/create", response_model=RtmpEndpointResponse)
async def create_rtmp_endpoints(
    rtmp_endpoints: CreateRtmpEndpointCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RtmpEndpointResponse:
    """
    Create a new stream settings for the current user.

    Args:
        rtmp_endpoints: The stream settings to create.
        db: The database session.
        current_user: The current user.

    Returns:
        The created stream settings.
    """
    try:
        new_rtmp_endpoints = await rtmp_service.create_rtmp_endpoints(
            rtmp_endpoints=rtmp_endpoints,
            user_id=UUID(str(current_user.id)),
            db=db,
        )
        return new_rtmp_endpoints
    except ValueError as e:
        # Handle unique constraint violations with proper error messages
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/", response_model=List[RtmpEndpointResponse])
async def get_rtmp_endpoints(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[RtmpEndpointResponse]:
    """
    Get all stream settings for the current user.

    Args:
        db: The database session.
        current_user: The current user.

    Returns:
        A list of stream settings.
    """
    try:
        rtmp_endpoints = await rtmp_service.get_rtmp_endpoints_by_user_id(
            user_id=UUID(str(current_user.id)),
            db=db,
        )
        return rtmp_endpoints
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/all", response_model=List[RtmpEndpointResponse])
async def get_all_rtmp_endpoints(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[RtmpEndpointResponse]:
    """
    Get all stream settings for all users.

    Args:
        db: The database session.
        current_user: The current user (for authentication).

    Returns:
        A list of all stream settings from all users.
    """
    try:
        rtmp_endpoints = await rtmp_service.get_all_rtmp_endpoints(db=db)
        return rtmp_endpoints
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{rtmp_endpoints_id}", response_model=RtmpEndpointResponse)
async def get_rtmp_endpoints_by_id(
    rtmp_endpoints_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> RtmpEndpointResponse:
    """
    Get stream settings by ID.

    Args:
        rtmp_endpoints_id: The ID of the stream settings.
        db: The database session.

    Returns:
        The stream settings.
    """
    try:
        rtmp_endpoints = await rtmp_service.get_rtmp_endpoints_by_id(
            rtmp_endpoints_id=rtmp_endpoints_id,
            db=db,
        )
        if not rtmp_endpoints:
            raise HTTPException(status_code=404, detail="Stream settings not found")
        return rtmp_endpoints
    except HTTPException:
        # Re-raise HTTPException without changing it
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{rtmp_endpoints_id}", response_model=RtmpEndpointResponse)
async def update_rtmp_endpoints(
    rtmp_endpoints_id: UUID,
    rtmp_endpoints_update: RtmpEndpointUpdate,
    db: AsyncSession = Depends(get_db),
) -> RtmpEndpointResponse:
    """
    Update stream settings by ID.

    Args:
        rtmp_endpoints_id: The ID of the stream settings.
        rtmp_endpoints_update: The updated stream settings.
        db: The database session.

    Returns:
        The updated stream settings.
    """
    try:
        updated_rtmp_endpoints = await rtmp_service.update_rtmp_endpoints(
            rtmp_endpoints_id=rtmp_endpoints_id,
            rtmp_endpoints_update=rtmp_endpoints_update,
            db=db,
        )
        if not updated_rtmp_endpoints:
            raise HTTPException(status_code=404, detail="Stream settings not found")
        return updated_rtmp_endpoints
    except HTTPException:
        # Re-raise HTTPException without changing it
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{rtmp_endpoints_id}", response_model=RtmpEndpointDeleteResponse)
async def delete_rtmp_endpoints(
    rtmp_endpoints_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RtmpEndpointDeleteResponse:
    """
    Delete stream settings by ID.
    Args:
        rtmp_endpoints_id: The ID of the stream settings.
        db: The database session.
        current_user: The current user.
    Returns:
        The deleted stream settings.
    """
    try:
        deleted_rtmp_endpoints = await rtmp_service.delete_rtmp_endpoints(
            rtmp_endpoints_id=rtmp_endpoints_id,
            user_id=UUID(str(current_user.id)),
            db=db,
        )
        if not deleted_rtmp_endpoints:
            raise HTTPException(status_code=404, detail="Stream settings not found")
        return deleted_rtmp_endpoints
    except HTTPException:
        # Re-raise HTTPException without changing it
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
