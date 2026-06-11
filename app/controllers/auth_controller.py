from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database.session import get_db
from app.config.logger_config import logger
from app.config.redis_config import cache
from app.config.settings import get_settings
from app.controllers.user_controller import get_current_user
from app.models.auth_models import PasswordResetRequest, SessionRequest
from app.models.organization_models import OrganizationEmailDomain
from app.models.user_models import User
from app.services.auth_service import AuthService
from app.services.email_template_renderer import render_email
from app.utils.email import send_email
from app.utils.rate_limit import limiter

settings = get_settings()

router = APIRouter(prefix="/api", tags=["Authentication"])

auth_service = AuthService()

# Session cookie lifetime — must match AuthService.SESSION_COOKIE_MAX_AGE.
SESSION_COOKIE_MAX_AGE_SECONDS = 14 * 24 * 60 * 60


class ProtectedRouteResponse(BaseModel):
    message: str


def set_session_cookie(response: Response, session_cookie: str) -> None:
    """Set the Firebase session cookie as an httpOnly cookie.

    Replaces the previous access_token + refresh_token pair — the Firebase
    session cookie is a single long-lived JWT that the Next.js middleware
    decodes for route guarding (it carries the ``roles`` custom claim).
    """
    is_production = settings.env == "production"
    cookie_domain = settings.domain if is_production else None
    samesite_setting: Literal["lax", "strict", "none"] = "none" if is_production else "lax"
    expires = datetime.now(UTC) + timedelta(seconds=SESSION_COOKIE_MAX_AGE_SECONDS)

    response.set_cookie(
        key="session",
        value=session_cookie,
        expires=expires,
        max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=is_production,
        samesite=samesite_setting,
        path="/",
        domain=cookie_domain,
    )


def clear_auth_cookies(response: Response) -> None:
    """Clear the session cookie."""
    cookie_domain = settings.domain if settings.env == "production" else None
    response.delete_cookie("session", path="/", domain=cookie_domain)
    # Best-effort cleanup of legacy Keycloak-era cookies for users mid-migration.
    response.delete_cookie("access_token", path="/", domain=cookie_domain)
    response.delete_cookie("refresh_token", path="/", domain=cookie_domain)


def extract_firebase_roles(decoded_token: dict) -> list[str] | None:
    """Extract the ``roles`` custom claim from a decoded Firebase token.

    Returns None when no roles claim is present so the database default is
    preserved (matches the previous Keycloak behaviour).
    """
    roles = decoded_token.get("roles")
    if isinstance(roles, list) and roles:
        return [str(r) for r in roles]
    return None


