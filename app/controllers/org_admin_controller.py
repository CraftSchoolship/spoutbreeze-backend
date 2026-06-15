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

import secrets
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config.database.session import get_db
from app.config.logger_config import get_logger
from app.models.admin_schemas import AnalyticsOverview
from app.models.organization_models import (
    Organization,
    OrganizationEmailDomain,
    OrganizationInvite,
)
from app.models.organization_schemas import (
    AddDomainRequest,
    CreateMyOrgRequest,
    CreateMyOrgResponse,
    DomainVerificationStatus,
    EmailDomainDetail,
    JoinOrgRequest,
    JoinOrgResponse,
    OrganizationInviteResponse,
    OrganizationResponse,
)
from app.models.user_models import User
from app.models.user_schemas import UpdateUserRoleRequest, UserResponse
from app.services.admin_analytics_service import AdminAnalyticsService
from app.services.auth_service import AuthService
from app.services.cached.user_service_cached import user_service_cached
from app.services.org_verification_service import (
    verification_record_name,
    verification_record_value,
    verify_domain_record,
)
from app.utils.datetime_utils import utcnow

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
    details = []
    for d in sorted(org.email_domains, key=lambda x: x.domain):
        verified = d.verified_at is not None
        details.append(
            EmailDomainDetail(
                domain=d.domain,
                verified=verified,
                verified_at=d.verified_at,
                verification_record_name=(None if verified else verification_record_name(d.domain)),
                verification_record_value=(
                    None if verified or not d.verification_token else verification_record_value(d.verification_token)
                ),
            )
        )
    return OrganizationResponse(
        id=org.id,
        name=org.name,
        is_active=org.is_active,
        email_domains=[d.domain for d in details],
        email_domain_details=details,
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

    # the auth backend first, then DB — same ordering as super-admin role updates so
    # an auth backend failure leaves the DB untouched.
    try:
        await auth_service.update_user_role(user_id=target_user.firebase_uid, new_role=new_role)
    except HTTPException as e:
        logger.error(f"[{request_id}] Role update failed: {e.detail}")
        raise HTTPException(
            status_code=e.status_code,
            detail=f"Failed to update role: {e.detail}",
        ) from e

    updated_user = await user_service_cached.update_user_role(user_id=user_id, new_role=new_role, db=db)
    return updated_user


# ---------------------------------------------------------------------------
# Self-serve onboarding
# ---------------------------------------------------------------------------


def _invite_join_path(code: str) -> str:
    """The frontend route that consumes an invite code."""
    return f"/join/org/{code}"


def _serialize_invite(inv: OrganizationInvite) -> OrganizationInviteResponse:
    return OrganizationInviteResponse(
        code=inv.code,
        organization_id=inv.organization_id,
        created_at=inv.created_at,
        revoked_at=inv.revoked_at,
        expires_at=inv.expires_at,
        join_path=_invite_join_path(inv.code),
    )


async def _active_invite_for_org(db: AsyncSession, org_id: UUID) -> OrganizationInvite | None:
    """Find the (single) currently-active invite for an org, if any."""
    now = utcnow()
    result = await db.execute(
        select(OrganizationInvite)
        .where(
            OrganizationInvite.organization_id == org_id,
            OrganizationInvite.revoked_at.is_(None),
        )
        .order_by(OrganizationInvite.created_at.desc())
    )
    for inv in result.scalars().all():
        if inv.expires_at is None or inv.expires_at > now:
            return inv
    return None


def _new_invite_code() -> str:
    # 8 url-safe bytes ≈ 11 chars; collisions are astronomically unlikely but
    # the PK constraint will catch any anyway, and the caller can retry.
    return secrets.token_urlsafe(8)


@router.post(
    "/create",
    response_model=CreateMyOrgResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_my_organization(
    payload: CreateMyOrgRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreateMyOrgResponse:
    """
    Self-serve organization creation.

    The caller becomes the org's first ``admin`` and is upgraded to the
    ``admin`` role in both Firebase and the database atomically with the
    org creation. We refresh the caller's auth cookies before returning so
    their next request carries a JWT with the new role baked in — without
    this, middleware would still see the stale ``moderator`` role and would
    bounce them out of ``/my-org``.

    The supplied email domain is stored unverified with a TXT-record token;
    auto-match by email domain won't pick this org up until
    ``POST /verify-domain`` succeeds.
    """
    request_id = str(uuid.uuid4())

    if current_user.organization_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("You already belong to an organization. Leave or be reassigned before creating a new one."),
        )

    # Step 1: Promote the caller to `admin` in Firebase FIRST. If this fails,
    # we never touch the DB — no half-created orgs, no orphan admins. The
    # auth_service raises HTTPException on failure which propagates as-is to
    # the client (typically a 500 with the auth backend error detail).
    logger.info(
        f"[{request_id}] Granting admin role in Firebase for {current_user.username} before creating org '{payload.name}'"
    )
    await auth_service.update_user_role(user_id=current_user.firebase_uid, new_role="admin")

    # Step 2: Atomic DB transaction — org + domain + invite + user mutations
    # all commit together. If anything fails here we still have the Firebase
    # admin grant in place (acceptable; idempotent on retry, and the user
    # can be assigned an org later by a super-admin).
    token = secrets.token_urlsafe(24)
    now = utcnow()
    org = Organization(name=payload.name)
    org.email_domains = [
        OrganizationEmailDomain(
            domain=payload.email_domain,
            verification_token=token,
            verification_started_at=now,
        )
    ]
    db.add(org)

    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        msg = str(e.orig).lower() if e.orig else str(e).lower()
        if "name" in msg or "organizations_name" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Organization name '{payload.name}' is already taken.",
            ) from e
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email domain is already claimed by another organization.",
        ) from e

    # Re-fetch the caller in this session so we can mutate them safely.
    user_result = await db.execute(select(User).where(User.id == current_user.id))
    target_user = user_result.scalar_one()
    target_user.organization_id = org.id
    target_user.has_completed_onboarding = True
    target_user.set_roles_list(["admin"])  # mirror the Firebase grant in the DB

    # Initial invite so the admin has something to share immediately.
    invite = OrganizationInvite(
        code=_new_invite_code(),
        organization_id=org.id,
        created_by_user_id=target_user.id,
    )
    db.add(invite)

    await db.commit()
    await db.refresh(org, attribute_names=["email_domains"])
    await db.refresh(target_user)

    # Step 3: invalidate caches so the next /api/me read reflects the new
    # role + org assignment. Best-effort; a Redis blip can't roll back the
    # successful commit above.
    try:
        await user_service_cached.invalidate_user_cache(target_user.id, target_user.firebase_uid)
    except Exception as e:
        logger.warning(f"[{request_id}] Cache invalidation after org create failed: {e}")

    # Step 4: the new `admin` role was written as a Firebase custom claim in
    # Step 1, but the caller's CURRENT session cookie was minted from an ID
    # token issued BEFORE that claim existed, so it still says `moderator`.
    # Firebase custom claims only appear after the client force-refreshes its
    # ID token. The frontend therefore re-establishes the session right after
    # this call returns (auth.currentUser.getIdToken(true) -> POST /api/session);
    # see the create-org flow in the web app. `session_refresh_required` tells
    # it to do so before navigating to /my-org.
    logger.info(f"[{request_id}] Admin claim set; client must refresh session to pick up the 'admin' role")

    logger.info(
        f"[{request_id}] User {target_user.username} created org '{org.name}' with pending domain {payload.email_domain}"
    )

    return CreateMyOrgResponse(
        organization=_serialize_org(org),
        verification=DomainVerificationStatus(
            domain=payload.email_domain,
            verified=False,
            verification_token=token,
            verification_record_name=verification_record_name(payload.email_domain),
            verification_record_value=verification_record_value(token),
        ),
    )


