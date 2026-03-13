from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models.payment_models import (
    PLAN_LIMITS,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    WebhookEvent,
)
from app.services.payment_service import PaymentService


class TestGetPlanFromPriceId:
    """Test _get_plan_from_price_id mapping"""

    @patch("app.services.payment_service.settings")
    def test_pro_price_id(self, mock_settings):
        mock_settings.stripe_pro_price_id = "price_pro_123"
        mock_settings.stripe_enterprise_price_id = "price_ent_456"
        result = PaymentService._get_plan_from_price_id("price_pro_123")
        assert result == SubscriptionPlan.PRO.value

    @patch("app.services.payment_service.settings")
    def test_enterprise_price_id(self, mock_settings):
        mock_settings.stripe_pro_price_id = "price_pro_123"
        mock_settings.stripe_enterprise_price_id = "price_ent_456"
        result = PaymentService._get_plan_from_price_id("price_ent_456")
        assert result == SubscriptionPlan.ENTERPRISE.value

    @patch("app.services.payment_service.settings")
    def test_unknown_price_id_returns_free(self, mock_settings):
        mock_settings.stripe_pro_price_id = "price_pro_123"
        mock_settings.stripe_enterprise_price_id = "price_ent_456"
        result = PaymentService._get_plan_from_price_id("price_unknown")
        assert result == SubscriptionPlan.FREE.value


class TestPlanLimits:
    """Test centralized PLAN_LIMITS constant"""

    def test_free_plan_limits(self):
        limits = PLAN_LIMITS[SubscriptionPlan.FREE.value]
        assert limits["max_quality"] == "720p"
        assert limits["max_concurrent_streams"] == 1
        assert limits["max_stream_duration_hours"] == 1
        assert limits["chat_filter"] is False

    def test_pro_plan_limits(self):
        limits = PLAN_LIMITS[SubscriptionPlan.PRO.value]
        assert limits["max_quality"] == "1080p"
        assert limits["max_concurrent_streams"] == 10
        assert limits["max_stream_duration_hours"] is None
        assert limits["chat_filter"] is False

    def test_enterprise_plan_limits(self):
        limits = PLAN_LIMITS[SubscriptionPlan.ENTERPRISE.value]
        assert limits["max_quality"] == "4K"
        assert limits["max_concurrent_streams"] is None
        assert limits["chat_filter"] is True
        assert limits["oauth_enabled"] is True

    def test_unlimited_plan_limits(self):
        limits = PLAN_LIMITS["unlimited"]
        assert limits["max_quality"] == "4K"
        assert limits["max_concurrent_streams"] is None
        assert limits["analytics_enabled"] is True


class TestPriceIdValidation:
    """Test that create_checkout_session validates price IDs"""

    @pytest.mark.anyio
    @patch("app.services.payment_service.settings")
    @patch("app.services.payment_service.stripe")
    async def test_invalid_price_id_raises_400(self, mock_stripe, mock_settings, db_session, test_user):
        import stripe as real_stripe

        mock_stripe.StripeError = real_stripe.StripeError
        mock_settings.stripe_free_price_id = "price_free_123"
        mock_settings.stripe_pro_price_id = "price_pro_123"
        mock_settings.stripe_enterprise_price_id = "price_ent_123"

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await PaymentService.create_checkout_session(
                user=test_user,
                price_id="price_invalid_999",
                success_url="http://test.com/success",
                cancel_url="http://test.com/cancel",
                db=db_session,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.anyio
    @patch("app.services.payment_service.settings")
    @patch("app.services.payment_service.stripe")
    async def test_empty_price_id_not_valid(self, mock_stripe, mock_settings, db_session, test_user):
        import stripe as real_stripe

        mock_stripe.StripeError = real_stripe.StripeError
        mock_settings.stripe_free_price_id = ""
        mock_settings.stripe_pro_price_id = "price_pro_123"
        mock_settings.stripe_enterprise_price_id = ""

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await PaymentService.create_checkout_session(
                user=test_user,
                price_id="",
                success_url="http://test.com/success",
                cancel_url="http://test.com/cancel",
                db=db_session,
            )
        assert exc_info.value.status_code == 400


class TestWebhookDeduplication:
    """Test webhook event deduplication"""

    @pytest.mark.anyio
    async def test_duplicate_event_skipped(self, db_session, test_user):
        # Create a subscription for the test
        sub = Subscription(
            user_id=test_user.id,
            stripe_customer_id=f"cus_{uuid4().hex[:8]}",
            plan=SubscriptionPlan.FREE.value,
            status=SubscriptionStatus.TRIALING.value,
        )
        db_session.add(sub)
        await db_session.commit()

        event_id = f"evt_{uuid4().hex}"

        # First call should process
        await PaymentService.handle_webhook_event(
            event_id=event_id,
            event_type="checkout.session.completed",
            data={"object": {"customer": "cus_test", "metadata": {"user_id": str(test_user.id)}}},
            db=db_session,
        )

        # Second call with same event_id should be skipped (no error)
        await PaymentService.handle_webhook_event(
            event_id=event_id,
            event_type="checkout.session.completed",
            data={"object": {"customer": "cus_test", "metadata": {"user_id": str(test_user.id)}}},
            db=db_session,
        )

        # Verify only one webhook event record exists
        from sqlalchemy import func, select

        count = await db_session.execute(
            select(func.count()).select_from(WebhookEvent).where(WebhookEvent.stripe_event_id == event_id)
        )
        assert count.scalar() == 1


class TestCancelSubscription:
    """Test subscription cancellation logic"""

    @pytest.mark.anyio
    async def test_cannot_cancel_free_plan(self, db_session, test_user):
        sub = Subscription(
            user_id=test_user.id,
            stripe_customer_id=f"cus_{uuid4().hex[:8]}",
            plan=SubscriptionPlan.FREE.value,
            status=SubscriptionStatus.TRIALING.value,
        )
        db_session.add(sub)
        await db_session.commit()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await PaymentService.cancel_subscription(
                user=test_user,
                cancel_immediately=False,
                db=db_session,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.anyio
    async def test_cancel_nonexistent_subscription_raises_404(self, db_session, test_user):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await PaymentService.cancel_subscription(
                user=test_user,
                cancel_immediately=False,
                db=db_session,
            )
        assert exc_info.value.status_code == 404
