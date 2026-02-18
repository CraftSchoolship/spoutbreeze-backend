from fastapi import APIRouter, Query, HTTPException, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.config.database.session import get_db
from app.config.twitch_auth import TwitchAuth
from app.models.user_models import User
from app.controllers.user_controller import get_current_user
from app.services.chat_gateway_client import chat_gateway_client
from app.services.connection_service import ConnectionService
import logging
from app.config.settings import get_settings

router = APIRouter(prefix="/auth", tags=["Twitch Authentication"])
logger = logging.getLogger(__name__)

TWITCH_SCOPES = ["chat:read", "chat:edit"]


@router.get("/twitch/callback")
async def twitch_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle Twitch OAuth callback - store connection and notify gateway"""
    settings = get_settings()

    if error:
        # Redirect to frontend with error
        return RedirectResponse(
            url=f"{settings.frontend_url}/settings?twitch_error={error}",
            status_code=302,
        )

    try:
        twitch_auth = TwitchAuth()
        token_data = await twitch_auth.exchange_code_for_token(code)

        # Save the connection (encrypts tokens, revokes old one)
        await ConnectionService.save_connection(
            db=db,
            user_id=current_user.id,
            provider="twitch",
            token_data=token_data,
            scopes=TWITCH_SCOPES,
        )

        logger.info(f"[Twitch] Connection saved for user {current_user.id}")

        # Notify gateway to start IRC connection
        try:
            await chat_gateway_client.connect_twitch(str(current_user.id))
            logger.info(
                f"[Twitch] Notified gateway to connect for user {current_user.id}"
            )
        except Exception as e:
            logger.error(f"[Twitch] Failed to notify gateway: {e}")

        # Redirect to frontend with success
        return RedirectResponse(
            url=f"{settings.frontend_url}/settings?twitch_success=true", status_code=302
        )
    except Exception as e:
        logger.error(f"[Twitch] Auth failed: {e}")
        return RedirectResponse(
            url=f"{settings.frontend_url}/settings?twitch_error=auth_failed",
            status_code=302,
        )


@router.get("/twitch/login")
async def twitch_login(current_user: User = Depends(get_current_user)):
    """Redirect user to Twitch for authorization"""
    twitch_auth = TwitchAuth()
    auth_url = twitch_auth.get_authorization_url()
    return {
        "authorization_url": auth_url,
        "user_id": str(current_user.id),
    }


@router.get("/twitch/token-status")
async def get_token_status(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Get current user's Twitch connection status (never exposes raw tokens)"""
    try:
        return await ConnectionService.get_connection_status(
            db=db, user_id=current_user.id, provider="twitch"
        )
    except Exception as e:
        logger.error(f"[Twitch] Token status check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")


@router.delete("/twitch/token")
async def revoke_twitch_token(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Revoke/deactivate the current user's Twitch connection"""
    try:
        count = await ConnectionService.revoke_connection(
            db=db, user_id=current_user.id, provider="twitch"
        )

        logger.info(f"[Twitch] Connection revoked for user {current_user.id}")

        # Notify gateway to disconnect
        try:
            await chat_gateway_client.disconnect_twitch(str(current_user.id))
            logger.info(
                f"[Twitch] Notified gateway to disconnect for user {current_user.id}"
            )
        except Exception as e:
            logger.error(f"[Twitch] Failed to disconnect from gateway: {e}")

        return {
            "message": "Twitch connection revoked",
            "user_id": str(current_user.id),
            "connections_revoked": count,
        }

    except Exception as e:
        logger.error(f"[Twitch] Connection revocation failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Connection revocation failed: {str(e)}"
        )
