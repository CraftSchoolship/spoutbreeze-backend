from fastapi import APIRouter, Body, status
from app.services.broadcaster_service import BroadcasterService
from app.models.bbb_schemas import (
    BroadcasterRobot,
    StartBroadcastResponse,
    BroadcastStatusResponse,
)
from app.services.bbb_service import BBBService

router = APIRouter(prefix="/api/bbb", tags=["Broadcaster"])

bbb_service = BBBService()
broadcaster_service = BroadcasterService()


@router.post(
    "/broadcaster",
    response_model=StartBroadcastResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_broadcaster(payload: BroadcasterRobot = Body(...)):
    return await broadcaster_service.start_broadcasting(
        meeting_id=payload.meeting_id,
        rtmp_url=payload.rtmp_url,
        stream_key=payload.stream_key,
        password=payload.password,
        platform=payload.platform,
        bbb_service=bbb_service,
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
