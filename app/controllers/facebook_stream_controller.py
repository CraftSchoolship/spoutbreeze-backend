import logging
import os

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database.session import get_db
from app.config.facebook_auth import FacebookAuth
from app.models.bbb_models import BbbMeeting
from app.services.connection_service import ConnectionService

router = APIRouter(prefix="/api/streaming/facebook", tags=["Facebook Streaming"])

logger = logging.getLogger("FacebookStreamController")
PLUGIN_SECRET = os.getenv("CHAT_GATEWAY_SHARED_SECRET", "dev-secret")


def verify_plugin_auth(x_internal_auth: str = Header(None, alias="X-Internal-Auth")):
    """Verify shared secret for internal streaming clients."""
    if x_internal_auth != PLUGIN_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _get_user_id_from_meeting(meeting_id: str, db: AsyncSession) -> str:
    """Look up user_id from a meeting_id."""
    result = await db.execute(select(BbbMeeting).where(BbbMeeting.meeting_id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return str(meeting.user_id)


@router.get("/status/{meeting_id}")
async def facebook_status(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_plugin_auth),
):
    """Check if the meeting owner has an active Facebook connection."""
    user_id = await _get_user_id_from_meeting(meeting_id, db)

    user_status = await ConnectionService.get_connection_status(db=db, user_id=user_id, provider="facebook")
    return {
        "connected": user_status.get("has_token", False),
        "is_expired": user_status.get("is_expired", False),
    }


@router.get("/pages/{meeting_id}")
async def facebook_pages(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_plugin_auth),
):
    """Return the meeting owner's connected Facebook Pages."""
    user_id = await _get_user_id_from_meeting(meeting_id, db)

    pages = await ConnectionService.get_connections_by_provider(db=db, user_id=user_id, provider="facebook_page")
    return {
        "pages": [
            {
                "page_id": p.provider_user_id,
                "is_active": p.is_active,
            }
            for p in pages
        ]
    }


@router.get("/token/{meeting_id}")
async def facebook_stream_token(
    meeting_id: str,
    target: str = "me",
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_plugin_auth),
):
    """Return a decrypted Facebook access token for internal streaming clients."""
    user_id = await _get_user_id_from_meeting(meeting_id, db)

    if target == "me":
        token = await ConnectionService.get_decrypted_token(db=db, user_id=user_id, provider="facebook")
    else:
        token = await ConnectionService.get_decrypted_token(
            db=db,
            user_id=user_id,
            provider="facebook_page",
            provider_user_id=target,
        )

    if not token:
        raise HTTPException(status_code=404, detail="No Facebook connection found.")

    return {
        "access_token": token.get("access_token"),
        "expires_at": token.get("expires_at"),
        "target": target,
        "user_id": user_id,
    }


class GoLiveRequest(BaseModel):
    meeting_id: str
    target: str = "me"
    title: str = "SpoutBreeze Live"
    privacy: str = "EVERYONE"


@router.post("/go-live")
async def facebook_go_live(
    body: GoLiveRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_plugin_auth),
):
    """Create a Facebook LiveVideo and return RTMP URL + stream key."""
    user_id = await _get_user_id_from_meeting(body.meeting_id, db)

    if body.target == "me":
        token = await ConnectionService.get_decrypted_token(db=db, user_id=user_id, provider="facebook")
    else:
        token = await ConnectionService.get_decrypted_token(
            db=db,
            user_id=user_id,
            provider="facebook_page",
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

        logger.info(f"[Facebook Stream] Go-live: {result['live_video_id']} for user {user_id} on {body.target}")

        return {
            "live_video_id": result["live_video_id"],
            "rtmp_url": result["rtmp_url"],
            "stream_key": result["stream_key"],
            "stream_url": result["stream_url"],
            "target": body.target,
        }
    except Exception as e:
        logger.error(f"[Facebook Stream] Go-live failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class EndLiveRequest(BaseModel):
    meeting_id: str
    live_video_id: str
    target: str = "me"


@router.post("/end-live")
async def facebook_end_live(
    body: EndLiveRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_plugin_auth),
):
    """End a Facebook live broadcast."""
    user_id = await _get_user_id_from_meeting(body.meeting_id, db)

    if body.target == "me":
        token = await ConnectionService.get_decrypted_token(db=db, user_id=user_id, provider="facebook")
    else:
        token = await ConnectionService.get_decrypted_token(
            db=db,
            user_id=user_id,
            provider="facebook_page",
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
        logger.info(f"[Facebook Stream] Ended: {body.live_video_id}")
        return {"message": "Live video ended", "live_video_id": body.live_video_id}
    except Exception as e:
        logger.error(f"[Facebook Stream] End-live failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
