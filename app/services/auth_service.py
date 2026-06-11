"""Authentication service backed by Firebase Authentication.

Replaces the previous Keycloak/OpenID implementation. Sessions use Firebase
**session cookies**: the frontend signs in with the Firebase Web SDK, obtains
an ID token, and the backend exchanges it for a long-lived httpOnly session
cookie (see auth_controller.create_session). Roles are stored both in the
database (source of truth for application logic) and as a Firebase custom
claim (``roles``) so they ride inside the ID token / session cookie and can be
read by the Next.js middleware for route guarding.

The Firebase Admin SDK is synchronous, so every call is dispatched to a
thread with ``asyncio.to_thread`` to avoid blocking the event loop.
"""

import asyncio
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException, status
from firebase_admin import auth as fb_auth

from app.config.firebase_config import get_firebase_app
from app.config.logger_config import logger

# Firebase session cookies can live up to 14 days. After that the user must
# sign in again (the Web SDK refreshes ID tokens silently up to that point).
SESSION_COOKIE_MAX_AGE = timedelta(days=14)


class AuthService:
    """Service for authentication and authorization operations (Firebase)."""

    def __init__(self) -> None:
        # Touch the app so a misconfigured service account fails loudly at
        # first use rather than deep inside a request handler.
        self._app = get_firebase_app()

    def _ensure_app(self) -> None:
        if self._app is None:
            self._app = get_firebase_app()
        if self._app is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication backend is not configured",
            )

    async def verify_id_token(self, id_token: str, check_revoked: bool = False) -> dict[str, Any]:
        """Verify a Firebase ID token (used for Bearer-style API callers).

        Returns the decoded claims (``uid``, ``email``, ``name``, ``roles`` …).
        Raises 401 on any failure.
        """
        self._ensure_app()
        try:
            return await asyncio.to_thread(fb_auth.verify_id_token, id_token, check_revoked=check_revoked)
        except Exception as e:
            logger.error(f"Firebase ID token verification failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # Back-compat alias — callers that previously used ``validate_token`` for a
    # bearer JWT now validate a Firebase ID token.
    async def validate_token(self, token: str) -> dict[str, Any]:
        return await self.verify_id_token(token)

    async def create_session_cookie(self, id_token: str) -> str:
        """Exchange a freshly-minted ID token for a long-lived session cookie."""
        self._ensure_app()
        try:
            return await asyncio.to_thread(
                fb_auth.create_session_cookie,
                id_token,
                expires_in=SESSION_COOKIE_MAX_AGE,
            )
        except Exception as e:
            logger.error(f"Failed to create Firebase session cookie: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to establish session",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def verify_session_cookie(self, session_cookie: str, check_revoked: bool = True) -> dict[str, Any]:
        """Verify a session cookie and return its decoded claims."""
        self._ensure_app()
        try:
            return await asyncio.to_thread(
                fb_auth.verify_session_cookie,
                session_cookie,
                check_revoked=check_revoked,
            )
        except Exception as e:
            logger.error(f"Firebase session cookie verification failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def get_user(self, uid: str) -> fb_auth.UserRecord:
        """Fetch a Firebase user record by uid."""
        self._ensure_app()
        return await asyncio.to_thread(fb_auth.get_user, uid)

    async def generate_password_reset_code(self, email: str, continue_url: str) -> str | None:
        """Generate a password-reset oobCode for ``email``.

        Returns the ``oobCode`` parsed out of the Admin-generated link, or
        ``None`` if the email isn't registered (so callers can stay silent and
        avoid account enumeration). We extract just the code and embed it in our
        own ``/auth/action`` URL — the link Firebase generates otherwise points
        at its hosted handler.
        """
        self._ensure_app()
        try:
            settings = fb_auth.ActionCodeSettings(url=continue_url)
            link = await asyncio.to_thread(
                fb_auth.generate_password_reset_link, email, settings
            )
        except fb_auth.UserNotFoundError:
            return None
        except Exception as e:
            logger.error(f"Failed to generate password reset link: {e}")
            return None

        params = parse_qs(urlparse(link).query)
        codes = params.get("oobCode")
        return codes[0] if codes else None

    async def update_user_profile(self, user_id: str, user_data: dict[str, Any]) -> bool:
        """Update a user's Firebase profile (email / display name).

        ``user_id`` is the Firebase uid. Field names mirror the previous
        Keycloak-era signature so callers don't change.
        """
        self._ensure_app()
        kwargs: dict[str, Any] = {}
        if "email" in user_data:
            kwargs["email"] = user_data["email"]
        first = user_data.get("first_name")
        last = user_data.get("last_name")
        if first is not None or last is not None:
            kwargs["display_name"] = " ".join(p for p in [first, last] if p).strip()

        if not kwargs:
            return True

        try:
            await asyncio.to_thread(fb_auth.update_user, user_id, **kwargs)
            logger.info(f"Updated Firebase profile for uid {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update Firebase profile for uid {user_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to update user info: {e}",
            )

    async def update_user_role(self, user_id: str, new_role: str) -> None:
        """Set the user's role as a Firebase custom claim.

        ``user_id`` is the Firebase uid. The claim propagates into every ID
        token minted afterwards, so the Next.js middleware can read it from the
        session cookie. The database remains the source of truth for app logic.
        """
        await self.set_roles_claim(user_id, [new_role])

    async def set_roles_claim(self, uid: str, roles: list[str]) -> None:
        """Write the ``roles`` custom claim for a user."""
        self._ensure_app()
        try:
            # Preserve any existing non-role claims.
            user = await asyncio.to_thread(fb_auth.get_user, uid)
            claims = dict(user.custom_claims or {})
            claims["roles"] = roles
            await asyncio.to_thread(fb_auth.set_custom_user_claims, uid, claims)
            logger.info(f"Set roles claim {roles} for uid {uid}")
        except Exception as e:
            logger.error(f"Failed to set roles claim for uid {uid}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update user role: {e}",
            )

    async def delete_user(self, user_id: str) -> bool:
        """Permanently delete a user from Firebase. ``user_id`` is the uid."""
        self._ensure_app()
        try:
            await asyncio.to_thread(fb_auth.delete_user, user_id)
            logger.info(f"Deleted Firebase user {user_id}")
            return True
        except fb_auth.UserNotFoundError:
            # Already gone — treat as success so DB cleanup can proceed.
            logger.warning(f"Firebase user {user_id} not found during delete; continuing")
            return True
        except Exception as e:
            logger.error(f"Failed to delete Firebase user {user_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete user from authentication backend: {e}",
            )

    async def logout(self, uid: str) -> None:
        """Revoke all refresh tokens for the user, invalidating sessions.

        ``check_revoked=True`` on session-cookie verification then rejects the
        existing session on its next use.
        """
        self._ensure_app()
        try:
            await asyncio.to_thread(fb_auth.revoke_refresh_tokens, uid)
            logger.info(f"Revoked refresh tokens for uid {uid}")
        except Exception as e:
            # Logout is best-effort; cookie is cleared regardless.
            logger.error(f"Failed to revoke refresh tokens for uid {uid}: {e}")

    async def health_check(self) -> bool:
        """Report whether the Firebase Admin SDK is initialised."""
        try:
            self._ensure_app()
            return True
        except Exception:
            return False
