from fastapi import APIRouter, Query, HTTPException, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.config.database.session import get_db
from app.config.facebook_auth import FacebookAuth
from app.config.settings import get_settings
from app.models.user_models import User
from app.controllers.user_controller import get_current_user
from app.services.connection_service import ConnectionService
import logging
from urllib.parse import urlencode

router = APIRouter(prefix="/auth", tags=["Facebook Authentication"])
logger = logging.getLogger(__name__)

FACEBOOK_SCOPES = [
    "publish_video",
    "pages_manage_posts",
    "pages_read_engagement",
]


@router.get("/facebook/callback")
async def facebook_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    error_reason: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle Facebook OAuth callback — exchange code, store connection, redirect to frontend."""
    settings = get_settings()

    if error:
        logger.error(
            f"[Facebook] OAuth error for user {current_user.id}: {error} - {error_reason}"
        )
        error_params = urlencode({"tab": "integrations", "facebook_error": error})
        return RedirectResponse(
            url=f"{settings.frontend_url}/settings?{error_params}", status_code=302
        )

    if not code:
        error_params = urlencode(
            {"tab": "integrations", "facebook_error": "invalid_callback"}
        )
        return RedirectResponse(
            url=f"{settings.frontend_url}/settings?{error_params}", status_code=302
        )

    try:
        fb_auth = FacebookAuth()

        # Exchange code for short-lived token
        short_token_data = await fb_auth.exchange_code_for_token(code)

        # Exchange short-lived token for long-lived token (~60 days)
        long_token_data = await fb_auth.exchange_for_long_lived_token(
            short_token_data["access_token"]
        )

        # Facebook doesn't return a refresh_token — the long-lived token IS the
        # refreshable credential. We store it as both access_token and refresh_token
        # so the unified refresh logic in ConnectionService can work.
        token_data = {
            "access_token": long_token_data["access_token"],
            "refresh_token": long_token_data["access_token"],  # same token used for refresh
            "expires_in": long_token_data.get("expires_in", 5184000),  # ~60 days
        }

        await ConnectionService.save_connection(
            db=db,
            user_id=str(current_user.id),
            provider="facebook",
            token_data=token_data,
            scopes=FACEBOOK_SCOPES,
        )

        logger.info(f"[Facebook] Connection saved for user {current_user.id}")

        success_params = urlencode(
            {"tab": "integrations", "facebook_success": "true"}
        )
        return RedirectResponse(
            url=f"{settings.frontend_url}/settings?{success_params}", status_code=302
        )

    except Exception as e:
        logger.error(f"[Facebook] Auth failed for user {current_user.id}: {e}")
        error_params = urlencode(
            {"tab": "integrations", "facebook_error": "auth_failed"}
        )
        return RedirectResponse(
            url=f"{settings.frontend_url}/settings?{error_params}", status_code=302
        )


@router.get("/facebook/login")
async def facebook_login(current_user: User = Depends(get_current_user)):
    """Return the Facebook OAuth authorization URL."""
    fb_auth = FacebookAuth()
    authorization_url = fb_auth.get_authorization_url()
    return {"authorization_url": authorization_url, "user_id": str(current_user.id)}


@router.get("/facebook/token-status")
async def facebook_token_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return Facebook connection status (no raw tokens)."""
    status = await ConnectionService.get_connection_status(
        db=db, user_id=str(current_user.id), provider="facebook"
    )
    return status


@router.delete("/facebook/token")
async def revoke_facebook_token(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the user's Facebook connection."""
    await ConnectionService.revoke_connection(
        db=db, user_id=str(current_user.id), provider="facebook"
    )
    logger.info(f"[Facebook] Connection revoked for user {current_user.id}")
    return {"message": "Facebook connection revoked"}