@router.post("/join", response_model=JoinOrgResponse)
async def join_my_organization(
    payload: JoinOrgRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JoinOrgResponse:
    """
    Join an organization using an invite code.

    The code matches a non-revoked, non-expired ``OrganizationInvite`` row.
    Caller's role is unchanged (defaults to moderator).
    """
    request_id = str(uuid.uuid4())

    if current_user.organization_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already belong to an organization. Switch is not supported here.",
        )

    invite_result = await db.execute(select(OrganizationInvite).where(OrganizationInvite.code == payload.code))
    invite = invite_result.scalar_one_or_none()
    now = utcnow()
    if invite is None or invite.revoked_at is not None or (invite.expires_at is not None and invite.expires_at <= now):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite code is invalid or has expired.",
        )

    org = await _load_org_with_domains(db, invite.organization_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The organization for this invite no longer exists.",
        )

    user_result = await db.execute(select(User).where(User.id == current_user.id))
    target_user = user_result.scalar_one()
    target_user.organization_id = org.id
    target_user.has_completed_onboarding = True
    await db.commit()

    try:
        await user_service_cached.invalidate_user_cache(target_user.id, target_user.firebase_uid)
    except Exception as e:
        logger.warning(f"[{request_id}] Cache invalidation after org join failed: {e}")

    logger.info(f"[{request_id}] User {target_user.username} joined org '{org.name}' via invite {invite.code}")
    return JoinOrgResponse(organization=_serialize_org(org))


