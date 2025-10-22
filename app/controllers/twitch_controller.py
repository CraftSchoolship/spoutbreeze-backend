from fastapi import APIRouter, Query, HTTPException, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, select
from app.config.database.session import get_db
from app.config.twitch_auth import TwitchAuth
from app.models.twitch.twitch_models import TwitchToken
from app.models.user_models import User
from app.controllers.user_controller import get_current_user
from app.services.twitch_service import twitch_service
from app.services.chat_gateway_client import chat_gateway_client
from datetime import datetime, timedelta
import logging
from pydantic import BaseModel
import os

router = APIRouter(prefix="/auth", tags=["Twitch Authentication"])
logger = logging.getLogger(__name__)

SHARED_SECRET = os.getenv("CHAT_GATEWAY_SHARED_SECRET", "dev-secret")


@router.get("/twitch/callback")
async def twitch_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle Twitch OAuth callback"""
    if error:
        raise HTTPException(status_code=400, detail=f"Twitch OAuth error: {error}")

    try:
        twitch_auth = TwitchAuth()
        token_data = await twitch_auth.exchange_code_for_token(code)

        # Store token in database
        expires_at = datetime.now() + timedelta(
            seconds=token_data.get("expires_in", 3600)
        )

        # Deactivate old tokens for this user
        stmt = (
            update(TwitchToken)
            .where(TwitchToken.user_id == current_user.id, TwitchToken.is_active)
            .values(is_active=False)
        )
        await db.execute(stmt)

        # Store new token for the current user
        token = TwitchToken(
            user_id=current_user.id,
            access_token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            is_active=True,
        )
        db.add(token)
        await db.commit()

        return {
            "message": "Successfully authenticated with Twitch and token stored",
            "expires_in": token_data.get("expires_in"),
            "user_id": str(current_user.id),
        }
    except Exception as e:
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
                "error": "No active token found for this user",
                "user_id": str(current_user.id),
                "has_token": False,
            }

        current_time = datetime.now()
        time_until_expiry = token_record.expires_at - current_time

        return {
            "user_id": str(current_user.id),
            "has_token": True,
            "token_preview": token_record.access_token[:20] + "...",
            "expires_at": token_record.expires_at.isoformat(),
            "current_time": current_time.isoformat(),
            "time_until_expiry": str(time_until_expiry),
            "is_expired": token_record.expires_at <= current_time,
            "expires_soon": token_record.expires_at
            <= current_time + timedelta(minutes=5),
            "has_refresh_token": bool(token_record.refresh_token),
            "created_at": token_record.created_at.isoformat(),
        }

    except Exception as e:
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

        tokens_deactivated = result.rowcount

        return {
            "message": "Twitch token(s) revoked successfully",
            "user_id": str(current_user.id),
            "tokens_deactivated": tokens_deactivated,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Token revocation failed: {str(e)}"
        )


@router.post("/twitch/connect")
async def connect_to_twitch(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Start Twitch IRC connection for this user"""
    try:
        # Check if user has token
        stmt = select(TwitchToken).where(
            TwitchToken.user_id == current_user.id,
            TwitchToken.is_active == True,
            TwitchToken.expires_at > datetime.now(),
        )
        result = await db.execute(stmt)
        token = result.scalars().first()

        if not token:
            raise HTTPException(status_code=400, detail="No active Twitch token found")

        # Start IRC connection
        await twitch_service.start_connection_for_user(str(current_user.id))
        # Do not await a long-running loop; optionally wait a moment
        # import asyncio
        # await asyncio.sleep(0.5)

        await chat_gateway_client.register_platform("twitch", str(current_user.id))

        return {
            "message": "Twitch connection started",
            "user_id": str(current_user.id),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SendMessageRequest(BaseModel):
    user_id: str
    message: str


@router.post("/twitch/send-message")
async def send_twitch_message(
    request: SendMessageRequest,
    x_internal_auth: str = Header(None, alias="X-Internal-Auth"),
):
    """Send a message to Twitch IRC (internal endpoint for gateway)"""
    # Verify internal auth
    if not x_internal_auth or x_internal_auth != SHARED_SECRET:
        logger.error(
            f"[Twitch] Auth failed. Received: {x_internal_auth}, Expected: {SHARED_SECRET}"
        )
        raise HTTPException(status_code=401, detail="Unauthorized - internal endpoint")

    try:
        from app.services.twitch_service import twitch_service
        import asyncio  # ADD

        client = twitch_service.get_connection_for_user(request.user_id)
        if not client or not getattr(client, "is_connected", False):
            logger.info(
                f"[Twitch] No active connection for {request.user_id}, starting..."
            )
            await twitch_service.start_connection_for_user(request.user_id)
            # brief wait for connection to establish
            await asyncio.sleep(1.0)
            client = twitch_service.get_connection_for_user(request.user_id)

        if not client or not getattr(client, "is_connected", False):
            raise HTTPException(
                status_code=404,
                detail=f"No active Twitch connection for user {request.user_id}",
            )

        await client.send_chat_message(request.message)
        logger.info(
            f"Message sent to Twitch for user {request.user_id}: {request.message}"
        )
        return {
            "status": "success",
            "message": "Message sent to Twitch",
            "content": request.message,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending Twitch message: {e}")
        raise HTTPException(status_code=500, detail=str(e))
