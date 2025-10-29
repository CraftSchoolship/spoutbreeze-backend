from fastapi import APIRouter, Query, HTTPException, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, select
from app.config.database.session import get_db
from app.config.youtube_auth import YouTubeAuth
from app.models.youtube_models import YouTubeToken
from app.models.user_models import User
from app.controllers.user_controller import get_current_user
from app.services.youtube_service import youtube_service
from app.services.chat_gateway_client import chat_gateway_client
from datetime import datetime, timedelta
import logging
from pydantic import BaseModel
import os
import httpx
from app.config.settings import get_settings
import asyncio  # ADD

settings = get_settings()

router = APIRouter(prefix="/auth", tags=["YouTube Authentication"])
logger = logging.getLogger(__name__)

SHARED_SECRET = os.getenv("CHAT_GATEWAY_SHARED_SECRET", "dev-secret")


@router.get("/youtube/login")
async def youtube_login(current_user: User = Depends(get_current_user)):
    """Redirect user to YouTube for authorization"""
    youtube_auth = YouTubeAuth()
    auth_url = youtube_auth.get_authorization_url()
    return {
        "authorization_url": auth_url,
        "user_id": str(current_user.id),
    }


@router.get("/youtube/callback")
async def youtube_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle YouTube OAuth callback"""
    if error:
        raise HTTPException(status_code=400, detail=f"YouTube OAuth error: {error}")

    try:
        youtube_auth = YouTubeAuth()
        token_data = await youtube_auth.exchange_code_for_token(code)

        expires_at = datetime.now() + timedelta(
            seconds=token_data.get("expires_in", 3600)
        )

        # Deactivate old tokens
        stmt = (
            update(YouTubeToken)
            .where(YouTubeToken.user_id == current_user.id, YouTubeToken.is_active)
            .values(is_active=False)
        )
        await db.execute(stmt)

        # Store new token
        token = YouTubeToken(
            user_id=current_user.id,
            access_token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            is_active=True,
        )
        db.add(token)
        await db.commit()

        return {
            "message": "Successfully authenticated with YouTube",
            "expires_in": token_data.get("expires_in"),
            "user_id": str(current_user.id),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to exchange code: {str(e)}"
        )


@router.post("/youtube/connect")
async def connect_to_youtube(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Start YouTube chat polling for this user"""
    try:
        # Check if user has token
        stmt = select(YouTubeToken).where(
            YouTubeToken.user_id == current_user.id,
            YouTubeToken.is_active == True,
            YouTubeToken.expires_at > datetime.now(),
        )
        result = await db.execute(stmt)
        token = result.scalars().first()

        if not token:
            raise HTTPException(status_code=400, detail="No active YouTube token found")

        # Start chat polling
        await youtube_service.start_connection_for_user(str(current_user.id))
        await chat_gateway_client.register_platform("youtube", str(current_user.id))

        return {
            "message": "YouTube chat connection started",
            "user_id": str(current_user.id),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SendMessageRequest(BaseModel):
    user_id: str
    message: str


@router.post("/youtube/send-message")
async def send_youtube_message(
    request: SendMessageRequest,
    x_internal_auth: str = Header(None, alias="X-Internal-Auth"),
):
    """Send a message to YouTube Live Chat (internal endpoint for gateway)"""
    if not x_internal_auth or x_internal_auth != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        client = youtube_service.get_connection_for_user(request.user_id)

        if (
            not client
            or not client.is_connected
            or not getattr(client, "live_chat_id", None)
        ):
            logger.info(
                f"[YouTube] No active connection for {request.user_id}, starting..."
            )
            await youtube_service.start_connection_for_user(request.user_id)

            # Wait up to 5s for connect + liveChatId discovery
            for _ in range(10):
                client = youtube_service.get_connection_for_user(request.user_id)
                if (
                    client
                    and client.is_connected
                    and getattr(client, "live_chat_id", None)
                ):
                    break
                await asyncio.sleep(0.5)

        if (
            not client
            or not client.is_connected
            or not getattr(client, "live_chat_id", None)
        ):
            raise HTTPException(
                status_code=404,
                detail=f"No active YouTube live chat for user {request.user_id}",
            )

        await client.send_message(request.message)
        logger.info(
            f"[YouTube] → Sent message for user {request.user_id}: {request.message}"
        )
        return {
            "status": "success",
            "message": "Message sent to YouTube",
            "content": request.message,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[YouTube] Error sending message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Optional: quick status endpoint for debugging from Swagger
@router.get("/youtube/status")
async def youtube_status(current_user: User = Depends(get_current_user)):
    client = youtube_service.get_connection_for_user(str(current_user.id))
    return {
        "connected": bool(client and client.is_connected),
        "live_chat_id": getattr(client, "live_chat_id", None),
        "polling_interval": getattr(client, "polling_interval", None),
        "authorized_channel_id": getattr(client, "authorized_channel_id", None),  # ADD
        "authorized_channel_title": getattr(
            client, "authorized_channel_title", None
        ),  # ADD
        "last_error": getattr(client, "last_error", None),  # ADD
    }


from pydantic import BaseModel


class AttachChatIdRequest(BaseModel):
    live_chat_id: str


class AttachByVideoIdRequest(BaseModel):
    video_id: str


# Force attach by live_chat_id (debug/unblock)
@router.post("/youtube/attach-chat")
async def youtube_attach_chat(
    payload: AttachChatIdRequest,
    current_user: User = Depends(get_current_user),
):
    await youtube_service.start_with_chat_id(str(current_user.id), payload.live_chat_id)
    return {"status": "attached", "live_chat_id": payload.live_chat_id}


# Resolve from video_id then attach
@router.post("/youtube/attach-by-video")
async def youtube_attach_by_video(
    payload: AttachByVideoIdRequest,
    current_user: User = Depends(get_current_user),
):
    # use user's token via client
    client = youtube_service.get_connection_for_user(str(current_user.id))
    if not client:
        await youtube_service.start_connection_for_user(str(current_user.id))
        # give service a client
        client = youtube_service.get_connection_for_user(str(current_user.id))

    # ensure token
    if not client.token:
        client.token = await client.get_active_token()

    headers = {"Authorization": f"Bearer {client.token}"}
    async with httpx.AsyncClient() as http:
        vr = await http.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "liveStreamingDetails", "id": payload.video_id},
            headers=headers,
        )
        vr.raise_for_status()
        vdata = vr.json()
        if not vdata.get("items"):
            raise HTTPException(status_code=404, detail="Video not found")
        live_chat_id = vdata["items"][0]["liveStreamingDetails"].get("activeLiveChatId")
        if not live_chat_id:
            raise HTTPException(
                status_code=404, detail="No activeLiveChatId on this video"
            )
    await youtube_service.start_with_chat_id(str(current_user.id), live_chat_id)
    return {
        "status": "attached",
        "video_id": payload.video_id,
        "live_chat_id": live_chat_id,
    }