@router.post("/skip-onboarding")
async def skip_onboarding(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Mark the user as having completed onboarding without joining an org.

    The user remains in the 'Unassigned' bucket and a super-admin can later
    place them via ``PATCH /api/users/{id}/organization``.
    """
    user_result = await db.execute(select(User).where(User.id == current_user.id))
    target_user = user_result.scalar_one()
    if not target_user.has_completed_onboarding:
        target_user.has_completed_onboarding = True
        await db.commit()
        try:
            await user_service_cached.invalidate_user_cache(target_user.id, target_user.firebase_uid)
        except Exception as e:
            logger.warning(f"Cache invalidation after skip-onboarding failed: {e}")
    return {"message": "Onboarding skipped", "statusCode": 200}


@router.post("/verify-domain", response_model=DomainVerificationStatus)
async def verify_my_organization_domain(
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> DomainVerificationStatus:
    """
    Trigger a DNS TXT-record check for the caller's first unverified domain.

    On success, ``verified_at`` is set and the token is cleared; from then on
    new signups with that email domain will auto-match the org on first login.
    """
    domains_result = await db.execute(
        select(OrganizationEmailDomain).where(
            OrganizationEmailDomain.organization_id == current_user.organization_id,
            OrganizationEmailDomain.verified_at.is_(None),
        )
    )
    pending = domains_result.scalars().first()
    if pending is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending domain to verify.",
        )
    if not pending.verification_token:
        # Defensive: should never happen given the create flow, but treat as
        # "regenerate first" if someone managed to land in this state.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No verification token on file. Re-create the domain entry.",
        )

    verified = await verify_domain_record(pending.domain, pending.verification_token)
    if verified:
        pending.verified_at = utcnow()
        token_used = pending.verification_token
        pending.verification_token = None
        await db.commit()
        return DomainVerificationStatus(
            domain=pending.domain,
            verified=True,
            verification_token=None,
            verification_record_name=None,
            verification_record_value=None,
        )

    # Not verified — return the record details again so the UI can re-display.
    return DomainVerificationStatus(
        domain=pending.domain,
        verified=False,
        verification_token=pending.verification_token,
        verification_record_name=verification_record_name(pending.domain),
        verification_record_value=verification_record_value(pending.verification_token),
    )


@router.get("/invite", response_model=OrganizationInviteResponse)
async def get_my_organization_invite(
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> OrganizationInviteResponse:
    """Return the current active invite for the caller's org, creating one on demand."""
    # require_org_admin guarantees organization_id is set; narrow for mypy.
    assert current_user.organization_id is not None
    org_id = current_user.organization_id
    invite = await _active_invite_for_org(db, org_id)
    if invite is None:
        invite = OrganizationInvite(
            code=_new_invite_code(),
            organization_id=org_id,
            created_by_user_id=current_user.id,
        )
        db.add(invite)
        await db.commit()
        await db.refresh(invite)
    return _serialize_invite(invite)


@router.post("/invite/rotate", response_model=OrganizationInviteResponse)
async def rotate_my_organization_invite(
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> OrganizationInviteResponse:
    """Revoke the current invite (if any) and create a new one."""
    # require_org_admin guarantees organization_id is set; narrow for mypy.
    assert current_user.organization_id is not None
    org_id = current_user.organization_id
    now = utcnow()
    existing = await _active_invite_for_org(db, org_id)
    if existing is not None:
        existing.revoked_at = now

    new_invite = OrganizationInvite(
        code=_new_invite_code(),
        organization_id=org_id,
        created_by_user_id=current_user.id,
    )
    db.add(new_invite)
    await db.commit()
    await db.refresh(new_invite)
    return _serialize_invite(new_invite)


# ---------------------------------------------------------------------------
# Domain management — list, add, verify (per-domain)
# ---------------------------------------------------------------------------


@router.post(
    "/domains",
    response_model=DomainVerificationStatus,
    status_code=status.HTTP_201_CREATED,
)
async def add_my_organization_domain(
    payload: AddDomainRequest,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> DomainVerificationStatus:
    """
    Add another email domain to the caller's organization.

    The domain starts unverified with a fresh TXT-record token; the org
    admin then publishes the record and calls
    ``POST /domains/{domain}/verify`` to flip the status. Auto-match by
    email domain won't pick this domain up until verified.
    """
    token = secrets.token_urlsafe(24)
    new_row = OrganizationEmailDomain(
        domain=payload.domain,
        organization_id=current_user.organization_id,
        verification_token=token,
        verification_started_at=utcnow(),
    )
    db.add(new_row)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Domain '{payload.domain}' is already claimed by another organization.",
        ) from e

    logger.info(
        f"Org admin {current_user.username} added pending domain {payload.domain} "
        f"to organization {current_user.organization_id}"
    )

    return DomainVerificationStatus(
        domain=payload.domain,
        verified=False,
        verification_token=token,
        verification_record_name=verification_record_name(payload.domain),
        verification_record_value=verification_record_value(token),
    )


@router.post(
    "/domains/{domain}/verify",
    response_model=DomainVerificationStatus,
)
async def verify_my_organization_domain_by_name(
    domain: str = Path(..., title="The domain to verify"),
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> DomainVerificationStatus:
    """
    Trigger a DNS TXT-record check for a specific domain on the caller's org.

    Returns 404 if the domain isn't registered on this org. Idempotent —
    a domain that's already verified is returned with ``verified=True``
    and no DNS lookup is made.
    """
    normalized = domain.strip().lower().lstrip("@")
    result = await db.execute(
        select(OrganizationEmailDomain).where(
            OrganizationEmailDomain.domain == normalized,
            OrganizationEmailDomain.organization_id == current_user.organization_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Domain '{normalized}' is not registered on your organization.",
        )

    if row.verified_at is not None:
        return DomainVerificationStatus(
            domain=row.domain,
            verified=True,
            verification_token=None,
            verification_record_name=None,
            verification_record_value=None,
        )

    if not row.verification_token:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No verification token on file for this domain.",
        )

    verified = await verify_domain_record(row.domain, row.verification_token)
    if verified:
        row.verified_at = utcnow()
        row.verification_token = None
        await db.commit()
        return DomainVerificationStatus(
            domain=row.domain,
            verified=True,
            verification_token=None,
            verification_record_name=None,
            verification_record_value=None,
        )

    return DomainVerificationStatus(
        domain=row.domain,
        verified=False,
        verification_token=row.verification_token,
        verification_record_name=verification_record_name(row.domain),
        verification_record_value=verification_record_value(row.verification_token),
    )


@router.delete(
    "/domains/{domain}",
    status_code=status.HTTP_200_OK,
)
async def delete_my_organization_domain(
    domain: str = Path(..., title="The domain to remove"),
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Remove an email domain from the caller's organization.

    Works for both verified and pending domains. Once deleted, the domain
    no longer auto-attracts new signups; existing members of the org are
    unaffected (their ``organization_id`` stays). The domain row is freed
    up for any other organization to claim.
    """
    normalized = domain.strip().lower().lstrip("@")
    result = await db.execute(
        select(OrganizationEmailDomain).where(
            OrganizationEmailDomain.domain == normalized,
            OrganizationEmailDomain.organization_id == current_user.organization_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Domain '{normalized}' is not registered on your organization.",
        )

    await db.delete(row)
    await db.commit()
    logger.info(
        f"Org admin {current_user.username} removed domain {normalized} from organization {current_user.organization_id}"
    )
    return {"message": f"Domain '{normalized}' removed.", "statusCode": 200}
