#!/usr/bin/env python3
"""
Script to grant unlimited access to a specific user by email.
Usage: python grant_unlimited_access.py
"""

import asyncio
import sys
from sqlalchemy import select, update
from app.config.database.session import SessionLocal

# Import all models to ensure they're registered
from app.models.user_models import User
from app.models.payment_models import Subscription, Transaction
from app.models.twitch.twitch_models import TwitchToken


async def grant_unlimited_access(email: str):
    """Grant unlimited access to a user by email"""
    async with SessionLocal() as session:
        # Find user by email
        result = await session.execute(
            select(User).where(User.email == email)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            print(f"❌ User with email '{email}' not found in the database.")
            print(f"\n💡 Note: Users are synced from Keycloak to the database on first login.")
            print(f"   The user needs to log in at least once before you can grant unlimited access.")
            print(f"\n   After the user logs in, run this script again.")
            return False
        
        # Update unlimited_access field
        user.unlimited_access = True
        await session.commit()
        
        print(f"✅ Successfully granted unlimited access to user:")
        print(f"   - ID: {user.id}")
        print(f"   - Email: {user.email}")
        print(f"   - Username: {user.username}")
        print(f"   - Name: {user.first_name} {user.last_name}")
        return True


async def main():
    email = "mohamed@mohamed.com"
    
    # Check if we need to list users
    if len(sys.argv) > 1:
        if sys.argv[1] == "--list":
            await list_users()
            return
        else:
            email = sys.argv[1]
    
    print(f"🔧 Granting unlimited access to: {email}")
    print("-" * 50)
    
    success = await grant_unlimited_access(email)
    
    if success:
        print("-" * 50)
        print("✅ Done! User now has unlimited access to all features.")
    else:
        sys.exit(1)


async def list_users():
    """List all users in the database"""
    async with SessionLocal() as session:
        result = await session.execute(
            select(User.email, User.username, User.first_name, User.last_name, User.unlimited_access)
        )
        users = result.all()
        
        if not users:
            print("No users found in database.")
            return
        
        print("📋 Users in database:")
        print("-" * 80)
        for email, username, first_name, last_name, unlimited in users:
            status = "✅ UNLIMITED" if unlimited else "   standard"
            print(f"{status} | {email:30} | {username or 'N/A':20} | {first_name} {last_name}")
        print("-" * 80)


if __name__ == "__main__":
    asyncio.run(main())
