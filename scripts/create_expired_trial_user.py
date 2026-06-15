#!/usr/bin/env python3
"""
Script to create a test user with an expired free trial.
This registers the user in both Firebase Authentication and the local DB, then
sets up an expired 14-day free trial so the user can never use the free plan
again.

Usage:
    python scripts/create_expired_trial_user.py
    python scripts/create_expired_trial_user.py --email test@example.com --password Test1234!
"""

import asyncio
import sys
import uuid
from datetime import timedelta

from firebase_admin import auth as fb_auth
from sqlalchemy import select

from app.config.database.session import SessionLocal
from app.config.firebase_config import get_firebase_app
from app.config.settings import get_settings
from app.models.payment_models import (
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
)

# Import all models to ensure they're registered with SQLAlchemy
from app.models.user_models import User
from app.utils.datetime_utils import utcnow

DEFAULT_EMAIL = "expired_trial_user@test.com"
DEFAULT_USERNAME = "expired_trial_user"
DEFAULT_FIRST_NAME = "Expired"
DEFAULT_LAST_NAME = "TrialUser"
DEFAULT_PASSWORD = "Test1234!"


async def create_expired_trial_user(
    email: str = DEFAULT_EMAIL,
    username: str = DEFAULT_USERNAME,
    first_name: str = DEFAULT_FIRST_NAME,
    last_name: str = DEFAULT_LAST_NAME,
    password: str = DEFAULT_PASSWORD,
):
    """
    1. Create the user in Firebase
    2. Create the user in the local DB
    3. Create an expired free trial subscription
    4. Mark has_used_free_trial = True
    """
    settings = get_settings()

    if get_firebase_app() is None:
        print("   ❌ Firebase Admin SDK not configured (FIREBASE_SERVICE_ACCOUNT_BASE64).")
        return

    # ── Step 1: Create in Firebase ──────────────────────────────────
    print("\n🔥 Creating user in Firebase …")
    display_name = f"{first_name} {last_name}".strip()

    try:
        record = fb_auth.create_user(
            email=email,
            password=password,
            display_name=display_name,
            email_verified=True,
        )
        firebase_uid = record.uid
        print(f"   ✅ Firebase user created — UID: {firebase_uid}")
    except fb_auth.EmailAlreadyExistsError:
        record = fb_auth.get_user_by_email(email)
        firebase_uid = record.uid
        print(f"   ℹ️  Firebase user already exists — UID: {firebase_uid}")

    # ── Step 2 & 3: Create in DB + expired subscription ─────────────
    print("\n💾 Creating user & expired subscription in DB …")

    async with SessionLocal() as session:
        # Check if user already in DB
        result = await session.execute(select(User).where(User.firebase_uid == firebase_uid))
        user = result.scalar_one_or_none()

        if user:
            print(f"   ℹ️  User already in DB — ID: {user.id}")
        else:
            user = User(
                firebase_uid=firebase_uid,
                email=email,
                username=username,
                first_name=first_name,
                last_name=last_name,
                roles="moderator",
                is_active=True,
                unlimited_access=False,
                has_used_free_trial=True,  # Permanently mark trial as used
            )
            session.add(user)
            await session.flush()  # get user.id
            print(f"   ✅ DB user created — ID: {user.id}")

        # Ensure has_used_free_trial is True
        user.has_used_free_trial = True

        # Check for existing subscription
        result = await session.execute(select(Subscription).where(Subscription.user_id == user.id))
        existing_sub = result.scalar_one_or_none()

        if existing_sub:
            print("   ℹ️  Subscription already exists — updating to expired …")
            existing_sub.plan = SubscriptionPlan.FREE.value
            existing_sub.status = SubscriptionStatus.EXPIRED.value
            existing_sub.trial_start = utcnow() - timedelta(days=30)
            existing_sub.trial_end = utcnow() - timedelta(days=16)
            existing_sub.current_period_start = utcnow() - timedelta(days=30)
            existing_sub.current_period_end = utcnow() - timedelta(days=16)
        else:
            # Create a subscription that expired 16 days ago (started 30 days ago)
            trial_start = utcnow() - timedelta(days=30)
            trial_end = trial_start + timedelta(days=14)  # ended 16 days ago

            # We need a stripe_customer_id — use a placeholder since this is a test user
            import stripe

            stripe.api_key = settings.stripe_secret_key

            try:
                customer = stripe.Customer.create(
                    email=email,
                    name=f"{first_name} {last_name}",
                    metadata={
                        "user_id": str(user.id),
                        "username": username,
                        "test_user": "true",
                    },
                )
                stripe_customer_id = customer.id
                print(f"   ✅ Stripe customer created — ID: {stripe_customer_id}")
            except Exception:
                # If Stripe is not configured, use a placeholder
                stripe_customer_id = f"cus_test_{uuid.uuid4().hex[:14]}"
                print(f"   ⚠️  Stripe not available, using placeholder: {stripe_customer_id}")

            subscription = Subscription(
                user_id=user.id,
                stripe_customer_id=stripe_customer_id,
                plan=SubscriptionPlan.FREE.value,
                status=SubscriptionStatus.EXPIRED.value,
                trial_start=trial_start,
                trial_end=trial_end,
                current_period_start=trial_start,
                current_period_end=trial_end,
            )
            session.add(subscription)
            print(f"   ✅ Expired subscription created (trial ended {trial_end.strftime('%Y-%m-%d')})")

        await session.commit()

    # ── Summary ─────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("  ✅ Test user with expired free trial is ready!")
    print(f"{'=' * 60}")
    print(f"  Email:             {email}")
    print(f"  Username:          {username}")
    print(f"  Password:          {password}")
    print(f"  Firebase UID:      {firebase_uid}")
    print("  Free trial:        EXPIRED (used & cannot be restarted)")
    print("  has_used_free_trial: True")
    print(f"{'=' * 60}")
    print("\n  👉 Log in with these credentials to verify that:")
    print("     • The free plan button shows 'Trial Used' (disabled)")
    print("     • The user cannot start a new free trial")
    print("     • Upgrading to Pro/Enterprise still works\n")


async def main():
    email = DEFAULT_EMAIL
    password = DEFAULT_PASSWORD

    # Parse CLI args
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--email" and i + 1 < len(args):
            email = args[i + 1]
            i += 2
        elif args[i] == "--password" and i + 1 < len(args):
            password = args[i + 1]
            i += 2
        else:
            i += 1

    # Derive username from email if using custom email
    username = email.split("@")[0].replace(".", "_").replace("+", "_")

    await create_expired_trial_user(
        email=email,
        username=username,
        password=password,
    )


if __name__ == "__main__":
    asyncio.run(main())
