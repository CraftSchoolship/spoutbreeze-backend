from fastapi import APIRouter, Body
from app.services.broadcaster_service import BroadcasterService
from app.models.bbb_schemas import BroadcasterRobot
from app.services.bbb_service import BBBService


router = APIRouter(prefix="/api/bbb", tags=["Broadcaster"])

bbb_service = BBBService()
broadcaster_service = BroadcasterService()


@router.post("/broadcaster")
async def broadcaster_meeting(
    payload: BroadcasterRobot = Body(..., description="Broadcaster robot payload"),
):
    """
    Start broadcasting a BBB meeting.
    platform maps to stream.title (e.g., youtube, twitch, etc.)
    """
    return await broadcaster_service.start_broadcasting(
        meeting_id=payload.meeting_id,
        rtmp_url=payload.rtmp_url,
        stream_key=payload.stream_key,
        password=payload.password,
        platform=payload.platform,
        bbb_service=bbb_service,
    )


# Add these imports
# from app.services.streaming_platform_service import StreamingPlatformService
# from pydantic import BaseModel
# from uuid import UUID

# # Add this service
# streaming_platform_service = StreamingPlatformService()

# # Add this model
# class StartStreamRequest(BaseModel):
#     meeting_id: str
#     platform_id: UUID

# # Add this endpoint
# @router.post("/start-stream")
# async def start_stream(
#     request: StartStreamRequest,
#     db: AsyncSession = Depends(get_db),
#     current_user: User = Depends(get_current_user)
# ):
#     """
#     Start streaming a BBB meeting to a selected platform
#     """
#     # Verify platform exists and belongs to user
#     platform = await streaming_platform_service.get_platform(
#         db=db,
#         platform_id=request.platform_id,
#         user_id=current_user.id
#     )

#     if platform is None:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail="Streaming platform not found"
#         )

#     # Check if the meeting exists and is running
#     meeting_check = bbb_service.is_meeting_running(
#         IsMeetingRunningRequest(meeting_id=request.meeting_id)
#     )

#     if not meeting_check.get("running", "false") == "true":
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="Meeting is not running"
#         )

#     # Use broadcaster service to start streaming
#     try:
#         # Here you'd implement the actual streaming start logic
#         # This depends on your broadcaster implementation
#         from app.services.broadcaster_service import BroadcasterService
#         broadcaster = BroadcasterService()

#         stream_result = broadcaster.start_stream(
#             meeting_id=request.meeting_id,
#             rtmp_url=platform.rtmp_url,
#             stream_key=platform.stream_key
#         )

#         return {
#             "status": "success",
#             "message": f"Streaming to {platform.name} started successfully",
#             "details": stream_result
#         }

#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Failed to start stream: {str(e)}"
#         )
