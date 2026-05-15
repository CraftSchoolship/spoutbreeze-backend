"""
Endpoints that let an authenticated user act on their *own* organization.

- ``GET /api/me/organization`` — any authenticated user can read the org they
  belong to (or get a 404 if unassigned).
- ``GET /api/me/organization/overview`` — org-scoped analytics snapshot.
- ``GET /api/me/organization/users`` — list members of the user's org.
- ``PATCH /api/me/organization/users/{user_id}/role`` — change a member's
  role within the org (limited to moderator ↔ admin).

The latter three require the caller to have the ``admin`` role AND to be
assigned to an organization. Super-admin actions still live on the
existing ``/api/admin/*`` endpoints; this controller is intentionally
scoped to a single org and never reveals platform-wide data.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config.database.session import get_db
from app.config.logger_config import get_logger
from app.models.admin_schemas import AnalyticsOverview
from app.models.organization_models import Organization
from app.models.organization_schemas import OrganizationResponse
from app.models.user_models import User
from app.models.user_schemas import UpdateUserRoleRequest, UserResponse
from app.services.admin_analytics_service import AdminAnalyticsService
from app.services.auth_service import AuthService
from app.services.cached.user_service_cached import user_service_cached

logger = get_logger("OrgAdminController")
auth_service = AuthService()

router = APIRouter(prefix="/api/me/organization", tags=["My Organization"])

# Roles an org admin is allowed to assign within their own org. Promoting
# anyone to ``super_admin`` is strictly a platform-owner action and stays on
# the existing super-admin endpoint.
ORG_ADMIN_MANAGEABLE_ROLES = ("moderator", "admin")


def require_org_admin(current_user: User = Depends(get_current_user)) -> User:
    """
    Dependency: caller must have the ``admin`` role and be assigned to an
    organization. Returns the caller for use in the endpoint body.
    """
    if not current_user.has_role("admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin role required.",
        )
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not assigned to an organization.",
        )
    return current_user


async def _load_org_with_domains(db: AsyncSession, org_id: UUID) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    return result.scalar_one_or_none()


def _serialize_org(org: Organization) -> OrganizationResponse:
    return OrganizationResponse(
        id=org.id,
        name=org.name,
        is_active=org.is_active,
        email_domains=sorted(d.domain for d in org.email_domains),
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


@router.get("", response_model=OrganizationResponse)
async def get_my_organization(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationResponse:
    """
    Return the organization the authenticated user belongs to.

    Any authenticated user can call this. 404 when the user has no org.
    """
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not assigned to an organization.",
        )
    org = await _load_org_with_domains(db, current_user.organization_id)
    if org is None:
        # The user's org row was deleted concurrently; treat as unassigned.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not assigned to an organization.",
        )
    return _serialize_org(org)


@router.get("/overview", response_model=AnalyticsOverview)
async def get_my_organization_overview(
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsOverview:
    """
    Analytics snapshot scoped to the caller's organization.

    Wraps the existing ``AdminAnalyticsService.get_overview`` with an
    ``org_filter`` of the caller's organization. The ``organizations``
    rollup field is overwritten to contain only the caller's org row so an
    org admin cannot see other organizations' totals.
    """
    data = await AdminAnalyticsService.get_overview(db, current_user.organization_id)

    # Strip the platform-wide rollup down to just the caller's own org so
    # an org admin can't enumerate other organizations' metrics.
    org_id_str = str(current_user.organization_id)
    data["organizations"] = [row for row in data["organizations"] if row.get("id") and str(row["id"]) == org_id_str]
    return AnalyticsOverview.model_validate(data)


@router.get("/users", response_model=list[UserResponse])
async def list_my_organization_users(
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> list[User]:
    """List every user assigned to the caller's organization."""
    result = await db.execute(
        select(User).where(User.organization_id == current_user.organization_id).order_by(User.created_at.desc())
    )
    return list(result.scalars().all())


@router.patch("/users/{user_id}/role", response_model=UserResponse)
async def update_my_organization_user_role(
    role_data: UpdateUserRoleRequest,
    user_id: UUID = Path(..., title="The ID of the user to update"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_org_admin),
):
    """
    Change a member's role *within* the caller's organization.

    Guarded against:
    - changing a user outside the caller's org (404, looks unfound)
    - changing oneself (400)
    - touching a super_admin (403)
    - assigning anything other than moderator/admin (400)
    """
    request_id = str(uuid.uuid4())

    target_result = await db.execute(select(User).where(User.id == user_id))
    target_user = target_result.scalar_one_or_none()

    # Pretend "not found" rather than "not in your org" — leaks no info
    # about users outside the caller's organization.
    if target_user is None or target_user.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found in your organization.",
        )

    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot modify your own role.",
        )

    if "super_admin" in target_user.get_roles_list():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot change the role of a super admin.",
        )

    new_role = role_data.role.strip().lower()
    if new_role not in ORG_ADMIN_MANAGEABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role '{new_role}' is not allowed. Allowed roles: {', '.join(ORG_ADMIN_MANAGEABLE_ROLES)}",
        )

    logger.info(f"[{request_id}] Org admin {current_user.username} changing role of {target_user.username} to {new_role}")

    # Keycloak first, then DB — same ordering as super-admin role updates so
    # a Keycloak failure leaves the DB untouched.
    try:
        await auth_service.update_user_role(user_id=target_user.keycloak_id, new_role=new_role)
    except HTTPException as e:
        logger.error(f"[{request_id}] Keycloak role update failed: {e.detail}")
        raise HTTPException(
            status_code=e.status_code,
            detail=f"Failed to update role in Keycloak: {e.detail}",
        ) from e

    updated_user = await user_service_cached.update_user_role(user_id=user_id, new_role=new_role, db=db)
    return updated_user
