"""Shared FastAPI dependencies.

Single source of truth for authentication-related dependencies. The
duplicate `get_current_user` that previously lived in both
`user_controller.py` (cookie-only) and `payment_controller.py` (header
or cookie) had drifted, which meant any auth-layer tightening applied to
one silently missed the other. This module is the unified version.
"""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database.session import get_db
from app.config.logger_config import logger
from app.models.user_models import User
from app.services.auth_service import AuthService
from app.services.cached.user_service_cached import user_service_cached

_auth_service = AuthService()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the authenticated user from a Firebase `session` HTTP-only
    cookie OR an `Authorization: Bearer <id_token>` header.

    The header is checked first so cross-origin / API-style callers (CLI
    scripts, the BBB plugin, etc.) can pass a raw Firebase ID token. The
    session cookie is the fallback for browser navigations — it's a Firebase
    session cookie minted at sign-in and carries the `roles` custom claim.

    The decoded token is stashed on `user._token_payload` so callers that need
    claims straight from the JWT can read it without re-verifying.

    Raises a 401 on any failure — credential missing, invalid, expired/revoked,
    or no matching user row in the DB.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        auth_header = request.headers.get("Authorization")

        if auth_header and auth_header.lower().startswith("bearer "):
            # API-style caller: a raw Firebase ID token.
            id_token = auth_header.split(" ", 1)[1].strip()
            if not id_token:
                raise credentials_exception
            payload = await _auth_service.verify_id_token(id_token, check_revoked=True)
        else:
            # Browser caller: the session cookie set at sign-in.
            session_cookie = request.cookies.get("session")
            if not session_cookie:
                raise credentials_exception
            payload = await _auth_service.verify_session_cookie(session_cookie, check_revoked=True)

        firebase_uid = payload.get("uid") or payload.get("sub")
        if not firebase_uid:
            raise credentials_exception

        user = await user_service_cached.get_user_by_firebase_uid_cached(firebase_uid, db)
        if user is None:
            raise credentials_exception

        # Temporary attribute, not persisted — used by callers that read
        # claims straight from the JWT.
        user._token_payload = payload  # type: ignore[attr-defined]

        return user

    except HTTPException:
        raise credentials_exception
    except Exception as e:
        logger.error(f"Authentication error: {str(e)}")
        raise credentials_exception
