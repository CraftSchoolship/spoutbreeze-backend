#!/usr/bin/env python3
"""
Script to create a test user with an expired free trial.
This registers the user in both Keycloak and the local DB, then sets up
an expired 14-day free trial so the user can never use the free plan again.

Usage:
    python scripts/create_expired_trial_user.py
    python scripts/create_expired_trial_user.py --email test@example.com --password Test1234!
"""

import asyncio
import sys
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from keycloak import KeycloakAdmin
from keycloak.exceptions import KeycloakPostError

from app.config.database.session import SessionLocal
from app.config.settings import get_settings, verify_ssl

# Import all models to ensure they're registered with SQLAlchemy
from app.models.user_models import User
from app.models.payment_models import (
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    Transaction,
)
from app.models.twitch.twitch_models import TwitchToken


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
    1. Create the user in Keycloak
    2. Create the user in the local DB
    3. Create an expired free trial subscription
    4. Mark has_used_free_trial = True
    """
    settings = get_settings()

    # Create a KeycloakAdmin instance using admin credentials (master realm)
    kc_admin = KeycloakAdmin(
        server_url=settings.keycloak_server_url,
        username=settings.keycloak_admin_username,
        password=settings.keycloak_admin_password,
        realm_name=settings.keycloak_realm,
        verify=verify_ssl,
    )

    # ── Step 1: Create in Keycloak ──────────────────────────────────
    print(f"\n🔑 Creating user in Keycloak …")
    keycloak_id = None

    try:
        keycloak_id = kc_admin.create_user(
            {
                "email": email,
                "username": username,
                "firstName": first_name,
                "lastName": last_name,
                "enabled": True,
                "emailVerified": True,
                "credentials": [
                    {
                        "type": "password",
                        "value": password,
                        "temporary": False,
                    }
                ],
            }
        )
        print(f"   ✅ Keycloak user created — ID: {keycloak_id}")
    except KeycloakPostError as e:
        if "User exists" in str(e) or "409" in str(e):
            # User already exists — look up by username
            users = kc_admin.get_users({"username": username, "exact": True})
            if users:
                keycloak_id = users[0]["id"]
                print(f"   ℹ️  Keycloak user already exists — ID: {keycloak_id}")
            else:
                print(f"   ❌ Keycloak user exists but could not be looked up.")
                return
        else:
            print(f"   ❌ Keycloak error: {e}")
            return

    # ── Step 2 & 3: Create in DB + expired subscription ─────────────
    print(f"\n💾 Creating user & expired subscription in DB …")

    async with SessionLocal() as session:
        # Check if user already in DB
        result = await session.execute(
            select(User).where(User.keycloak_id == keycloak_id)
        )
        user = result.scalar_one_or_none()

        if user:
            print(f"   ℹ️  User already in DB — ID: {user.id}")
        else:
            user = User(
                keycloak_id=keycloak_id,
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
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        existing_sub = result.scalar_one_or_none()

        if existing_sub:
            print(f"   ℹ️  Subscription already exists — updating to expired …")
            existing_sub.plan = SubscriptionPlan.FREE.value
            existing_sub.status = SubscriptionStatus.EXPIRED.value
            existing_sub.trial_start = datetime.utcnow() - timedelta(days=30)
            existing_sub.trial_end = datetime.utcnow() - timedelta(days=16)
            existing_sub.current_period_start = datetime.utcnow() - timedelta(days=30)
            existing_sub.current_period_end = datetime.utcnow() - timedelta(days=16)
        else:
            # Create a subscription that expired 16 days ago (started 30 days ago)
            trial_start = datetime.utcnow() - timedelta(days=30)
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
            except Exception as e:
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
    print(f"  ✅ Test user with expired free trial is ready!")
    print(f"{'=' * 60}")
    print(f"  Email:             {email}")
    print(f"  Username:          {username}")
    print(f"  Password:          {password}")
    print(f"  Keycloak ID:       {keycloak_id}")
    print(f"  Free trial:        EXPIRED (used & cannot be restarted)")
    print(f"  has_used_free_trial: True")
    print(f"{'=' * 60}")
    print(f"\n  👉 Log in with these credentials to verify that:")
    print(f"     • The free plan button shows 'Trial Used' (disabled)")
    print(f"     • The user cannot start a new free trial")
    print(f"     • Upgrading to Pro/Enterprise still works\n")


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