def _split_name(decoded_token: dict, body: SessionRequest) -> tuple[str, str]:
    """Resolve first/last name from the request body or the token's ``name``."""
    if body.first_name or body.last_name:
        return (body.first_name or "").strip(), (body.last_name or "").strip()
    full_name = str(decoded_token.get("name", "")).strip()
    if not full_name:
        return "", ""
    parts = full_name.split(" ", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


async def process_user_info(decoded_token: dict, body: SessionRequest, db: AsyncSession) -> User:
    """Create or update the local user row from a verified Firebase token.

    Returns the User. For brand-new users the default role's custom claim is
    written to Firebase so it rides in subsequent tokens.
    """
    firebase_uid = str(decoded_token.get("uid") or decoded_token.get("sub"))
    email = str(decoded_token.get("email", "")).strip().lower()

    # Match by firebase_uid first, then fall back to email. The email fallback
    # reconciles a row that exists under a stale/migrated uid (e.g. the user was
    # imported under their old uid but signed up fresh): the verified ID token
    # proves control of this email, so the live Firebase uid is authoritative
    # and we rebind the existing row to it instead of colliding on the unique
    # email constraint.
    stmt = select(User).where(User.firebase_uid == firebase_uid)
    result = await db.execute(stmt)
    existing_user = result.scalars().first()

    if existing_user is None and email:
        by_email = await db.execute(select(User).where(User.email == email))
        existing_user = by_email.scalars().first()

    user_roles = extract_firebase_roles(decoded_token)
    first_name, last_name = _split_name(decoded_token, body)

    if not existing_user:
        new_user = User(
            firebase_uid=firebase_uid,
            # Firebase has no username concept; email is unique, use it.
            username=email or firebase_uid,
            email=email,
            first_name=first_name,
            last_name=last_name,
        )
        if user_roles is not None:
            new_user.set_roles_list(user_roles)

        # Auto-assign organization by verified email domain (first login only).
        domain = email.rpartition("@")[-1].strip().lower()
        if domain:
            domain_row = await db.execute(
                select(OrganizationEmailDomain).where(
                    OrganizationEmailDomain.domain == domain,
                    OrganizationEmailDomain.verified_at.is_not(None),
                )
            )
            match = domain_row.scalar_one_or_none()
            if match:
                new_user.organization_id = match.organization_id
                new_user.has_completed_onboarding = True
                logger.info(
                    f"Auto-assigned new user {new_user.username} to organization "
                    f"{match.organization_id} via verified domain {domain}"
                )

        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        logger.info(f"New user created: {new_user.username}, firebase_uid={new_user.firebase_uid}, roles={new_user.roles}")

        # Mirror the DB role into a Firebase custom claim so middleware sees it
        # on the next token refresh. Best-effort — a failure here doesn't block
        # sign-in (the DB remains the source of truth for the backend).
        try:
            await auth_service.set_roles_claim(firebase_uid, new_user.get_roles_list())
        except Exception as e:
            logger.warning(f"Failed to set initial roles claim for {firebase_uid}: {e}")

        try:
            await cache.delete_pattern("users_list:*")
        except Exception as e:
            logger.warning(f"Failed to invalidate users_list cache after new signup: {e}")
        return new_user

    # Update the existing user. Only overwrite name fields when we actually
    # have a value, so we don't blank out a profile on a bare Google login.
    if existing_user.firebase_uid != firebase_uid:
        logger.info(
            f"Rebinding user {existing_user.id} from firebase_uid "
            f"{existing_user.firebase_uid} → {firebase_uid} (matched by email)"
        )
        existing_user.firebase_uid = firebase_uid
    if email:
        existing_user.email = email
    if first_name:
        existing_user.first_name = first_name
    if last_name:
        existing_user.last_name = last_name
    existing_user.updated_at = datetime.now()
    if user_roles is not None:
        existing_user.set_roles_list(user_roles)

    await db.commit()
    await db.refresh(existing_user)
    logger.info(f"User updated: {existing_user.username}, firebase_uid={existing_user.firebase_uid}")
    return existing_user


@router.get("/protected", response_model=ProtectedRouteResponse)
async def protected_route(current_user: User = Depends(get_current_user)):
    """Protected route that requires authentication."""
    return {"message": f"Hello, {current_user.username}! This is a protected route."}


@router.post("/session")
@limiter.limit(lambda: settings.rate_limit_token)
async def create_session(
    request: Request,
    body: SessionRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Establish a session from a Firebase ID token.

    Verifies the ID token, upserts the local user, mints a Firebase session
    cookie, and sets it as an httpOnly cookie.
    """
    try:
        decoded = await auth_service.verify_id_token(body.id_token)

        logger.info(f"Session requested for uid={decoded.get('uid')} email={decoded.get('email')}")

        user = await process_user_info(decoded, body, db)

        session_cookie = await auth_service.create_session_cookie(body.id_token)
        set_session_cookie(response, session_cookie)

        return {
            "user_info": {
                "uid": decoded.get("uid"),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "roles": user.get_roles_list(),
            },
            "expires_in": SESSION_COOKIE_MAX_AGE_SECONDS,
            "token_type": "Bearer",
        }
    except HTTPException:
        raise
    except IntegrityError as e:
        await db.rollback()
        logger.error(f"Database integrity error during session creation: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this Firebase UID already exists",
        )
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to establish session",
        )


@router.post("/password-reset")
@limiter.limit(lambda: settings.rate_limit_token)
async def request_password_reset(request: Request, body: PasswordResetRequest, response: Response):
    """Send a self-hosted password-reset email.

    Generates a Firebase reset code via the Admin SDK, embeds it in a link to
    our own ``/auth/action`` page, and emails it through the configured SMTP
    relay. Always returns 200 with the same body regardless of whether the
    email is registered — never reveal account existence.
    """
    email = body.email.strip().lower()
    generic_response = {"message": "If an account exists for that email, a reset link has been sent."}

    continue_url = f"{settings.frontend_url}/auth/signin"
    oob_code = await auth_service.generate_password_reset_code(email, continue_url)

    if oob_code:
        reset_url = f"{settings.frontend_url}/auth/action?mode=resetPassword&oobCode={oob_code}"
        html_body = render_email(
            "password_reset",
            {"title": "Reset your password", "data": {"reset_url": reset_url}},
        )
        await send_email(
            recipient_email=email,
            subject="Reset your BlueScale password",
            text_body=f"Reset your password using this link (expires in 1 hour):\n\n{reset_url}\n\n"
            "If you didn't request this, ignore this email.",
            html_body=html_body,
        )
    else:
        logger.info("Password reset requested for non-existent / unresolved email (suppressed)")

    return generic_response


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Log the user out: revoke Firebase refresh tokens and clear the cookie."""
    try:
        await auth_service.logout(current_user.firebase_uid)
    except Exception as e:
        logger.error(f"Logout error: {e}")
    finally:
        clear_auth_cookies(response)

    return {"message": "Successfully logged out", "statusCode": status.HTTP_200_OK}
