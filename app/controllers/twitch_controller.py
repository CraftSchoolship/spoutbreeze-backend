from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, select
from app.config.database.session import get_db
from app.config.twitch_auth import TwitchAuth
from app.models.twitch.twitch_models import TwitchToken
from app.models.user_models import User
from app.controllers.user_controller import get_current_user
from app.services.chat_gateway_client import chat_gateway_client
from datetime import datetime, timedelta
import logging

router = APIRouter(prefix="/auth", tags=["Twitch Authentication"])
logger = logging.getLogger(__name__)


@router.get("/twitch/callback")
async def twitch_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle Twitch OAuth callback - store token and notify gateway"""
    if error:
        raise HTTPException(status_code=400, detail=f"Twitch OAuth error: {error}")

    try:
        twitch_auth = TwitchAuth()
        token_data = await twitch_auth.exchange_code_for_token(code)

        expires_at = datetime.now() + timedelta(
            seconds=token_data.get("expires_in", 3600)
        )

        # Deactivate old tokens
        stmt = (
            update(TwitchToken)
            .where(TwitchToken.user_id == current_user.id, TwitchToken.is_active)
            .values(is_active=False)
        )
        await db.execute(stmt)

        # Store new token
        token = TwitchToken(
            user_id=current_user.id,
            access_token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            is_active=True,
        )
        db.add(token)
        await db.commit()

        logger.info(f"[Twitch] Token stored for user {current_user.id}")

        # Notify gateway to start IRC connection
        try:
            await chat_gateway_client.connect_twitch(str(current_user.id))
            logger.info(
                f"[Twitch] Notified gateway to connect for user {current_user.id}"
            )
        except Exception as e:
            logger.error(f"[Twitch] Failed to notify gateway: {e}")
            # Don't fail the auth flow if gateway notification fails

        return {
            "message": "Successfully authenticated with Twitch",
            "expires_in": token_data.get("expires_in"),
            "user_id": str(current_user.id),
        }
    except Exception as e:
        logger.error(f"[Twitch] Auth failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to exchange code: {str(e)}"
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
    """Get current user's token status"""
    try:
        stmt = (
            select(TwitchToken)
            .where(
                TwitchToken.user_id == current_user.id, TwitchToken.is_active == True
            )
            .order_by(TwitchToken.created_at.desc())
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

        return {
            "user_id": str(current_user.id),
            "has_token": True,
            "token_preview": token_record.access_token,
            "expires_at": token_record.expires_at.isoformat(),
            "is_expired": token_record.expires_at <= current_time,
            "has_refresh_token": bool(token_record.refresh_token),
        }

    except Exception as e:
        logger.error(f"[Twitch] Token status check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")


@router.delete("/twitch/token")
async def revoke_twitch_token(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Revoke/deactivate the current user's Twitch token"""
    try:
        stmt = (
            update(TwitchToken)
            .where(TwitchToken.user_id == current_user.id, TwitchToken.is_active)
            .values(is_active=False)
        )
        result = await db.execute(stmt)
        await db.commit()

        logger.info(f"[Twitch] Token revoked for user {current_user.id}")

        # Notify gateway to disconnect
        try:
            await chat_gateway_client.disconnect_twitch(str(current_user.id))
            logger.info(
                f"[Twitch] Notified gateway to disconnect for user {current_user.id}"
            )
        except Exception as e:
            logger.error(f"[Twitch] Failed to disconnect from gateway: {e}")

        return {
            "message": "Twitch token revoked",
            "user_id": str(current_user.id),
            "tokens_deactivated": result.rowcount,
        }

    except Exception as e:
        logger.error(f"[Twitch] Token revocation failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Token revocation failed: {str(e)}"
        )