@router.get("/youtube/token-status")
async def youtube_token_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the current user's YouTube token status (mirrors Twitch token status shape)
    """
    try:
        stmt = (
            select(YouTubeToken)
            .where(YouTubeToken.user_id == current_user.id)
            .order_by(YouTubeToken.created_at.desc())
        )
        result = await db.execute(stmt)
        token = result.scalars().first()

        now = datetime.now()
        if not token:
            return {
                "user_id": str(current_user.id),
                "has_token": False,
                "token_preview": None,
                "expires_at": now.isoformat(),
                "current_time": now.isoformat(),
                "time_until_expiry": "0s",
                "is_expired": True,
                "expires_soon": False,
                "has_refresh_token": False,
                "created_at": now.isoformat(),
                "error": None,
            }

        expires_at = token.expires_at
        diff = (expires_at - now).total_seconds()
        is_expired = diff <= 0
        expires_soon = (diff > 0) and (diff <= 60 * 60)  # within 60 min

        # Simple preview
        token_preview = f"{token.access_token[:6]}..." if token.access_token else None

        # Human-ish time until expiry
        if diff <= 0:
            time_until_expiry = "0s"
        else:
            mins = int(diff // 60)
            if mins < 60:
                time_until_expiry = f"{mins}m"
            else:
                hrs = mins // 60
                rem = mins % 60
                time_until_expiry = f"{hrs}h {rem}m"

        return {
            "user_id": str(current_user.id),
            "has_token": token.is_active,
            "token_preview": token_preview,
            "expires_at": expires_at.isoformat(),
            "current_time": now.isoformat(),
            "time_until_expiry": time_until_expiry,
            "is_expired": is_expired,
            "expires_soon": expires_soon,
            "has_refresh_token": bool(token.refresh_token),
            "created_at": (token.created_at or now).isoformat(),
            "error": None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/youtube/token")
async def youtube_revoke_token(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Deactivate all active YouTube tokens for the user
    """
    try:
        stmt = (
            update(YouTubeToken)
            .where(
                YouTubeToken.user_id == current_user.id, YouTubeToken.is_active == True
            )
            .values(is_active=False, updated_at=datetime.now())
        )
        await db.execute(stmt)
        await db.commit()
        return {"message": "YouTube token revoked"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/youtube/disconnect")
async def disconnect_youtube(current_user: User = Depends(get_current_user)):
    await youtube_service.stop_connection_for_user(str(current_user.id))
    return {"message": "YouTube chat disconnected"}
