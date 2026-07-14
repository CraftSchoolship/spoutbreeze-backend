"""Tests for AdminAnalyticsService.get_overview org-scoping (N3).

The super-admin path returns the full platform rollup; the org-admin path
(`scope_org_rollup=True`) returns only the caller's org row and never the
other organizations' metrics.
"""

import uuid

import pytest

from app.models.organization_models import Organization
from app.models.user_models import User
from app.services.admin_analytics_service import AdminAnalyticsService
from app.utils.datetime_utils import utcnow

pytestmark = pytest.mark.anyio


async def _make_org_with_user(db, org_name: str, email: str) -> Organization:
    org = Organization(name=org_name)
    db.add(org)
    await db.flush()
    db.add(
        User(
            id=uuid.uuid4(),
            firebase_uid=f"uid-{uuid.uuid4()}",
            username=f"user-{uuid.uuid4()}",
            email=email,
            first_name="Test",
            last_name="User",
            organization_id=org.id,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    await db.commit()
    return org


async def test_scoped_overview_returns_only_callers_org(db_session):
    sfx = uuid.uuid4().hex[:8]
    org_a = await _make_org_with_user(db_session, f"Alpha-{sfx}", f"a-{sfx}@alpha.com")
    await _make_org_with_user(db_session, f"Beta-{sfx}", f"b-{sfx}@beta.com")

    data = await AdminAnalyticsService.get_overview(db_session, org_a.id, scope_org_rollup=True)

    orgs = data["organizations"]
    assert len(orgs) == 1
    assert orgs[0]["id"] == org_a.id
    assert orgs[0]["name"] == f"Alpha-{sfx}"
    assert orgs[0]["user_count"] == 1


async def test_platform_overview_returns_all_orgs(db_session):
    sfx = uuid.uuid4().hex[:8]
    await _make_org_with_user(db_session, f"Alpha-{sfx}", f"a-{sfx}@alpha.com")
    await _make_org_with_user(db_session, f"Beta-{sfx}", f"b-{sfx}@beta.com")

    data = await AdminAnalyticsService.get_overview(db_session)

    names = {row["name"] for row in data["organizations"]}
    assert {f"Alpha-{sfx}", f"Beta-{sfx}"}.issubset(names)


async def test_platform_overview_ignores_scope_flag_without_uuid(db_session):
    """scope_org_rollup only narrows when org_filter is a UUID; a bare flag
    (org_filter=None) must still yield the full platform rollup."""
    sfx = uuid.uuid4().hex[:8]
    await _make_org_with_user(db_session, f"Alpha-{sfx}", f"a-{sfx}@alpha.com")
    await _make_org_with_user(db_session, f"Beta-{sfx}", f"b-{sfx}@beta.com")

    data = await AdminAnalyticsService.get_overview(db_session, None, scope_org_rollup=True)

    names = {row["name"] for row in data["organizations"]}
    assert {f"Alpha-{sfx}", f"Beta-{sfx}"}.issubset(names)
