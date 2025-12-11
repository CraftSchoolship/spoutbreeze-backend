from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update
from app.config.database.session import get_db
from app.config.youtube_auth import YouTubeAuth
from app.models.youtube_models import YouTubeToken
from app.models.user_models import User
from app.controllers.user_controller import get_current_user
from app.services.chat_gateway_client import chat_gateway_client
from datetime import datetime, timedelta
import logging

router = APIRouter(prefix="/auth", tags=["YouTube Authentication"])
logger = logging.getLogger(__name__)


@router.get("/youtube/callback")
async def youtube_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle YouTube OAuth callback - store token and notify gateway"""
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

        logger.info(f"[YouTube] Token stored for user {current_user.id}")

        # Notify gateway to start polling connection
        try:
            await chat_gateway_client.connect_youtube(str(current_user.id))
            logger.info(
                f"[YouTube] Notified gateway to connect for user {current_user.id}"
            )
        except Exception as e:
            logger.error(f"[YouTube] Failed to notify gateway: {e}")

        return {
            "message": "Successfully authenticated with YouTube",
            "expires_in": token_data.get("expires_in"),
            "user_id": str(current_user.id),
        }
    except Exception as e:
        logger.error(f"[YouTube] Auth failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to exchange code: {str(e)}"
        )


@router.get("/youtube/login")
async def youtube_login(current_user: User = Depends(get_current_user)):
    """Redirect user to YouTube for authorization"""
    youtube_auth = YouTubeAuth()
    auth_url = youtube_auth.get_authorization_url()
    return {
        "authorization_url": auth_url,
        "user_id": str(current_user.id),
    }


@router.delete("/youtube/token")
async def revoke_youtube_token(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Revoke/deactivate the current user's YouTube token"""
    try:
        stmt = (
            update(YouTubeToken)
            .where(YouTubeToken.user_id == current_user.id, YouTubeToken.is_active)
            .values(is_active=False)
        )
        result = await db.execute(stmt)
        await db.commit()

        logger.info(f"[YouTube] Token revoked for user {current_user.id}")

        # Notify gateway to disconnect
        try:
            await chat_gateway_client.disconnect_youtube(str(current_user.id))
            logger.info(
                f"[YouTube] Notified gateway to disconnect for user {current_user.id}"
            )
        except Exception as e:
            logger.error(f"[YouTube] Failed to disconnect from gateway: {e}")

        return {
            "message": "YouTube token revoked",
            "user_id": str(current_user.id),
            "tokens_deactivated": result.rowcount,
        }

    except Exception as e:
        logger.error(f"[YouTube] Token revocation failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Token revocation failed: {str(e)}"
        )
