from fastapi import APIRouter, HTTPException, Header, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.config.database.session import get_db
from app.services.connection_service import ConnectionService
import os
import logging

router = APIRouter(prefix="/api/internal", tags=["Internal"])
logger = logging.getLogger("InternalAPI")

SHARED_SECRET = os.getenv("CHAT_GATEWAY_SHARED_SECRET", "dev-secret")

VALID_PROVIDERS = {"twitch", "youtube", "facebook"}


def verify_internal_auth(x_internal_auth: str = Header(None, alias="X-Internal-Auth")):
    if x_internal_auth != SHARED_SECRET:
        logger.warning("[Internal] Unauthorized access attempt")
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/token/{provider}/{user_id}")
async def get_provider_token(
    provider: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_internal_auth),
):
    """Unified internal endpoint for gateway to fetch provider tokens (with auto-refresh)."""
    if provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider '{provider}'. Must be one of: {', '.join(VALID_PROVIDERS)}",
        )

    try:
        token_data = await ConnectionService.get_valid_token(
            db=db, user_id=user_id, provider=provider
        )

        if not token_data:
            logger.info(f"[Internal] No active {provider} token for user {user_id}")
            raise HTTPException(status_code=404, detail="No active token found")

        logger.info(f"[Internal] Fetched {provider} token for user {user_id}")
        return token_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Internal] Error fetching {provider} token: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# --- Backward-compatible endpoints ---


@router.get("/twitch-token/{user_id}")
async def get_twitch_token(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_internal_auth),
):
    """Internal endpoint for gateway to fetch Twitch tokens (backward-compatible)."""
    return await get_provider_token("twitch", user_id, db, _auth)


@router.get("/youtube-token/{user_id}")
async def get_youtube_token(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_internal_auth),
):
    """Internal endpoint for gateway to fetch YouTube tokens (backward-compatible)."""
    return await get_provider_token("youtube", user_id, db, _auth)
