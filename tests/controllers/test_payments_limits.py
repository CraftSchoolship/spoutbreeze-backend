import pytest
from httpx import AsyncClient
from uuid import uuid4

from app.main import app
from app.controllers.payment_controller import get_current_user
from app.models.user_models import User
from app.models.payment_models import Subscription, SubscriptionPlan, SubscriptionStatus


@pytest.mark.anyio
async def test_limits_free_plan(client: AsyncClient, db_session, test_user: User, mock_current_user):
    app.dependency_overrides[get_current_user] = mock_current_user
    try:
        # Create a FREE subscription row to avoid hitting Stripe in tests
        sub = Subscription(
            user_id=test_user.id,
            stripe_customer_id=f"cus_{uuid4().hex[:8]}",
            plan=SubscriptionPlan.FREE.value,
            status=SubscriptionStatus.TRIALING.value,
        )
        db_session.add(sub)
        await db_session.commit()

        resp = await client.get("/api/payments/limits")
        assert resp.status_code == 200
        limits = resp.json()
        assert limits["max_quality"] == "720p"
        assert limits["max_concurrent_streams"] == 1
        assert limits["max_stream_duration_hours"] == 1
        assert limits["chat_filter"] is False
        assert limits["oauth_enabled"] is False
        assert limits["analytics_enabled"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_limits_pro_plan(client: AsyncClient, db_session, test_user: User, mock_current_user):
    app.dependency_overrides[get_current_user] = mock_current_user
    try:
        sub = Subscription(
            user_id=test_user.id,
            stripe_customer_id=f"cus_{uuid4().hex[:8]}",
            plan=SubscriptionPlan.PRO.value,
            status=SubscriptionStatus.ACTIVE.value,
        )
        db_session.add(sub)
        await db_session.commit()

        resp = await client.get("/api/payments/limits")
        assert resp.status_code == 200
        limits = resp.json()
        assert limits["max_quality"] == "1080p"
        assert limits["max_concurrent_streams"] == 10
        assert limits["max_stream_duration_hours"] is None
        # Enterprise-only features must remain disabled on Pro
        assert limits["chat_filter"] is False
        assert limits["oauth_enabled"] is False
        assert limits["analytics_enabled"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_limits_enterprise_plan(client: AsyncClient, db_session, test_user: User, mock_current_user):
    app.dependency_overrides[get_current_user] = mock_current_user
    try:
        sub = Subscription(
            user_id=test_user.id,
            stripe_customer_id=f"cus_{uuid4().hex[:8]}",
            plan=SubscriptionPlan.ENTERPRISE.value,
            status=SubscriptionStatus.ACTIVE.value,
        )
        db_session.add(sub)
        await db_session.commit()

        resp = await client.get("/api/payments/limits")
        assert resp.status_code == 200
        limits = resp.json()
        assert limits["max_quality"] == "4K"
        assert limits["max_concurrent_streams"] is None
        assert limits["max_stream_duration_hours"] is None
        assert limits["chat_filter"] is True
        assert limits["oauth_enabled"] is True
        assert limits["analytics_enabled"] is True
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_limits_after_cancel_immediate_applies_free(client: AsyncClient, db_session, test_user: User, mock_current_user):
    """Downgrading from Pro should instantly apply Free limits when subscription is inactive."""
    app.dependency_overrides[get_current_user] = mock_current_user
    try:
        # Simulate a user who canceled immediately: status becomes CANCELED
        sub = Subscription(
            user_id=test_user.id,
            stripe_customer_id=f"cus_{uuid4().hex[:8]}",
            plan=SubscriptionPlan.PRO.value,
            status=SubscriptionStatus.CANCELED.value,
        )
        db_session.add(sub)
        await db_session.commit()

        resp = await client.get("/api/payments/limits")
        assert resp.status_code == 200
        limits = resp.json()
        # Should return Free limits due to inactive status
        assert limits["max_quality"] == "720p"
        assert limits["max_concurrent_streams"] == 1
        assert limits["max_stream_duration_hours"] == 1
        assert limits["chat_filter"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)
