from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database.session import get_db
from app.models.bbb_models import BbbMeeting
from app.models.bbb_schemas import (
    BroadcasterRobot,
    BroadcastStatusResponse,
    StartBroadcastResponse,
)
from app.services.bbb_service import BBBService
from app.services.broadcaster_service import BroadcasterService

router = APIRouter(prefix="/api/bbb", tags=["Broadcaster"])

bbb_service = BBBService()
broadcaster_service = BroadcasterService()


@router.post(
    "/broadcaster",
    response_model=StartBroadcastResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_broadcaster(
    payload: BroadcasterRobot = Body(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(BbbMeeting).where(BbbMeeting.meeting_id == payload.meeting_id))
    meeting = result.scalar_one_or_none()

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    user_id = str(meeting.user_id)

    return await broadcaster_service.start_broadcasting(
        meeting_id=payload.meeting_id,
        rtmp_url=payload.rtmp_url,
        stream_key=payload.stream_key,
        password=payload.password,
        platform=payload.platform,
        bbb_service=bbb_service,
        user_id=user_id,
        db=db,
        requested_resolution=payload.resolution,  # <-- NEW
    )


@router.get(
    "/broadcaster/{stream_id}",
    response_model=BroadcastStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def get_broadcast_status(stream_id: str):
    return await broadcaster_service.fetch_status(stream_id)


@router.delete("/broadcaster/{stream_id}", status_code=status.HTTP_200_OK)
async def stop_broadcast(stream_id: str):
    return await broadcaster_service.stop_broadcast(stream_id)
