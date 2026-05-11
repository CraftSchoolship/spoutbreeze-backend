from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database.session import get_db
from app.controllers.user_controller import require_role
from app.models.admin_schemas import AnalyticsOverview
from app.services.admin_analytics_service import AdminAnalyticsService

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin"],
    dependencies=[Depends(require_role("super_admin"))],
)


@router.get("/analytics/overview", response_model=AnalyticsOverview)
async def get_analytics_overview(db: AsyncSession = Depends(get_db)) -> AnalyticsOverview:
    """
    Platform-wide snapshot metrics: users, events, streaming, revenue.

    Admin-only. Returns a single payload to minimize round-trips from the
    dashboard. Values are computed live; for caching, wrap the service call
    in ``@cached_db`` if/when load demands it.
    """
    data = await AdminAnalyticsService.get_overview(db)
    return AnalyticsOverview.model_validate(data)
