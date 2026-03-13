"""
Subscription Middleware and Guards
Enforces subscription plan limits and restrictions
"""

from datetime import datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database.session import get_db
from app.config.logger_config import get_logger
from app.controllers.user_controller import get_current_user
from app.models.payment_models import Subscription, SubscriptionPlan, SubscriptionStatus
from app.models.user_models import User
from app.services.payment_service import PaymentService

logger = get_logger("SubscriptionMiddleware")


class SubscriptionGuard:
    """Guard class to check subscription status and enforce limits"""

    @staticmethod
    async def get_user_with_subscription(request: Request, db: AsyncSession = Depends(get_db)) -> tuple[User, Subscription]:
        """
        Get current user with their subscription
        Creates a free subscription if user doesn't have one
        """
        user = await get_current_user(request, db)

        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        subscription = await PaymentService.get_user_subscription(user, db)

        if not subscription:
            if user.has_used_free_trial:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Your 14-day free trial has expired. Please upgrade to a paid plan.",
                )
            # Create free trial subscription (first time only)
            subscription = await PaymentService.create_free_subscription(user, db)

        return user, subscription

    @staticmethod
    async def check_subscription_active(user: User, subscription: Subscription) -> None:
        """Check if subscription is active"""
        # Skip check for users with unlimited access
        if user.unlimited_access:
            return

        if not subscription.is_active():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your subscription is not active. Please upgrade your plan.",
            )

    @staticmethod
    async def check_trial_expired(subscription: Subscription, db: AsyncSession | None = None) -> None:
        """Check if trial period has expired.
        When the 14-day free trial expires, the subscription is permanently
        marked as expired and the user can never get a free trial again.
        """
        # Skip check for users with unlimited access
        if subscription.user.unlimited_access:
            return

        if subscription.is_trial() and subscription.trial_end:
            if datetime.utcnow() > subscription.trial_end:
                # Permanently expire the free trial in the DB
                subscription.status = SubscriptionStatus.EXPIRED.value
                subscription.user.has_used_free_trial = True
                if db:
                    await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Your 14-day free trial has expired. Please upgrade to a paid plan to continue.",
                )

        # Also block if status is already expired
        if subscription.status == SubscriptionStatus.EXPIRED.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your 14-day free trial has expired. Please upgrade to a paid plan to continue.",
            )

    @staticmethod
    async def check_stream_quality(subscription: Subscription, requested_quality: str) -> None:
        """Check if requested stream quality is allowed for the plan"""
        # Skip check for users with unlimited access
        if subscription.user.unlimited_access:
            return

        limits = subscription.get_plan_limits()
        max_quality = limits.get("max_quality", "720p")

        quality_hierarchy = {
            "360p": 0,
            "480p": 1,
            "720p": 2,
            "1080p": 3,
            "1440p": 4,
            "4K": 5,
        }

        requested_level = quality_hierarchy.get(requested_quality, 0)
        max_level = quality_hierarchy.get(max_quality, 0)

        if requested_level > max_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your plan only supports up to {max_quality}. Please upgrade for higher quality.",
            )

    @staticmethod
    async def check_concurrent_streams(subscription: Subscription, current_stream_count: int) -> None:
        """Check if user can start another concurrent stream"""
        # Skip check for users with unlimited access
        if subscription.user.unlimited_access:
            return

        limits = subscription.get_plan_limits()
        max_streams = limits.get("max_concurrent_streams")

        if max_streams is not None and current_stream_count >= max_streams:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your plan allows only {max_streams} concurrent stream(s). Please upgrade for more.",
            )

    @staticmethod
    async def check_stream_duration(subscription: Subscription, stream_duration_hours: float) -> None:
        """Check if stream duration exceeds plan limit"""
        # Skip check for users with unlimited access
        if subscription.user.unlimited_access:
            return

        limits = subscription.get_plan_limits()
        max_duration = limits.get("max_stream_duration_hours")

        if max_duration is not None and stream_duration_hours > max_duration:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your plan allows streams up to {max_duration} hour(s). Please upgrade for longer streams.",
            )

    @staticmethod
    async def check_feature_access(subscription: Subscription, feature: str) -> None:
        """Check if user has access to a specific feature"""
        # Skip check for users with unlimited access
        if subscription.user.unlimited_access:
            return

        limits = subscription.get_plan_limits()

        feature_map = {
            "chat_filter": "chat_filter",
            "oauth": "oauth_enabled",
            "analytics": "analytics_enabled",
        }

        limit_key = feature_map.get(feature)
        if not limit_key:
            logger.warning(f"Unknown feature: {feature}")
            return

        if not limits.get(limit_key, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"The {feature} feature is not available on your plan. Please upgrade to Enterprise.",
            )

    @staticmethod
    async def require_paid_plan(subscription: Subscription) -> None:
        """Require user to have a paid plan (Pro or Enterprise)"""
        # Skip check for users with unlimited access
        if subscription.user.unlimited_access:
            return

        if subscription.plan == SubscriptionPlan.FREE.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This feature requires a paid plan. Please upgrade to Pro or Enterprise.",
            )

    @staticmethod
    async def require_enterprise_plan(subscription: Subscription) -> None:
        """Require user to have Enterprise plan"""
        # Skip check for users with unlimited access
        if subscription.user.unlimited_access:
            return

        if subscription.plan != SubscriptionPlan.ENTERPRISE.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This feature is only available on the Enterprise plan.",
            )


# Dependency functions for easy use in routes
async def require_active_subscription(request: Request, db: AsyncSession = Depends(get_db)) -> tuple[User, Subscription]:
    """
    Dependency to require an active subscription
    """
    user, subscription = await SubscriptionGuard.get_user_with_subscription(request, db)
    await SubscriptionGuard.check_subscription_active(user, subscription)
    await SubscriptionGuard.check_trial_expired(subscription, db)
    return user, subscription


async def require_paid_subscription(request: Request, db: AsyncSession = Depends(get_db)) -> tuple[User, Subscription]:
    """
    Dependency to require a paid subscription (Pro or Enterprise)
    """
    user, subscription = await SubscriptionGuard.get_user_with_subscription(request, db)
    await SubscriptionGuard.check_subscription_active(user, subscription)
    await SubscriptionGuard.check_trial_expired(subscription, db)
    await SubscriptionGuard.require_paid_plan(subscription)
    return user, subscription


async def require_enterprise_subscription(request: Request, db: AsyncSession = Depends(get_db)) -> tuple[User, Subscription]:
    """
    Dependency to require Enterprise subscription
    """
    user, subscription = await SubscriptionGuard.get_user_with_subscription(request, db)
    await SubscriptionGuard.check_subscription_active(user, subscription)
    await SubscriptionGuard.check_trial_expired(subscription, db)
    await SubscriptionGuard.require_enterprise_plan(subscription)
    return user, subscription
