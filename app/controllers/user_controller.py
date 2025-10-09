from fastapi import APIRouter, Depends, HTTPException, status, Request, Path
from typing import List
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.auth_service import AuthService
from app.config.database.session import get_db
from app.models.user_models import User
from app.models.user_schemas import (
    UserResponse,
    UpdateProfileRequest,
    UpdateUserRoleRequest,
)
from app.config.logger_config import logger
from app.config.settings import get_settings
from app.services.cached.user_service_cached import user_service_cached
from app.config.redis_config import cache
import uuid
import inspect


auth_service = AuthService()
settings = get_settings()

router = APIRouter(prefix="/api", tags=["Users"])


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Get the current authenticated user from HTTP-only cookie with caching
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # Get access token from cookie instead of Authorization header
        access_token = request.cookies.get("access_token")

        if not access_token:
            raise credentials_exception

        # Validate token and get payload
        payload = auth_service.validate_token(access_token)

        # Extract user identifier from token
        keycloak_id = payload.get("sub")
        if keycloak_id is None:
            raise credentials_exception

        # Get user from database with caching
        user = await user_service_cached.get_user_by_keycloak_id_cached(keycloak_id, db)

        if user is None:
            raise credentials_exception

        # Store the token payload in the user object for role extraction
        # This is a temporary attribute, not persisted to database
        user._token_payload = payload

        return user

    except HTTPException:
        raise credentials_exception
    except Exception as e:
        # Log the error for debugging
        logger.error(f"Authentication error: {str(e)}")
        raise credentials_exception


def get_current_user_roles(current_user: User = Depends(get_current_user)) -> List[str]:
    """
    Get roles from the database (stored from Keycloak) with caching
    """
    user_roles = current_user.get_roles_list()
    logger.info(f"User {current_user.username} roles: {user_roles}")
    return user_roles


def require_role(required_role: str):
    """
    Create a dependency that checks for a specific role using database roles
    """

    def role_checker(current_user: User = Depends(get_current_user)):
        if not current_user.has_role(required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{required_role.capitalize()} role is required to access this resource",
            )
        return True

    return role_checker


def require_any_role(*required_roles: str):
    """
    Create a dependency that checks for any of the specified roles using database roles
    """

    def role_checker(current_user: User = Depends(get_current_user)):
        if not current_user.has_any_role(*required_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"One of roles {required_roles} is required to access this resource",
            )
        return True

    return role_checker


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Get the current user information (cached via get_current_user)
    """
    return current_user


@router.put("/me/profile", response_model=UserResponse)
async def update_user_profile(
    update_data: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update the current user's profile information with cache invalidation
    """
    request_id = str(uuid.uuid4())
    logger.info(
        f"[{request_id}] Starting profile update for user: {current_user.username}"
    )

    try:
        profile_update_data = {}

        if update_data.email is not None:
            profile_update_data["email"] = update_data.email.strip().lower()
            profile_update_data["username"] = update_data.email.strip().lower()
        if update_data.first_name is not None:
            profile_update_data["first_name"] = update_data.first_name.strip()
        if update_data.last_name is not None:
            profile_update_data["last_name"] = update_data.last_name.strip()

        if not profile_update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No profile data provided to update",
            )

        logger.info(
            f"[{request_id}] Updating Keycloak profile for user: {current_user.keycloak_id}"
        )

        # Update user in Keycloak first
        auth_service.update_user_profile(
            user_id=current_user.keycloak_id, user_data=profile_update_data
        )

        logger.info(
            f"[{request_id}] Updating database profile for user: {current_user.username}"
        )

        # Update user in the database using cached service
        updated_user = await user_service_cached.update_user_profile(
            user_id=current_user.id, updates=profile_update_data, db=db
        )

        logger.info(
            f"[{request_id}] Profile update completed successfully for user: {current_user.username}"
        )
        return updated_user

    except HTTPException as e:
        await db.rollback()
        logger.error(f"[{request_id}] HTTP error during profile update: {str(e)}")
        raise e
    except Exception as e:
        await db.rollback()
        logger.error(f"[{request_id}] Unexpected error updating user profile: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user profile",
        )


