import pytest
from httpx import AsyncClient
from uuid import uuid4
from datetime import datetime, timedelta

from app.main import app
from app.controllers.payment_controller import get_current_user
from app.models.user_models import User
from app.models.payment_models import (
    Subscription,
    Transaction,
    SubscriptionPlan,
    SubscriptionStatus,
    TransactionType,
)


@pytest.mark.anyio
async def test_get_plans_returns_list(client: AsyncClient):
    """Plans endpoint is public and should return a list"""
    resp = await client.get("/api/payments/plans")
    assert resp.status_code == 200
    plans = resp.json()
    assert isinstance(plans, list)
    assert len(plans) == 3
    plan_types = [p["plan_type"] for p in plans]
    assert "free" in plan_types
    assert "pro" in plan_types
    assert "enterprise" in plan_types


@pytest.mark.anyio
async def test_get_plans_has_required_fields(client: AsyncClient):
    """Each plan should have all required fields"""
    resp = await client.get("/api/payments/plans")
    assert resp.status_code == 200
    for plan in resp.json():
        assert "name" in plan
        assert "plan_type" in plan
        assert "price" in plan
        assert "features" in plan
        assert "limits" in plan
        assert "stripe_price_id" in plan
        assert isinstance(plan["features"], list)


@pytest.mark.anyio
async def test_get_subscription_creates_free_for_new_user(
    client: AsyncClient, db_session, test_user: User, mock_current_user
):
    """Should auto-create a free subscription for users without one"""
    app.dependency_overrides[get_current_user] = mock_current_user
    try:
        resp = await client.get("/api/payments/subscription")
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "free"
        assert data["status"] == "trialing"
        assert "limits" in data
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_get_transactions_empty(
    client: AsyncClient, db_session, test_user: User, mock_current_user
):
    """Should return empty list when no transactions exist"""
    app.dependency_overrides[get_current_user] = mock_current_user
    try:
        resp = await client.get("/api/payments/transactions")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_get_transactions_with_data(
    client: AsyncClient, db_session, test_user: User, mock_current_user
):
    """Should return transactions when they exist"""
    app.dependency_overrides[get_current_user] = mock_current_user
    try:
        # Create subscription and transaction
        sub = Subscription(
            user_id=test_user.id,
            stripe_customer_id=f"cus_{uuid4().hex[:8]}",
            plan=SubscriptionPlan.PRO.value,
            status=SubscriptionStatus.ACTIVE.value,
        )
        db_session.add(sub)
        await db_session.commit()
        await db_session.refresh(sub)

        txn = Transaction(
            subscription_id=sub.id,
            stripe_payment_intent_id=f"pi_{uuid4().hex[:16]}",
            stripe_invoice_id=f"in_{uuid4().hex[:16]}",
            amount=69.00,
            currency="usd",
            transaction_type=TransactionType.PAYMENT.value,
            status="succeeded",
            description="Payment for pro plan",
            receipt_url="https://pay.stripe.com/receipt/123",
        )
        db_session.add(txn)
        await db_session.commit()

        resp = await client.get("/api/payments/transactions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["amount"] == 69.00
        assert data[0]["status"] == "succeeded"
        assert data[0]["receipt_url"] == "https://pay.stripe.com/receipt/123"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_webhook_invalid_signature(client: AsyncClient):
    """Webhook should reject requests with invalid signatures"""
    resp = await client.post(
        "/api/payments/webhook",
        content=b'{"type": "test"}',
        headers={
            "stripe-signature": "invalid_sig",
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_get_usage_stats(
    client: AsyncClient, db_session, test_user: User, mock_current_user
):
    """Should return usage statistics"""
    app.dependency_overrides[get_current_user] = mock_current_user
    try:
        # Create a subscription
        sub = Subscription(
            user_id=test_user.id,
            stripe_customer_id=f"cus_{uuid4().hex[:8]}",
            plan=SubscriptionPlan.FREE.value,
            status=SubscriptionStatus.TRIALING.value,
            trial_end=datetime.utcnow() + timedelta(days=10),
        )
        db_session.add(sub)
        await db_session.commit()

        resp = await client.get("/api/payments/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_streams" in data
        assert "max_concurrent_streams" in data
        assert "current_plan" in data
        assert data["current_plan"] == "free"
        assert data["active_streams"] == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)
