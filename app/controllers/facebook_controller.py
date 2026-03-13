import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database.session import get_db
from app.config.facebook_auth import FacebookAuth
from app.config.settings import get_settings
from app.controllers.user_controller import get_current_user
from app.models.user_models import User
from app.services.connection_service import ConnectionService

router = APIRouter(prefix="/auth", tags=["Facebook Authentication"])
logger = logging.getLogger(__name__)

FACEBOOK_SCOPES = [
    "publish_video",
    "pages_manage_posts",
    "pages_read_engagement",
]


class GoLiveRequest(BaseModel):
    target: str = "me"  # "me" for user profile, or a Page ID
    title: str = "SpoutBreeze Live"
    privacy: str = "EVERYONE"  # EVERYONE, ALL_FRIENDS, SELF


# OAuth Callback


@router.get("/facebook/callback")
async def facebook_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    error_reason: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle Facebook OAuth callback — exchange code, store user + page connections, redirect."""
    settings = get_settings()

    if error:
        logger.error(f"[Facebook] OAuth error for user {current_user.id}: {error} - {error_reason}")
        error_params = urlencode({"tab": "integrations", "facebook_error": error})
        return RedirectResponse(url=f"{settings.frontend_url}/settings?{error_params}", status_code=302)

    if not code:
        error_params = urlencode({"tab": "integrations", "facebook_error": "invalid_callback"})
        return RedirectResponse(url=f"{settings.frontend_url}/settings?{error_params}", status_code=302)

    try:
        fb_auth = FacebookAuth()

        # Exchange code for short-lived token
        short_token_data = await fb_auth.exchange_code_for_token(code)

        # Exchange short-lived token for long-lived token (~60 days)
        long_token_data = await fb_auth.exchange_for_long_lived_token(short_token_data["access_token"])

        # Store user connection (provider="facebook")
        # Facebook doesn't return a refresh_token — the long-lived token IS the
        # refreshable credential. We store it as both access_token and refresh_token.
        user_token = long_token_data["access_token"]
        user_token_data = {
            "access_token": user_token,
            "refresh_token": user_token,  # same token used for refresh
            "expires_in": long_token_data.get("expires_in", 5184000),  # ~60 days
        }

        await ConnectionService.save_connection(
            db=db,
            user_id=str(current_user.id),
            provider="facebook",
            token_data=user_token_data,
            scopes=FACEBOOK_SCOPES,
        )
        logger.info(f"[Facebook] User connection saved for {current_user.id}")

        # Fetch and store Page connections (provider="facebook_page")
        try:
            pages = await fb_auth.get_user_pages(user_token)
            for page in pages:
                page_id = page.get("id")
                page_token = page.get("access_token")
                page_name = page.get("name", "")

                if not page_id or not page_token:
                    continue

                # Page tokens obtained from a long-lived user token are
                # long-lived and never expire — set a far-future expiry
                page_token_data = {
                    "access_token": page_token,
                    "refresh_token": page_token,
                    "expires_in": 5184000,  # ~60 days (safe default)
                }

                await ConnectionService.save_connection(
                    db=db,
                    user_id=str(current_user.id),
                    provider="facebook_page",
                    token_data=page_token_data,
                    scopes=FACEBOOK_SCOPES,
                    provider_user_id=page_id,
                )
                logger.info(f"[Facebook] Page '{page_name}' ({page_id}) saved for {current_user.id}")
        except Exception as e:
            logger.warning(f"[Facebook] Failed to fetch/store pages: {e}")
            # Don't fail the entire callback — user token is already saved

        success_params = urlencode({"tab": "integrations", "facebook_success": "true"})
        return RedirectResponse(url=f"{settings.frontend_url}/settings?{success_params}", status_code=302)

    except Exception as e:
        logger.error(f"[Facebook] Auth failed for user {current_user.id}: {e}")
        error_params = urlencode({"tab": "integrations", "facebook_error": "auth_failed"})
        return RedirectResponse(url=f"{settings.frontend_url}/settings?{error_params}", status_code=302)


# Auth Endpoints


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
    """Return Facebook user connection status (no raw tokens)."""
    status = await ConnectionService.get_connection_status(db=db, user_id=str(current_user.id), provider="facebook")
    return status


@router.delete("/facebook/token")
async def revoke_facebook_token(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the user's Facebook connection (user + all pages)."""
    # Revoke user connection
    await ConnectionService.revoke_connection(db=db, user_id=str(current_user.id), provider="facebook")
    # Revoke all page connections
    await ConnectionService.revoke_all_connections(db=db, user_id=str(current_user.id), provider="facebook_page")
    logger.info(f"[Facebook] All connections revoked for user {current_user.id}")
    return {"message": "Facebook connection revoked"}


