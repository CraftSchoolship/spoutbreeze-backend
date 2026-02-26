from fastapi import APIRouter, Body, Depends, Request, BackgroundTasks, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
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
from app.models.bbb_models import BbbMeeting
from uuid import UUID
from app.services.chat_context import set_user_mapping
from app.config.facebook_auth import FacebookAuth
from app.services.connection_service import ConnectionService
from pydantic import BaseModel
import os
import logging

router = APIRouter(prefix="/api/bbb", tags=["BigBlueButton"])
bbb_service = BBBServiceCached()

logger = logging.getLogger("BBBController")

PLUGIN_SECRET = os.getenv("CHAT_GATEWAY_SHARED_SECRET", "dev-secret")


def verify_plugin_auth(x_internal_auth: str = Header(None, alias="X-Internal-Auth")):
    """Verify shared secret for plugin requests."""
    if x_internal_auth != PLUGIN_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _get_user_id_from_meeting(meeting_id: str, db: AsyncSession) -> str:
    """Look up user_id from a meeting_id."""
    result = await db.execute(
        select(BbbMeeting).where(BbbMeeting.meeting_id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return str(meeting.user_id)


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
        meeting_id=result.get("internalMeetingID"),
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


# ─── Facebook Plugin Endpoints (shared secret auth) ─────────────


@router.get("/facebook/status/{meeting_id}")
async def facebook_status_for_plugin(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_plugin_auth),
):
    """Check if the meeting owner has an active Facebook connection."""
    user_id = await _get_user_id_from_meeting(meeting_id, db)

    user_status = await ConnectionService.get_connection_status(
        db=db, user_id=user_id, provider="facebook"
    )
    return {
        "connected": user_status.get("has_token", False),
        "is_expired": user_status.get("is_expired", False),
    }


@router.get("/facebook/pages/{meeting_id}")
async def facebook_pages_for_plugin(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_plugin_auth),
):
    """Return the meeting owner's connected Facebook Pages."""
    user_id = await _get_user_id_from_meeting(meeting_id, db)

    pages = await ConnectionService.get_connections_by_provider(
        db=db, user_id=user_id, provider="facebook_page"
    )
    return {
        "pages": [
            {
                "page_id": p.provider_user_id,
                "is_active": p.is_active,
            }
            for p in pages
        ]
    }


class PluginGoLiveRequest(BaseModel):
    meeting_id: str
    target: str = "me"  # "me" for profile, or a page ID
    title: str = "SpoutBreeze Live"
    privacy: str = "EVERYONE"


@router.post("/facebook/go-live")
async def facebook_go_live_for_plugin(
    body: PluginGoLiveRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_plugin_auth),
):
    """Create a Facebook LiveVideo and return RTMP URL + stream key."""
    user_id = await _get_user_id_from_meeting(body.meeting_id, db)

    # Get the right token
    if body.target == "me":
        token = await ConnectionService.get_decrypted_token(
            db=db, user_id=user_id, provider="facebook"
        )
    else:
        token = await ConnectionService.get_decrypted_token(
            db=db, user_id=user_id, provider="facebook_page",
            provider_user_id=body.target,
        )

    if not token:
        raise HTTPException(
            status_code=404,
            detail="No Facebook connection found for this user.",
        )

    try:
        fb_auth = FacebookAuth()
        result = await fb_auth.create_live_video(
            access_token=token["access_token"],
            target_id=body.target,
            title=body.title,
            privacy=body.privacy,
        )

        logger.info(
            f"[BBB Plugin] Facebook go-live: {result['live_video_id']} "
            f"for user {user_id} on {body.target}"
        )

        return {
            "live_video_id": result["live_video_id"],
            "rtmp_url": result["rtmp_url"],
            "stream_key": result["stream_key"],
            "stream_url": result["stream_url"],
            "target": body.target,
        }
    except Exception as e:
        logger.error(f"[BBB Plugin] Go-live failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class PluginEndLiveRequest(BaseModel):
    meeting_id: str
    live_video_id: str
    target: str = "me"


@router.post("/facebook/end-live")
async def facebook_end_live_for_plugin(
    body: PluginEndLiveRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_plugin_auth),
):
    """End a Facebook live broadcast."""
    user_id = await _get_user_id_from_meeting(body.meeting_id, db)

    if body.target == "me":
        token = await ConnectionService.get_decrypted_token(
            db=db, user_id=user_id, provider="facebook"
        )
    else:
        token = await ConnectionService.get_decrypted_token(
            db=db, user_id=user_id, provider="facebook_page",
            provider_user_id=body.target,
        )

    if not token:
        raise HTTPException(status_code=404, detail="No Facebook connection found.")

    try:
        fb_auth = FacebookAuth()
        await fb_auth.end_live_video(
            access_token=token["access_token"],
            live_video_id=body.live_video_id,
        )
        logger.info(f"[BBB Plugin] Facebook ended: {body.live_video_id}")
        return {"message": "Live video ended", "live_video_id": body.live_video_id}
    except Exception as e:
        logger.error(f"[BBB Plugin] End-live failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
