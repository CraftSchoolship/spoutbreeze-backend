#!/usr/bin/env python3
"""
Create a SUPER ADMIN user in Firebase Authentication + the local DB.

A super_admin has platform-wide back-office access (analytics, cross-tenant
user management, infra controls). The `admin` role is reserved for the
organization-scoped admin tier.

- Creates (or reuses) the Firebase user (email/password)
- Sets the `roles` custom claim to ["super_admin"] so it rides in the ID token
  / session cookie and the frontend middleware lets /admin pages render
- Creates (or updates) the matching local DB row with roles="super_admin",
  keyed by firebase_uid

Usage:
    python scripts/create_super_admin_user.py
    python scripts/create_super_admin_user.py --email admin@admin.com --password admin
"""

import asyncio
import sys
from pathlib import Path

# Allow running directly (`./scripts/create_super_admin_user.py`) by putting
# the repo root on sys.path so `import app...` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firebase_admin import auth as fb_auth
from sqlalchemy import select

from app.config.database.session import SessionLocal
from app.config.firebase_config import get_firebase_app

# Import all models so SQLAlchemy can resolve string-based relationships
# on the User mapper (Subscription, Notification, FCMToken, etc.).
from app.models import (  # noqa: F401
    bbb_models,
    channel,
    connection_model,
    event,
    fcm_token_model,
    notification_models,
    payment_models,
    stream_models,
    stream_session_models,
)
from app.models.user_models import User

DEFAULT_EMAIL = "admin@admin.com"
DEFAULT_USERNAME = "admin"
DEFAULT_FIRST_NAME = "Super"
DEFAULT_LAST_NAME = "Admin"
DEFAULT_PASSWORD = "admin"

TARGET_ROLE = "super_admin"
LEGACY_ROLE = "admin"


async def create_super_admin_user(
    email: str,
    username: str,
    first_name: str,
    last_name: str,
    password: str,
) -> None:
    if get_firebase_app() is None:
        print("   ❌ Firebase Admin SDK not configured (FIREBASE_SERVICE_ACCOUNT_BASE64).")
        return

    display_name = f"{first_name} {last_name}".strip()

    # ── Step 1: Create (or reuse) the Firebase user ─────────────────
    print("\n🔥 Creating user in Firebase …")
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
        try:
            fb_auth.update_user(firebase_uid, password=password, email_verified=True)
            print("   ✅ Password reset to requested value")
        except Exception as pw_err:
            print(f"   ⚠️  Could not reset password: {pw_err}")

    # ── Step 2: Set the super_admin custom claim ────────────────────
    print(f"\n🛡️  Setting '{TARGET_ROLE}' custom claim …")
    existing_claims = dict(record.custom_claims or {})
    existing_claims["roles"] = [TARGET_ROLE]
    fb_auth.set_custom_user_claims(firebase_uid, existing_claims)
    print("   ✅ Claim set")

    # ── Step 3: Create (or update) the local DB row ─────────────────
    print("\n💾 Syncing user into local DB …")
    async with SessionLocal() as session:
        # Match an existing row by firebase_uid OR email. The email lookup
        # reconciles the case where a row already exists under a different
        # (e.g. pre-migration / placeholder) uid — we rebind it to the real
        # Firebase uid rather than colliding on the unique email constraint.
        result = await session.execute(
            select(User).where((User.firebase_uid == firebase_uid) | (User.email == email))
        )
        user = result.scalars().first()

        if user:
            if user.firebase_uid != firebase_uid:
                print(f"   ℹ️  Rebinding existing row from uid {user.firebase_uid} → {firebase_uid}")
            user.firebase_uid = firebase_uid
            user.email = email
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            existing_roles = set(user.get_roles_list())
            existing_roles.discard(LEGACY_ROLE)
            existing_roles.add(TARGET_ROLE)
            user.set_roles_list(sorted(existing_roles))
            user.is_active = True
            print(f"   ℹ️  Updated existing DB user — ID: {user.id} (roles: {user.roles})")
        else:
            user = User(
                firebase_uid=firebase_uid,
                email=email,
                username=username,
                first_name=first_name,
                last_name=last_name,
                roles=TARGET_ROLE,
                is_active=True,
            )
            session.add(user)
            await session.flush()
            print(f"   ✅ Created DB user — ID: {user.id}")

        await session.commit()

    print("\n" + "=" * 60)
    print("  ✅ Super Admin user is ready")
    print("=" * 60)
    print(f"  Email:        {email}")
    print(f"  Password:     {password}")
    print(f"  Firebase UID: {firebase_uid}")
    print(f"  Role:         {TARGET_ROLE}")
    print("=" * 60)
    print("\n  👉 Sign in at the frontend; the 'Admin' link should appear\n")


async def main() -> None:
    email = DEFAULT_EMAIL
    password = DEFAULT_PASSWORD
    username = DEFAULT_USERNAME

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--email" and i + 1 < len(args):
            email = args[i + 1]
            username = email.split("@")[0].replace(".", "_").replace("+", "_")
            i += 2
        elif args[i] == "--password" and i + 1 < len(args):
            password = args[i + 1]
            i += 2
        elif args[i] == "--username" and i + 1 < len(args):
            username = args[i + 1]
            i += 2
        else:
            i += 1

    await create_super_admin_user(
        email=email,
        username=username,
        first_name=DEFAULT_FIRST_NAME,
        last_name=DEFAULT_LAST_NAME,
        password=password,
    )


if __name__ == "__main__":
    asyncio.run(main())
