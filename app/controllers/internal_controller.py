from fastapi import APIRouter, HTTPException, Header, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config.database.session import get_db
from app.models.twitch.twitch_models import TwitchToken
from app.models.youtube_models import YouTubeToken
from datetime import datetime
import os
import logging

router = APIRouter(prefix="/api/internal", tags=["Internal"])
logger = logging.getLogger("InternalAPI")

SHARED_SECRET = os.getenv("CHAT_GATEWAY_SHARED_SECRET", "dev-secret")


def verify_internal_auth(x_internal_auth: str = Header(None, alias="X-Internal-Auth")):
    if x_internal_auth != SHARED_SECRET:
        logger.warning("[Internal] Unauthorized access attempt")
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/twitch-token/{user_id}")
async def get_twitch_token(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_internal_auth),
):
    """Internal endpoint for gateway to fetch Twitch tokens"""
    try:
        stmt = (
            select(TwitchToken)
            .where(
                TwitchToken.user_id == user_id,
                TwitchToken.is_active == True,
                TwitchToken.expires_at > datetime.now(),
            )
            .order_by(TwitchToken.created_at.desc())
        )

        result = await db.execute(stmt)
        token = result.scalars().first()

        if not token:
            logger.info(f"[Internal] No active Twitch token for user {user_id}")
            raise HTTPException(status_code=404, detail="No active token found")

        logger.info(f"[Internal] Fetched Twitch token for user {user_id}")
        return {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "expires_at": token.expires_at.isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Internal] Error fetching Twitch token: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/youtube-token/{user_id}")
async def get_youtube_token(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_internal_auth),
):
    """Internal endpoint for gateway to fetch YouTube tokens"""
    try:
        stmt = (
            select(YouTubeToken)
            .where(
                YouTubeToken.user_id == user_id,
                YouTubeToken.is_active == True,
                YouTubeToken.expires_at > datetime.now(),
            )
            .order_by(YouTubeToken.created_at.desc())
        )

        result = await db.execute(stmt)
        token = result.scalars().first()

        if not token:
            logger.info(f"[Internal] No active YouTube token for user {user_id}")
            raise HTTPException(status_code=404, detail="No active token found")

        logger.info(f"[Internal] Fetched YouTube token for user {user_id}")
        return {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "expires_at": token.expires_at.isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Internal] Error fetching YouTube token: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
