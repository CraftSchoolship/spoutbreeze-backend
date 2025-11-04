from fastapi import APIRouter, Query, HTTPException, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, select
from app.config.database.session import get_db
from app.config.youtube_auth import YouTubeAuth
from app.models.youtube_models import YouTubeToken
from app.models.user_models import User
from app.controllers.user_controller import get_current_user
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
    """Tell Gateway to start YouTube connection"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            gateway_url = os.getenv("CHAT_GATEWAY_URL", "http://localhost:8800")
            await client.post(
                f"{gateway_url}/platforms/youtube/connect",
                params={"user_id": str(current_user.id)},
                headers={"X-Internal-Auth": SHARED_SECRET},
            )
    except Exception as e:
        logger.error(f"Failed to notify gateway: {e}")

    return {"status": "connection_requested"}


@router.post("/youtube/connect-with-chat-id")
async def connect_youtube_with_chat_id(
    live_chat_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tell Gateway to connect YouTube with a specific chat ID"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            gateway_url = os.getenv("CHAT_GATEWAY_URL", "http://localhost:8800")
            await client.post(
                f"{gateway_url}/platforms/youtube/connect-with-chat-id",
                params={"user_id": str(current_user.id), "live_chat_id": live_chat_id},
                headers={"X-Internal-Auth": SHARED_SECRET},
            )
    except Exception as e:
        logger.error(f"Failed to notify gateway: {e}")

    return {"status": "connection_requested", "chat_id": live_chat_id}


# # Optional: quick status endpoint for debugging from Swagger
# @router.get("/youtube/status")
# async def youtube_status(current_user: User = Depends(get_current_user)):
#     client = youtube_service.get_connection_for_user(str(current_user.id))
#     return {
#         "connected": bool(client and client.is_connected),
#         "live_chat_id": getattr(client, "live_chat_id", None),
#         "polling_interval": getattr(client, "polling_interval", None),
#         "authorized_channel_id": getattr(client, "authorized_channel_id", None),  # ADD
#         "authorized_channel_title": getattr(
#             client, "authorized_channel_title", None
#         ),  # ADD
#         "last_error": getattr(client, "last_error", None),  # ADD
#     }


from pydantic import BaseModel


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
