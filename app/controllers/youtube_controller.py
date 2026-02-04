from fastapi import APIRouter, Query, HTTPException, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, select
from app.config.database.session import get_db
from app.config.youtube_auth import YouTubeAuth
from app.config.settings import get_settings
from app.models.youtube_models import YouTubeToken
from app.models.user_models import User
from app.controllers.user_controller import get_current_user
from app.services.chat_gateway_client import chat_gateway_client
from datetime import datetime, timedelta
import logging
from urllib.parse import urlencode

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
    """Handle YouTube OAuth callback - store token and notify gateway, then redirect to frontend"""
    settings = get_settings()

    if error:
        logger.error(f"[YouTube] OAuth error for user {current_user.id}: {error}")
        # Redirect to frontend with error
        error_params = urlencode({"tab": "integrations", "youtube_error": error})
        return RedirectResponse(
            url=f"{settings.frontend_url}/settings?{error_params}", status_code=302
        )

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
            # Don't fail the auth flow if gateway notification fails

        # Redirect to frontend with success
        success_params = urlencode({"tab": "integrations", "youtube_success": "true"})
        return RedirectResponse(
            url=f"{settings.frontend_url}/settings?{success_params}", status_code=302
        )
    except Exception as e:
        logger.error(f"[YouTube] Auth failed for user {current_user.id}: {e}")
        error_params = urlencode(
            {"tab": "integrations", "youtube_error": "auth_failed"}
        )
        return RedirectResponse(
            url=f"{settings.frontend_url}/settings?{error_params}", status_code=302
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


@router.get("/youtube/token-status")
async def get_token_status(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Get current user's YouTube token status"""
    try:
        stmt = (
            select(YouTubeToken)
            .where(YouTubeToken.user_id == current_user.id, YouTubeToken.is_active)
            .order_by(YouTubeToken.created_at.desc())
        )

        result = await db.execute(stmt)
        token_record = result.scalars().first()

        if not token_record:
            return {
                "error": "No active token found",
                "user_id": str(current_user.id),
                "has_token": False,
            }

        current_time = datetime.now()
        time_until_expiry = token_record.expires_at - current_time
        expires_soon = time_until_expiry.total_seconds() < 3600  # Less than 1 hour

        return {
            "user_id": str(current_user.id),
            "has_token": True,
            "token_preview": (
                token_record.access_token[:20] + "..."
                if token_record.access_token
                else None
            ),
            "expires_at": token_record.expires_at.isoformat(),
            "current_time": current_time.isoformat(),
            "time_until_expiry": str(time_until_expiry),
            "is_expired": token_record.expires_at <= current_time,
            "expires_soon": expires_soon,
            "has_refresh_token": bool(token_record.refresh_token),
            "created_at": token_record.created_at.isoformat(),
        }

    except Exception as e:
        logger.error(f"[YouTube] Token status check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")
