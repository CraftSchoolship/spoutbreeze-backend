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
    """Resolve the authenticated user from `Authorization: Bearer <token>`
    OR an `access_token` HTTP-only cookie.

    The header is checked first so cross-origin / API-style callers (CLI
    scripts, the BBB plugin, etc.) work without a cookie. The cookie is
    the fallback for browser navigations where the SPA can't attach a
    custom header.

    The token payload is stashed on `user._token_payload` so callers that
    need realm/role data straight from the JWT (the `extract_keycloak_roles`
    flow) can read it without re-validating the token.

    Raises a 401 on any failure — token missing, invalid, expired, or no
    matching user row in the DB.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # Authorization header takes precedence; fall back to the cookie.
        auth_header = request.headers.get("Authorization")
        token: str | None = None

        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
        else:
            token = request.cookies.get("access_token")

        if not token:
            raise credentials_exception

        payload = await _auth_service.validate_token(token)
        keycloak_id = payload.get("sub")
        if not keycloak_id:
            raise credentials_exception

        user = await user_service_cached.get_user_by_keycloak_id_cached(keycloak_id, db)
        if user is None:
            raise credentials_exception

        # Temporary attribute, not persisted — used by callers that read
        # roles or realm metadata straight from the JWT.
        user._token_payload = payload  # type: ignore[attr-defined]

        return user

    except HTTPException:
        raise credentials_exception
    except Exception as e:
        logger.error(f"Authentication error: {str(e)}")
        raise credentials_exception
