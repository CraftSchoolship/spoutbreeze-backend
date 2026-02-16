#!/usr/bin/env python3
"""
Script to manually create a test transaction for testing purposes.
Run this to verify the transaction display works in the UI.
"""

import asyncio
import sys
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from datetime import datetime
from app.models.payment_models import Transaction, Subscription
from app.config.settings import get_settings

settings = get_settings()

async def create_test_transaction():
    """Create a test transaction for the current user's subscription"""
    
    # Create async engine
    engine = create_async_engine(settings.db_url, echo=True)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async with async_session() as session:
        # Get the first active subscription
        result = await session.execute(
            select(Subscription).where(Subscription.status.in_(["active", "trialing"]))
        )
        subscription = result.scalar_one_or_none()
        
        if not subscription:
            print("No active subscription found!")
            return
        
        print(f"Found subscription: {subscription.id}")
        print(f"User ID: {subscription.user_id}")
        print(f"Plan: {subscription.plan}")
        print(f"Status: {subscription.status}")
        
        # Create a test transaction
        transaction = Transaction(
            subscription_id=subscription.id,
            stripe_payment_intent_id=f"test_pi_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            stripe_invoice_id=f"test_in_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            amount=29.99 if subscription.plan == "pro" else 99.99,
            currency="usd",
            transaction_type="payment",
            status="succeeded",
            description=f"Test payment for {subscription.plan} plan",
            receipt_url="https://stripe.com/test_receipt",
        )
        
        session.add(transaction)
        await session.commit()
        await session.refresh(transaction)
        
        print(f"\n✅ Test transaction created successfully!")
        print(f"Transaction ID: {transaction.id}")
        print(f"Amount: ${transaction.amount}")
        print(f"Status: {transaction.status}")
        print(f"\nRefresh the UI to see the transaction.")

if __name__ == "__main__":
    asyncio.run(create_test_transaction())
