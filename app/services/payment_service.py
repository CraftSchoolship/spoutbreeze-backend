"""
Stripe Payment Service
Handles all Stripe-related operations including subscription management,
checkout sessions, customer portal, and webhook processing.
"""
import stripe
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status

from app.config.settings import get_settings
from app.config.logger_config import get_logger
from app.models.payment_models import (
    Subscription, 
    Transaction, 
    SubscriptionPlan, 
    SubscriptionStatus,
    TransactionType
)
from app.models.user_models import User
from app.models.payment_schemas import (
    CheckoutSessionResponse,
    CustomerPortalResponse,
    PlanInfo,
    PlanLimits,
)

logger = get_logger("PaymentService")
settings = get_settings()

# Initialize Stripe
stripe.api_key = settings.stripe_secret_key


class PaymentService:
    """Service for handling payment operations with Stripe"""

    @staticmethod
    async def get_or_create_customer(user: User, db: AsyncSession) -> str:
        """Get existing Stripe customer ID or create a new customer"""
        # Check if user already has a subscription with customer ID
        result = await db.execute(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        subscription = result.scalar_one_or_none()

        if subscription and subscription.stripe_customer_id:
            return subscription.stripe_customer_id

        # Create new Stripe customer
        try:
            customer = stripe.Customer.create(
                email=user.email,
                name=f"{user.first_name} {user.last_name}",
                metadata={
                    "user_id": str(user.id),
                    "username": user.username,
                }
            )
            logger.info(f"Created Stripe customer {customer.id} for user {user.id}")
            return customer.id
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create Stripe customer: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create customer: {str(e)}"
            )

    @staticmethod
    async def create_checkout_session(
        user: User,
        price_id: str,
        success_url: str,
        cancel_url: str,
        db: AsyncSession,
    ) -> CheckoutSessionResponse:
        """Create a Stripe checkout session for subscription"""
        try:
            # Get or create customer
            customer_id = await PaymentService.get_or_create_customer(user, db)

            # Check if user has an existing subscription
            result = await db.execute(
                select(Subscription).where(Subscription.user_id == user.id)
            )
            existing_subscription = result.scalar_one_or_none()

            # Create checkout session
            checkout_params = {
                "customer": customer_id,
                "line_items": [{
                    "price": price_id,
                    "quantity": 1,
                }],
                "mode": "subscription",
                "success_url": success_url,
                "cancel_url": cancel_url,
                "metadata": {
                    "user_id": str(user.id),
                },
                "subscription_data": {
                    "metadata": {
                        "user_id": str(user.id),
                    },
                },
            }

            # Trial handling:
            # Previously we granted a 14-day trial for any first checkout or when upgrading from FREE.
            # That caused paid upgrades (e.g., PRO) to start at $0 with the plan switching to trialing.
            # To require immediate payment for PRO/ENTERPRISE, only apply a trial when checking out the FREE price.
            if price_id == settings.stripe_free_price_id:
                checkout_params["subscription_data"]["trial_period_days"] = 14

            session = stripe.checkout.Session.create(**checkout_params)

            logger.info(f"Created checkout session {session.id} for user {user.id}")

            return CheckoutSessionResponse(
                session_id=session.id,
                url=session.url
            )

        except stripe.error.StripeError as e:
            logger.error(f"Failed to create checkout session: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create checkout session: {str(e)}"
            )

    @staticmethod
    async def create_customer_portal_session(
        user: User,
        return_url: str,
        db: AsyncSession,
    ) -> CustomerPortalResponse:
        """Create a Stripe customer portal session for subscription management"""
        try:
            # Get customer ID
            result = await db.execute(
                select(Subscription).where(Subscription.user_id == user.id)
            )
            subscription = result.scalar_one_or_none()

            if not subscription or not subscription.stripe_customer_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No subscription found"
                )

            # Create portal session
            session = stripe.billing_portal.Session.create(
                customer=subscription.stripe_customer_id,
                return_url=return_url,
            )

            logger.info(f"Created portal session for user {user.id}")

            return CustomerPortalResponse(url=session.url)

        except stripe.error.StripeError as e:
            logger.error(f"Failed to create portal session: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create portal session: {str(e)}"
            )

    @staticmethod
    async def get_user_subscription(user: User, db: AsyncSession) -> Optional[Subscription]:
        """Get user's subscription"""
        result = await db.execute(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def reconcile_subscription_from_stripe(user: User, db: AsyncSession) -> Optional[Subscription]:
        """Reconcile local subscription with Stripe state for the user's customer.

        This updates the local DB if Stripe has a more recent/active subscription
        (useful when webhooks are delayed or misconfigured).
        """
        # Fetch existing local subscription (may be FREE/trialing)
        result = await db.execute(select(Subscription).where(Subscription.user_id == user.id))
        subscription = result.scalar_one_or_none()

        if not subscription:
            return None

        customer_id = subscription.stripe_customer_id
        if not customer_id:
            # Ensure a customer exists to be able to query Stripe
            customer_id = await PaymentService.get_or_create_customer(user, db)
            subscription.stripe_customer_id = customer_id
            await db.commit()

        try:
            # Get latest non-canceled subscription for this customer
            stripe_subs = stripe.Subscription.list(customer=customer_id, status="all", limit=10)
            items = stripe_subs.get("data", []) if isinstance(stripe_subs, dict) else stripe_subs.data
            candidates = [s for s in items if s.get("status") != "canceled"]
            if not candidates:
                return subscription

            # Pick the most recently created one
            latest = max(candidates, key=lambda s: s.get("created", 0))

            price = latest["items"]["data"][0]["price"]
            price_id = price["id"]
            product_id = price.get("product")
            status = latest.get("status")

            # Map price to our plan
            plan = PaymentService._get_plan_from_price_id(price_id)

            # Update local subscription if anything differs
            changed = False
            if subscription.stripe_subscription_id != latest.get("id"):
                subscription.stripe_subscription_id = latest.get("id")
                changed = True
            if subscription.stripe_price_id != price_id:
                subscription.stripe_price_id = price_id
                subscription.plan = plan
                changed = True
            if subscription.stripe_product_id != product_id:
                subscription.stripe_product_id = product_id
                changed = True
            if subscription.status != status:
                subscription.status = status
                changed = True

            # Periods and trial info
            cps = latest.get("current_period_start")
            cpe = latest.get("current_period_end")
            if cps:
                dt = datetime.fromtimestamp(cps)
                if subscription.current_period_start != dt:
                    subscription.current_period_start = dt
                    changed = True
            if cpe:
                dt = datetime.fromtimestamp(cpe)
                if subscription.current_period_end != dt:
                    subscription.current_period_end = dt
                    changed = True

            ts = latest.get("trial_start")
            te = latest.get("trial_end")
            if ts:
                dt = datetime.fromtimestamp(ts)
                if subscription.trial_start != dt:
                    subscription.trial_start = dt
                    changed = True
            if te:
                dt = datetime.fromtimestamp(te)
                if subscription.trial_end != dt:
                    subscription.trial_end = dt
                    changed = True

            if changed:
                await db.commit()
                await db.refresh(subscription)
                logger.info(f"Reconciled subscription for user {user.id} from Stripe")

            return subscription
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error during reconcile: {str(e)}")
            return subscription

    @staticmethod
    async def create_free_subscription(user: User, db: AsyncSession) -> Subscription:
        """Create a free trial subscription for a new user"""
        try:
            # Get or create Stripe customer
            customer_id = await PaymentService.get_or_create_customer(user, db)

            # Create subscription record
            trial_start = datetime.utcnow()
            trial_end = trial_start + timedelta(days=14)

            subscription = Subscription(
                user_id=user.id,
                stripe_customer_id=customer_id,
                plan=SubscriptionPlan.FREE.value,
                status=SubscriptionStatus.TRIALING.value,
                trial_start=trial_start,
                trial_end=trial_end,
                current_period_start=trial_start,
                current_period_end=trial_end,
            )

            db.add(subscription)
            await db.commit()
            await db.refresh(subscription)

            logger.info(f"Created free trial subscription for user {user.id}")
            return subscription

        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to create free subscription: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create subscription: {str(e)}"
            )

    @staticmethod
    async def cancel_subscription(
        user: User,
        cancel_immediately: bool,
        db: AsyncSession,
    ) -> Subscription:
        """Cancel user's subscription"""
        try:
            subscription = await PaymentService.get_user_subscription(user, db)

            if not subscription:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No subscription found"
                )

            if subscription.plan == SubscriptionPlan.FREE.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot cancel free plan"
                )

            # Cancel in Stripe
            if subscription.stripe_subscription_id:
                if cancel_immediately:
                    stripe.Subscription.delete(subscription.stripe_subscription_id)
                    subscription.status = SubscriptionStatus.CANCELED.value
                    subscription.canceled_at = datetime.utcnow()
                else:
                    stripe.Subscription.modify(
                        subscription.stripe_subscription_id,
                        cancel_at_period_end=True
                    )
                    subscription.cancel_at_period_end = True

            await db.commit()
            await db.refresh(subscription)

            logger.info(f"Canceled subscription for user {user.id}")
            return subscription

        except stripe.error.StripeError as e:
            logger.error(f"Failed to cancel subscription: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to cancel subscription: {str(e)}"
            )

    @staticmethod
    async def handle_webhook_event(
        event_type: str,
        data: Dict[str, Any],
        db: AsyncSession,
    ) -> None:
        """Handle Stripe webhook events"""
        try:
            if event_type == "checkout.session.completed":
                await PaymentService._handle_checkout_completed(data, db)
            elif event_type == "customer.subscription.created":
                await PaymentService._handle_subscription_created(data, db)
            elif event_type == "customer.subscription.updated":
                await PaymentService._handle_subscription_updated(data, db)
            elif event_type == "customer.subscription.deleted":
                await PaymentService._handle_subscription_deleted(data, db)
            elif event_type == "invoice.payment_succeeded":
                await PaymentService._handle_payment_succeeded(data, db)
            elif event_type == "invoice.payment_failed":
                await PaymentService._handle_payment_failed(data, db)
            else:
                logger.info(f"Unhandled webhook event: {event_type}")

        except Exception as e:
            logger.error(f"Error handling webhook event {event_type}: {str(e)}")
            raise

    @staticmethod
    async def _handle_checkout_completed(data: Dict[str, Any], db: AsyncSession) -> None:
        """Handle checkout.session.completed event"""
        session = data.get("object", {})
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")
        user_id = session.get("metadata", {}).get("user_id")

        if not user_id:
            logger.warning("No user_id in checkout session metadata")
            return

        logger.info(f"Checkout completed for user {user_id}")

    @staticmethod
    async def _handle_subscription_created(data: Dict[str, Any], db: AsyncSession) -> None:
        """Handle customer.subscription.created event"""
        stripe_subscription = data.get("object", {})
        customer_id = stripe_subscription.get("customer")
        subscription_id = stripe_subscription.get("id")
        user_id = stripe_subscription.get("metadata", {}).get("user_id")

        if not user_id:
            logger.warning("No user_id in subscription metadata")
            return

        # Get user
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            logger.error(f"User {user_id} not found")
            return

        # Check if subscription already exists
        result = await db.execute(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        subscription = result.scalar_one_or_none()

        # Get price and product info
        price_id = stripe_subscription["items"]["data"][0]["price"]["id"]
        product_id = stripe_subscription["items"]["data"][0]["price"]["product"]

        # Determine plan based on price_id
        plan = PaymentService._get_plan_from_price_id(price_id)

        if subscription:
            # Update existing subscription
            subscription.stripe_subscription_id = subscription_id
            subscription.stripe_price_id = price_id
            subscription.stripe_product_id = product_id
            subscription.plan = plan
            subscription.status = stripe_subscription.get("status")
            subscription.current_period_start = datetime.fromtimestamp(
                stripe_subscription.get("current_period_start")
            )
            subscription.current_period_end = datetime.fromtimestamp(
                stripe_subscription.get("current_period_end")
            )

            if stripe_subscription.get("trial_start"):
                subscription.trial_start = datetime.fromtimestamp(
                    stripe_subscription.get("trial_start")
                )
            if stripe_subscription.get("trial_end"):
                subscription.trial_end = datetime.fromtimestamp(
                    stripe_subscription.get("trial_end")
                )
        else:
            # Create new subscription
            subscription = Subscription(
                user_id=user.id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
                stripe_price_id=price_id,
                stripe_product_id=product_id,
                plan=plan,
                status=stripe_subscription.get("status"),
                current_period_start=datetime.fromtimestamp(
                    stripe_subscription.get("current_period_start")
                ),
                current_period_end=datetime.fromtimestamp(
                    stripe_subscription.get("current_period_end")
                ),
            )

            if stripe_subscription.get("trial_start"):
                subscription.trial_start = datetime.fromtimestamp(
                    stripe_subscription.get("trial_start")
                )
            if stripe_subscription.get("trial_end"):
                subscription.trial_end = datetime.fromtimestamp(
                    stripe_subscription.get("trial_end")
                )

            db.add(subscription)

        await db.commit()
        logger.info(f"Subscription created/updated for user {user_id}")

    @staticmethod
    async def _handle_subscription_updated(data: Dict[str, Any], db: AsyncSession) -> None:
        """Handle customer.subscription.updated event"""
        stripe_subscription = data.get("object", {})
        subscription_id = stripe_subscription.get("id")

        # Find subscription
        result = await db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            logger.warning(f"Subscription {subscription_id} not found in database")
            return

        # Update subscription
        subscription.status = stripe_subscription.get("status")
        subscription.current_period_start = datetime.fromtimestamp(
            stripe_subscription.get("current_period_start")
        )
        subscription.current_period_end = datetime.fromtimestamp(
            stripe_subscription.get("current_period_end")
        )
        subscription.cancel_at_period_end = stripe_subscription.get("cancel_at_period_end", False)

        if stripe_subscription.get("canceled_at"):
            subscription.canceled_at = datetime.fromtimestamp(
                stripe_subscription.get("canceled_at")
            )

        # Update price if changed
        price_id = stripe_subscription["items"]["data"][0]["price"]["id"]
        if price_id != subscription.stripe_price_id:
            subscription.stripe_price_id = price_id
            subscription.plan = PaymentService._get_plan_from_price_id(price_id)

        await db.commit()
        logger.info(f"Subscription {subscription_id} updated")

    @staticmethod
    async def _handle_subscription_deleted(data: Dict[str, Any], db: AsyncSession) -> None:
        """Handle customer.subscription.deleted event"""
        stripe_subscription = data.get("object", {})
        subscription_id = stripe_subscription.get("id")

        # Find subscription
        result = await db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            logger.warning(f"Subscription {subscription_id} not found in database")
            return

        # Update subscription status
        subscription.status = SubscriptionStatus.CANCELED.value
        subscription.canceled_at = datetime.utcnow()

        await db.commit()
        logger.info(f"Subscription {subscription_id} deleted")

    @staticmethod
    async def _handle_payment_succeeded(data: Dict[str, Any], db: AsyncSession) -> None:
        """Handle invoice.payment_succeeded event"""
        invoice = data.get("object", {})
        subscription_id = invoice.get("subscription")
        payment_intent_id = invoice.get("payment_intent")
        invoice_id = invoice.get("id")

        if not subscription_id:
            return

        # Find subscription
        result = await db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            logger.warning(f"Subscription {subscription_id} not found")
            return

        # Create transaction record
        transaction = Transaction(
            subscription_id=subscription.id,
            stripe_payment_intent_id=payment_intent_id,
            stripe_invoice_id=invoice_id,
            amount=invoice.get("amount_paid") / 100,  # Convert from cents
            currency=invoice.get("currency", "usd"),
            transaction_type=TransactionType.PAYMENT.value,
            status="succeeded",
            description=f"Payment for {subscription.plan} plan",
            receipt_url=invoice.get("hosted_invoice_url"),
        )

        db.add(transaction)
        await db.commit()
        logger.info(f"Payment succeeded for subscription {subscription_id}")

    @staticmethod
    async def _handle_payment_failed(data: Dict[str, Any], db: AsyncSession) -> None:
        """Handle invoice.payment_failed event"""
        invoice = data.get("object", {})
        subscription_id = invoice.get("subscription")
        payment_intent_id = invoice.get("payment_intent")
        invoice_id = invoice.get("id")

        if not subscription_id:
            return

        # Find subscription
        result = await db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            logger.warning(f"Subscription {subscription_id} not found")
            return

        # Update subscription status
        subscription.status = SubscriptionStatus.PAST_DUE.value

        # Create transaction record
        transaction = Transaction(
            subscription_id=subscription.id,
            stripe_payment_intent_id=payment_intent_id or f"failed_{invoice_id}",
            stripe_invoice_id=invoice_id,
            amount=invoice.get("amount_due") / 100,
            currency=invoice.get("currency", "usd"),
            transaction_type=TransactionType.FAILED.value,
            status="failed",
            description=f"Failed payment for {subscription.plan} plan",
        )

        db.add(transaction)
        await db.commit()
        logger.warning(f"Payment failed for subscription {subscription_id}")

    @staticmethod
    def _get_plan_from_price_id(price_id: str) -> str:
        """Determine plan type from Stripe price ID"""
        if price_id == settings.stripe_pro_price_id:
            return SubscriptionPlan.PRO.value
        elif price_id == settings.stripe_enterprise_price_id:
            return SubscriptionPlan.ENTERPRISE.value
        else:
            return SubscriptionPlan.FREE.value

    @staticmethod
    async def get_available_plans() -> List[PlanInfo]:
        """Get list of available subscription plans with their details"""
        plans = []

        # Basic Plan (formerly Free Plan)
        basic_limits = PlanLimits(
            max_quality="720p",
            max_concurrent_streams=1,
            max_stream_duration_hours=1,
            support_response_hours=72,
            support_channels=["email"],
            chat_filter=False,
            oauth_enabled=False,
            analytics_enabled=False,
        )

        plans.append(PlanInfo(
            name="Basic",
            plan_type=SubscriptionPlan.FREE,
            price=0.0,
            currency="usd",
            interval="14 days trial",
            features=[
                "14-day free trial",
                "720p streaming quality",
                "1 concurrent stream",
                "1 hour stream duration",
                "Email support (72h response)",
            ],
            limits=basic_limits,
            stripe_price_id=settings.stripe_free_price_id,
            stripe_product_id="",
            is_popular=False,
        ))

        # Pro Plan
        pro_limits = PlanLimits(
            max_quality="1080p",
            max_concurrent_streams=10,
            max_stream_duration_hours=None,
            support_response_hours=24,
            support_channels=["email", "chat"],
            chat_filter=False,
            oauth_enabled=False,
            analytics_enabled=False,
        )

        plans.append(PlanInfo(
            name="Pro",
            plan_type=SubscriptionPlan.PRO,
            price=69.0,
            currency="usd",
            interval="month",
            features=[
                "Everything in Basic +",
                "Up to 1080p streaming quality",
                "Up to 10 concurrent streams",
                "Unlimited stream duration",
                "24h priority support (email & chat)",
            ],
            limits=pro_limits,
            stripe_price_id=settings.stripe_pro_price_id,
            stripe_product_id="",
            is_popular=True,
        ))

        # Enterprise Plan
        enterprise_limits = PlanLimits(
            max_quality="4K",
            max_concurrent_streams=None,
            max_stream_duration_hours=None,
            support_response_hours=0,
            support_channels=["email", "chat"],
            chat_filter=True,
            oauth_enabled=True,
            analytics_enabled=True,
        )

        plans.append(PlanInfo(
            name="Enterprise",
            plan_type=SubscriptionPlan.ENTERPRISE,
            price=0.0,  # Custom pricing
            currency="usd",
            interval="month",
            features=[
                "Everything in Pro +",
                "Up to 4K streaming quality",
                "Unlimited concurrent streams",
                "24/7 dedicated support",
                "Chat content filter",
                "OAuth integration",
                "Advanced analytics",
                "Custom pricing",
            ],
            limits=enterprise_limits,
            stripe_price_id=settings.stripe_enterprise_price_id,
            stripe_product_id="",
            is_popular=False,
        ))

        return plans