@router.get("/users", response_model=List[UserResponse])
async def get_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: bool = Depends(require_role("admin")),
):
    """
    Get a list of users (Admin only) with caching
    """
    logger.info(f"Admin user {current_user.username} is requesting users list")
    try:
        users = await user_service_cached.get_users_list_cached(skip, limit, db)
        return users
    except Exception as e:
        logger.error(f"Error fetching users list: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve users",
        )


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user_by_id(
    user_id: UUID = Path(..., title="The ID of the user to get"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: bool = Depends(require_any_role("admin", "moderator")),
):
    """
    Get a user by ID (Admin/Moderator only) with caching
    """
    logger.info(f"User {current_user.username} is requesting user {user_id}")

    try:
        user = await user_service_cached.get_user_by_id_cached(user_id, db)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found",
            )

        return user
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch user",
        )


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    role_data: UpdateUserRoleRequest,
    user_id: UUID = Path(..., title="The ID of the user to update"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: bool = Depends(require_role("admin")),
):
    """
    Update a user's role (Admin only) with cache invalidation
    """
    request_id = str(uuid.uuid4())
    logger.info(
        f"[{request_id}] Admin {current_user.username} updating role for user {user_id} to {role_data.role}"
    )

    try:
        # Get the target user using cached service
        target_user = await user_service_cached.get_user_by_id_cached(user_id, db)

        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found",
            )

        # Validate role format
        new_role = role_data.role.strip().lower()
        if not new_role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Role cannot be empty",
            )

        # Add validation for allowed roles
        allowed_roles = ["admin", "moderator"]
        if new_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Role '{new_role}' is not allowed. Allowed roles: {', '.join(allowed_roles)}",
            )

        # Prevent admin from changing their own role
        if target_user.id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot modify your own role",
            )

        logger.info(
            f"[{request_id}] Updating role in Keycloak for user: {target_user.keycloak_id}"
        )

        # Update role in Keycloak first
        try:
            auth_service.update_user_role(
                user_id=target_user.keycloak_id, new_role=new_role
            )
        except HTTPException as e:
            logger.error(f"[{request_id}] Keycloak role update failed: {e.detail}")
            raise HTTPException(
                status_code=e.status_code,
                detail=f"Failed to update role in Keycloak: {e.detail}",
            )

        logger.info(
            f"[{request_id}] Updating role in database for user: {target_user.username}"
        )

        # Update role in the database using cached service
        updated_user = await user_service_cached.update_user_role(
            user_id=user_id, new_role=new_role, db=db
        )

        logger.info(
            f"[{request_id}] Role update completed successfully for user: {target_user.username}"
        )
        return updated_user

    except HTTPException as e:
        await db.rollback()
        logger.error(f"[{request_id}] HTTP error during role update: {str(e)}")
        raise e
    except Exception as e:
        await db.rollback()
        logger.error(f"[{request_id}] Unexpected error updating user role: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user role",
        )


# Add cache management endpoints for admin users
@router.post("/cache/invalidate/{user_id}")
async def invalidate_user_cache(
    user_id: UUID = Path(..., title="The ID of the user to invalidate cache for"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),  # Properly inject the database session
    _: bool = Depends(require_role("admin")),
):
    """
    Manually invalidate cache for a specific user (Admin only)
    """
    try:
        # Get user to find keycloak_id using the properly injected db session
        user = await user_service_cached.get_user_by_id_cached(user_id, db)
        keycloak_id = user.keycloak_id if user else None

        await user_service_cached.invalidate_user_cache(user_id, keycloak_id)

        logger.info(
            f"Admin {current_user.username} invalidated cache for user {user_id}"
        )
        return {"message": f"Cache invalidated for user {user_id}"}
    except Exception as e:
        logger.error(f"Failed to invalidate cache for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to invalidate user cache",
        )


@router.get("/cache/stats")
async def get_cache_stats(
    current_user: User = Depends(get_current_user),
    _: bool = Depends(require_role("admin")),
):
    """
    Get cache statistics (Admin only)
    """
    try:
        healthy = False
        health_check = getattr(cache, "health_check", None)
        if callable(health_check):
            # Support both async and sync implementations
            if inspect.iscoroutinefunction(health_check):
                healthy = await health_check()
            else:
                result = health_check()
                # If the sync function still returns a coroutine, await it
                if inspect.isawaitable(result):
                    healthy = await result
                else:
                    healthy = bool(result)
    except Exception as e:
        logger.error(f"Cache health check error: {e}")
        healthy = False
    # Example known cache patterns used by services
    cache_patterns = [
        "user:*",
        "users:list*",
        "channel:*",
        "event:*",
    ]
    return {
        "cache_status": "healthy" if healthy else "unhealthy",
        "cache_patterns": cache_patterns,
    }