# Pages


@router.get("/facebook/pages")
async def facebook_pages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the user's connected Facebook Pages."""
    pages = await ConnectionService.get_connections_by_provider(db=db, user_id=str(current_user.id), provider="facebook_page")
    return {
        "pages": [
            {
                "page_id": p.provider_user_id,
                "is_active": p.is_active,
                "is_expired": p.is_expired,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in pages
        ]
    }


# Go Live


@router.post("/facebook/go-live")
async def facebook_go_live(
    body: GoLiveRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Facebook LiveVideo and return the RTMP URL + stream key.

    The stream goes live immediately (status=LIVE_NOW).
    No need to click 'Go Live' in Facebook's UI.
    """
    user_id = str(current_user.id)

    # Determine which token to use based on target
    if body.target == "me":
        # User profile stream — use the user's facebook token
        token = await ConnectionService.get_decrypted_token(db=db, user_id=user_id, provider="facebook")
        if not token:
            raise HTTPException(status_code=404, detail="No Facebook connection found. Please connect first.")
        access_token = token["access_token"]
        target_id = "me"
    else:
        # Page stream — use the page's token
        token = await ConnectionService.get_decrypted_token(
            db=db,
            user_id=user_id,
            provider="facebook_page",
            provider_user_id=body.target,
        )
        if not token:
            raise HTTPException(
                status_code=404,
                detail=f"No Facebook Page connection found for page {body.target}.",
            )
        access_token = token["access_token"]
        target_id = body.target

    try:
        fb_auth = FacebookAuth()
        result = await fb_auth.create_live_video(
            access_token=access_token,
            target_id=target_id,
            title=body.title,
            privacy=body.privacy,
        )

        logger.info(f"[Facebook] Go live: {result['live_video_id']} for user {user_id} on {target_id}")

        return {
            "live_video_id": result["live_video_id"],
            "rtmp_url": result["rtmp_url"],
            "stream_key": result["stream_key"],
            "stream_url": result["stream_url"],
            "target": target_id,
        }

    except Exception as e:
        logger.error(f"[Facebook] Go live failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to go live: {str(e)}")


@router.post("/facebook/end-live")
async def facebook_end_live(
    live_video_id: str = Body(..., embed=True),
    target: str = Body("me", embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """End a Facebook live broadcast."""
    user_id = str(current_user.id)

    if target == "me":
        token = await ConnectionService.get_decrypted_token(db=db, user_id=user_id, provider="facebook")
    else:
        token = await ConnectionService.get_decrypted_token(
            db=db,
            user_id=user_id,
            provider="facebook_page",
            provider_user_id=target,
        )

    if not token:
        raise HTTPException(status_code=404, detail="No Facebook connection found.")

    try:
        fb_auth = FacebookAuth()
        result = await fb_auth.end_live_video(
            access_token=token["access_token"],
            live_video_id=live_video_id,
        )
        logger.info(f"[Facebook] Live ended: {live_video_id} for user {user_id}")
        return {"message": "Live video ended", "id": result.get("id")}
    except Exception as e:
        logger.error(f"[Facebook] End live failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to end live: {str(e)}")
