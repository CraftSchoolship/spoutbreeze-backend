"""
Payment Controller
Handles all payment-related API endpoints including subscription management,
checkout, webhooks, and plan information.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import stripe

from app.config.database.session import get_db
from app.config.settings import get_settings
from app.config.logger_config import get_logger
from app.services.payment_service import PaymentService
from app.services.auth_service import AuthService
from app.models.user_models import User
from app.models.payment_schemas import (
    CreateCheckoutSessionRequest,
    CheckoutSessionResponse,
    CustomerPortalRequest,
    CustomerPortalResponse,
    SubscriptionResponse,
    SubscriptionWithLimits,
    TransactionResponse,
    PlanInfo,
    CancelSubscriptionRequest,
)
from app.services.cached.user_service_cached import user_service_cached

logger = get_logger("PaymentController")
settings = get_settings()
auth_service = AuthService()
router = APIRouter(prefix="/api/payments", tags=["payments"])


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    """Dependency to get current authenticated user.

    Accepts token from Authorization header (Bearer) or access_token cookie.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # Prefer Authorization header for cross-origin/API calls
        auth_header = request.headers.get("Authorization")
        token: Optional[str] = None

        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
        else:
            # Fallback to cookie for browser navigations
            token = request.cookies.get("access_token")

        if not token:
            raise credentials_exception

        # Validate token and extract user id
        payload = auth_service.validate_token(token)
        keycloak_id = payload.get("sub")
        if not keycloak_id:
            raise credentials_exception

        # Load user (cached)
        user = await user_service_cached.get_user_by_keycloak_id_cached(keycloak_id, db)
        if user is None:
            raise credentials_exception

        return user

    except HTTPException:
        # Normalize to 401 for callers
        raise credentials_exception
    except Exception as e:
        logger.error(f"Authentication error: {str(e)}")
        raise credentials_exception


@router.post("/checkout", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    checkout_data: CreateCheckoutSessionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a Stripe checkout session for subscription purchase
    """
    try:
        session = await PaymentService.create_checkout_session(
            user=user,
            price_id=checkout_data.price_id,
            success_url=checkout_data.success_url,
            cancel_url=checkout_data.cancel_url,
            db=db,
        )
        return session
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating checkout session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create checkout session",
        )


@router.post("/portal", response_model=CustomerPortalResponse)
async def create_customer_portal(
    portal_data: CustomerPortalRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a Stripe customer portal session for subscription management
    """
    try:
        portal = await PaymentService.create_customer_portal_session(
            user=user,
            return_url=portal_data.return_url,
            db=db,
        )
        return portal
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating portal session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create portal session",
        )


@router.get("/subscription", response_model=SubscriptionWithLimits)
async def get_subscription(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get current user's subscription details including plan limits
    """
    subscription = await PaymentService.get_user_subscription(user, db)

    if not subscription:
        # Create free trial subscription if user doesn't have one
        subscription = await PaymentService.create_free_subscription(user, db)

    # Best-effort reconcile with Stripe in case webhooks didn't update yet
    try:
        subscription = (
            await PaymentService.reconcile_subscription_from_stripe(user, db)
            or subscription
        )
    except Exception as e:
        logger.warning(f"Subscription reconcile skipped: {str(e)}")

    # Add plan limits to response (computed from current plan)
    limits = subscription.get_plan_limits()

    return SubscriptionWithLimits(**subscription.__dict__, limits=limits)  # type: ignore[arg-type]


@router.post("/subscription/cancel", response_model=SubscriptionResponse)
async def cancel_subscription(
    cancel_data: CancelSubscriptionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel current user's subscription
    """
    try:
        subscription = await PaymentService.cancel_subscription(
            user=user,
            cancel_immediately=cancel_data.cancel_immediately,
            db=db,
        )
        return subscription
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error canceling subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel subscription",
        )


@router.get("/plans", response_model=List[PlanInfo])
async def get_plans():
    """
    Get available subscription plans with pricing and features
    """
    try:
        plans = await PaymentService.get_available_plans()
        return plans
    except Exception as e:
        logger.error(f"Error fetching plans: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch plans",
        )


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Stripe webhook events
    """
    payload = await request.body()

    try:
        # Verify webhook signature
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, settings.stripe_webhook_secret
        )
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.SignatureVerificationError as e:
        logger.error(f"Invalid webhook signature: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Handle the event
    try:
        await PaymentService.handle_webhook_event(
            event_type=event["type"],
            data=event["data"],
            db=db,
        )
        logger.info(f"Webhook event {event['type']} processed successfully")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process webhook",
        )


@router.get("/transactions", response_model=List[TransactionResponse])
async def get_transactions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get user's payment transaction history
    """
    try:
        subscription = await PaymentService.get_user_subscription(user, db)

        if not subscription:
            return []

        # Transactions are loaded via relationship
        await db.refresh(subscription, ["transactions"])
        return subscription.transactions
    except Exception as e:
        logger.error(f"Error fetching transactions: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch transactions",
        )


@router.get("/limits")
async def get_current_limits(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get current user's plan limits
    """
    subscription = await PaymentService.get_user_subscription(user, db)

    if not subscription:
        subscription = await PaymentService.create_free_subscription(user, db)

    # If subscription is not active/trialing (canceled, unpaid, etc.),
    # immediately apply Free plan limits
    if not subscription.is_active():
        return {
            "max_quality": "720p",
            "max_concurrent_streams": 1,
            "max_stream_duration_hours": 1,
            "support_response_hours": 72,
            "support_channels": ["email"],
            "chat_filter": False,
            "oauth_enabled": False,
            "analytics_enabled": False,
        }

    return subscription.get_plan_limits()
