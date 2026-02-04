from fastapi import APIRouter, Body, Depends, Request, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.config.database.session import get_db

# Replace with cached services:
from app.services.cached.bbb_service_cached import BBBServiceCached
from app.services.cached.rtmp_service_cached import RtmpEndpointServiceCached
from app.models.bbb_schemas import (
    CreateMeetingRequest,
    JoinMeetingRequest,
    EndMeetingRequest,
    GetMeetingInfoRequest,
    IsMeetingRunningRequest,
    GetRecordingRequest,
)
from app.controllers.user_controller import get_current_user
from app.models.user_models import User
from uuid import UUID
from app.services.chat_context import set_user_mapping

router = APIRouter(prefix="/api/bbb", tags=["BigBlueButton"])
# bbb_service = BBBService()
bbb_service = BBBServiceCached()


@router.get("/")
def root():
    return {"message": "BBB API Integration with FastAPI"}


@router.post("/create")
async def create_meeting(
    request: CreateMeetingRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new BBB meeting."""
    result = await bbb_service.create_meeting(
        request=request,
        user_id=UUID(str(current_user.id)),
        db=db,
    )
    await set_user_mapping(
        meeting_id=str(result.get("internalMeetingID", "")),
        user_id=str(current_user.id),
        ttl=86400,
    )
    return result


@router.post("/join")
def join_meeting(request: JoinMeetingRequest = Body(...)):
    """Join a BBB meeting."""
    return bbb_service.join_meeting(request=request)


@router.post("/end")
async def end_meeting(
    request: EndMeetingRequest = Body(...), db: AsyncSession = Depends(get_db)
):
    """End a BBB meeting."""
    result = await bbb_service.end_meeting(request=request, db=db)
    return result


@router.post("/is-meeting-running")
async def is_meeting_running(request: IsMeetingRunningRequest = Body(...)):
    """Check if a meeting is running."""
    return await bbb_service.is_meeting_running_cached(request=request)


@router.post("/get-meeting-info")
async def get_meeting_info(request: GetMeetingInfoRequest = Body(...)):
    """Get detailed information about a meeting."""
    return await bbb_service.get_meeting_info_cached(request=request)


@router.get("/get-meetings")
async def get_meetings():
    """Get the list of all meetings."""
    return await bbb_service.get_meetings_cached()


@router.post("/get-recordings")
async def get_recordings(request: GetRecordingRequest = Body(...)):
    """Get the list of all recordings."""
    return await bbb_service.get_recordings_cached(request=request)


@router.get("/callback/meeting-ended")
async def meeting_ended_callback(
    request: Request, event_id: UUID, db: AsyncSession = Depends(get_db)
):
    """Callback endpoint for when a BBB meeting ends."""
    try:
        params = dict(request.query_params)
        meeting_id = params.get("meetingID")
        if not meeting_id:
            return {"error": "Missing meetingID in query parameters"}

        result = await bbb_service.meeting_ended_callback(
            meeting_id=meeting_id, db=db, event_id=event_id
        )
        return result
    except Exception as e:
        return {"error": str(e)}


@router.post("/maintenance/cleanup-old-meetings")
async def cleanup_old_meetings(
    background_tasks: BackgroundTasks,
    days: int = 30,
):
    """
    Cleanup old meetings that are older than the specified number of days.
    This is a background task that runs asynchronously.
    """
    background_tasks.add_task(bbb_service._clean_up_meetings_background, days=days)
    return {
        "message": f"Cleanup task for meetings older than {days} days has been started."
    }


@router.get("/proxy/stream-endpoints")
async def get_stream_endpoints_proxy(
    db: AsyncSession = Depends(get_db),
):
    """
    Proxy endpoint for BBB plugins to access stream endpoints.
    Returns all available stream endpoints.
    """
    try:
        # Use cached RTMP service
        rtmp_service = RtmpEndpointServiceCached()
        stream_endpoints = await rtmp_service.get_all_rtmp_endpoints(db=db)
        return stream_endpoints
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Get Meeting by internal meeting ID
@router.get("/meeting/{internal_meeting_id}")
async def get_meeting_by_internal_id(
    internal_meeting_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get a BBB meeting by its internal meeting ID.
    """
    try:
        meeting = await bbb_service.get_meeting_by_internal_id(
            internal_meeting_id=internal_meeting_id, db=db
        )
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        return meeting
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
