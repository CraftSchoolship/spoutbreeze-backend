"""
Test script to diagnose payment endpoint issues
"""

import asyncio

from sqlalchemy import select

from app.config.database.session import SessionLocal
from app.models.user_models import User
from app.services.payment_service import PaymentService


async def test_payment():
    async with SessionLocal() as db:
        # Get a user from database
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()

        if not user:
            print("❌ No users found in database")
            return

        print(f"✅ Testing with user: {user.email} (ID: {user.id})")

        try:
            # Test getting subscription
            print("\n📋 Testing get_user_subscription...")
            subscription = await PaymentService.get_user_subscription(user, db)

            if subscription:
                print(f"✅ Found subscription: {subscription.plan} - {subscription.status}")
            else:
                print("ℹ️  No subscription found, will create free trial...")

                # Test creating free subscription
                print("\n📋 Testing create_free_subscription...")
                subscription = await PaymentService.create_free_subscription(user, db)
                print(f"✅ Created subscription: {subscription.plan} - {subscription.status}")
                print(f"   Trial ends: {subscription.trial_end}")

        except Exception as e:
            print(f"❌ Error: {str(e)}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_payment())
