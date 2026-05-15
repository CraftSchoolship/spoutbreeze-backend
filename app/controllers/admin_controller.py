from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database.session import get_db
from app.config.logger_config import get_logger
from app.controllers.user_controller import require_role
from app.models.admin_schemas import AnalyticsOverview
from app.models.organization_models import Organization, OrganizationEmailDomain
from app.models.organization_schemas import (
    OrganizationCreate,
    OrganizationResponse,
    OrganizationUpdate,
)
from app.services.admin_analytics_service import UNASSIGNED, AdminAnalyticsService, OrgFilter

logger = get_logger("AdminController")

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin"],
    dependencies=[Depends(require_role("super_admin"))],
)


@router.get("/analytics/overview", response_model=AnalyticsOverview)
async def get_analytics_overview(
    organization_id: str | None = Query(
        None,
        description=(
            "Optional org scope for users/events/streaming/revenue metrics. "
            "Pass a UUID to scope to that organization, or 'unassigned' for "
            "users with no organization. Omit for the platform-wide view. "
            "The organizations rollup is always platform-wide."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsOverview:
    """
    Snapshot metrics: users, events, streaming, revenue, organizations.

    Per-tab metrics can be scoped by ``organization_id``. The
    ``organizations`` rollup ignores the filter — it is the breakdown view.
    """
    org_filter: OrgFilter = None
    if organization_id is not None:
        if organization_id == UNASSIGNED:
            org_filter = UNASSIGNED
        else:
            try:
                org_filter = UUID(organization_id)
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"organization_id must be a UUID or '{UNASSIGNED}'",
                ) from e

    data = await AdminAnalyticsService.get_overview(db, org_filter)
    return AnalyticsOverview.model_validate(data)


def _serialize_org(org: Organization) -> OrganizationResponse:
    return OrganizationResponse(
        id=org.id,
        name=org.name,
        is_active=org.is_active,
        email_domains=sorted(d.domain for d in org.email_domains),
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


async def _fetch_org_or_404(db: AsyncSession, org_id: UUID) -> Organization:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization {org_id} not found",
        )
    return org


@router.post("/organizations", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    payload: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
) -> OrganizationResponse:
    org = Organization(name=payload.name)
    org.email_domains = [OrganizationEmailDomain(domain=d) for d in payload.email_domains]
    db.add(org)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        msg = str(e.orig).lower() if e.orig else str(e).lower()
        if "name" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Organization name '{payload.name}' already exists.",
            ) from e
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="One of the email domains is already claimed by another organization.",
        ) from e
    await db.refresh(org, attribute_names=["email_domains"])
    return _serialize_org(org)


@router.get("/organizations", response_model=list[OrganizationResponse])
async def list_organizations(db: AsyncSession = Depends(get_db)) -> list[OrganizationResponse]:
    result = await db.execute(select(Organization).order_by(Organization.name.asc()))
    orgs = result.scalars().unique().all()
    return [_serialize_org(o) for o in orgs]


@router.get("/organizations/{org_id}", response_model=OrganizationResponse)
async def get_organization(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> OrganizationResponse:
    org = await _fetch_org_or_404(db, org_id)
    return _serialize_org(org)


@router.patch("/organizations/{org_id}", response_model=OrganizationResponse)
async def update_organization(
    org_id: UUID,
    payload: OrganizationUpdate,
    db: AsyncSession = Depends(get_db),
) -> OrganizationResponse:
    org = await _fetch_org_or_404(db, org_id)

    if payload.name is not None:
        org.name = payload.name
    if payload.is_active is not None:
        org.is_active = payload.is_active
    if payload.email_domains is not None:
        existing = await db.execute(select(OrganizationEmailDomain).where(OrganizationEmailDomain.organization_id == org_id))
        for row in existing.scalars().all():
            await db.delete(row)
        await db.flush()
        for d in payload.email_domains:
            db.add(OrganizationEmailDomain(domain=d, organization_id=org_id))

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        msg = str(e.orig).lower() if e.orig else str(e).lower()
        if "name" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An organization with that name already exists.",
            ) from e
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="One of the email domains is already claimed by another organization.",
        ) from e

    await db.refresh(org, attribute_names=["email_domains"])
    return _serialize_org(org)


@router.delete("/organizations/{org_id}", status_code=status.HTTP_200_OK)
async def delete_organization(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await _fetch_org_or_404(db, org_id)
    logger.info(f"Deleting organization {org.name} (id={org.id})")
    await db.delete(org)
    await db.commit()
    return {"message": "Organization deleted", "statusCode": 200}
