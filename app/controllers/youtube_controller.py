import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database.session import get_db
from app.config.settings import get_settings
from app.config.youtube_auth import YouTubeAuth
from app.controllers.user_controller import get_current_user
from app.models.user_models import User
from app.services.chat_gateway_client import chat_gateway_client
from app.services.connection_service import ConnectionService

router = APIRouter(prefix="/auth", tags=["YouTube Authentication"])
logger = logging.getLogger(__name__)

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


@router.get("/youtube/callback")
async def youtube_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle YouTube OAuth callback - store connection and notify gateway, then redirect to frontend"""
    settings = get_settings()

    if error:
        logger.error(f"[YouTube] OAuth error for user {current_user.id}: {error}")
        # Redirect to frontend with error
        error_params = urlencode({"tab": "integrations", "youtube_error": error})
        return RedirectResponse(url=f"{settings.frontend_url}/settings?{error_params}", status_code=302)

    try:
        youtube_auth = YouTubeAuth()
        token_data = await youtube_auth.exchange_code_for_token(code)

        # Save the connection (encrypts tokens, revokes old one)
        await ConnectionService.save_connection(
            db=db,
            user_id=current_user.id,
            provider="youtube",
            token_data=token_data,
            scopes=YOUTUBE_SCOPES,
        )

        logger.info(f"[YouTube] Connection saved for user {current_user.id}")

        # Notify gateway to start polling connection
        try:
            await chat_gateway_client.connect_youtube(str(current_user.id))
            logger.info(f"[YouTube] Notified gateway to connect for user {current_user.id}")
        except Exception as e:
            logger.error(f"[YouTube] Failed to notify gateway: {e}")
            # Don't fail the auth flow if gateway notification fails

        # Redirect to frontend with success
        success_params = urlencode({"tab": "integrations", "youtube_success": "true"})
        return RedirectResponse(url=f"{settings.frontend_url}/settings?{success_params}", status_code=302)
    except Exception as e:
        logger.error(f"[YouTube] Auth failed for user {current_user.id}: {e}")
        error_params = urlencode({"tab": "integrations", "youtube_error": "auth_failed"})
        return RedirectResponse(url=f"{settings.frontend_url}/settings?{error_params}", status_code=302)


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
async def revoke_youtube_token(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Revoke/deactivate the current user's YouTube connection"""
    try:
        count = await ConnectionService.revoke_connection(db=db, user_id=current_user.id, provider="youtube")

        logger.info(f"[YouTube] Connection revoked for user {current_user.id}")

        # Notify gateway to disconnect
        try:
            await chat_gateway_client.disconnect_youtube(str(current_user.id))
            logger.info(f"[YouTube] Notified gateway to disconnect for user {current_user.id}")
        except Exception as e:
            logger.error(f"[YouTube] Failed to disconnect from gateway: {e}")

        return {
            "message": "YouTube connection revoked",
            "user_id": str(current_user.id),
            "connections_revoked": count,
        }

    except Exception as e:
        logger.error(f"[YouTube] Connection revocation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Connection revocation failed: {str(e)}")


@router.get("/youtube/token-status")
async def get_token_status(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get current user's YouTube connection status (never exposes raw tokens)"""
    try:
        return await ConnectionService.get_connection_status(db=db, user_id=current_user.id, provider="youtube")
    except Exception as e:
        logger.error(f"[YouTube] Token status check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")
