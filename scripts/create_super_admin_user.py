#!/usr/bin/env python3
"""
Create a SUPER ADMIN user in Keycloak + the local DB.

A super_admin has platform-wide back-office access (analytics, cross-tenant
user management, infra controls). The `admin` role is reserved for the future
organization-scoped admin tier.

- Creates (or reuses) the Keycloak user
- Ensures the `super_admin` realm role exists, then assigns it to the user
  (so the JWT's realm_access.roles contains "super_admin", which the frontend
  middleware checks before letting /admin pages render)
- If the user previously had the bare `admin` role, that assignment is removed
  so role membership stays clean during the role-model migration
- Creates (or updates) the matching local DB row with roles="super_admin"

Usage:
    python scripts/create_super_admin_user.py
    python scripts/create_super_admin_user.py --email admin@admin.com --password admin
"""

import asyncio
import sys

from keycloak import KeycloakAdmin
from keycloak.exceptions import KeycloakPostError
from sqlalchemy import select

from app.config.database.session import SessionLocal
from app.config.settings import get_settings, verify_ssl

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
LEGACY_ROLE = "admin"  # If the user still has this from before the rename, strip it.


async def create_super_admin_user(
    email: str,
    username: str,
    first_name: str,
    last_name: str,
    password: str,
) -> None:
    settings = get_settings()

    kc_admin = KeycloakAdmin(
        server_url=settings.keycloak_server_url,
        username=settings.keycloak_admin_username,
        password=settings.keycloak_admin_password,
        realm_name=settings.keycloak_realm,
        user_realm_name="master",
        verify=verify_ssl,
    )

    # ── Step 1: Create (or reuse) Keycloak user ─────────────────────
    print("\n🔑 Creating user in Keycloak …")
    keycloak_id: str | None = None

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
                    {"type": "password", "value": password, "temporary": False}
                ],
            }
        )
        print(f"   ✅ Keycloak user created — ID: {keycloak_id}")
    except KeycloakPostError as e:
        if "User exists" in str(e) or "409" in str(e):
            users = kc_admin.get_users({"username": username, "exact": True})
            if not users:
                users = kc_admin.get_users({"email": email, "exact": True})
            if users:
                keycloak_id = users[0]["id"]
                print(f"   ℹ️  Keycloak user already exists — ID: {keycloak_id}")
                try:
                    kc_admin.set_user_password(
                        user_id=keycloak_id, password=password, temporary=False
                    )
                    print("   ✅ Password reset to requested value")
                except Exception as pw_err:
                    print(f"   ⚠️  Could not reset password: {pw_err}")
            else:
                print("   ❌ User exists in Keycloak but lookup failed.")
                return
        else:
            print(f"   ❌ Keycloak error: {e}")
            return

    assert keycloak_id is not None

    # ── Step 2: Ensure the `super_admin` realm role exists ──────────
    print(f"\n🛡️  Ensuring '{TARGET_ROLE}' realm role exists …")
    try:
        kc_admin.get_realm_role(TARGET_ROLE)
        print(f"   ℹ️  Realm role '{TARGET_ROLE}' already exists")
    except Exception:
        kc_admin.create_realm_role(
            {"name": TARGET_ROLE, "description": "Platform-wide back-office admin"}
        )
        print(f"   ✅ Created realm role '{TARGET_ROLE}'")

    # ── Step 3: Assign `super_admin` and strip legacy `admin` ───────
    print(f"\n🎟️  Assigning '{TARGET_ROLE}' realm role …")
    target_role = kc_admin.get_realm_role(TARGET_ROLE)
    kc_admin.assign_realm_roles(user_id=keycloak_id, roles=[target_role])
    print("   ✅ Role assigned")

    try:
        legacy_role = kc_admin.get_realm_role(LEGACY_ROLE)
        current_roles = kc_admin.get_realm_roles_of_user(user_id=keycloak_id)
        if any(r.get("name") == LEGACY_ROLE for r in current_roles):
            kc_admin.delete_realm_roles_of_user(
                user_id=keycloak_id, roles=[legacy_role]
            )
            print(f"   ✅ Removed stale '{LEGACY_ROLE}' realm role assignment")
    except Exception:
        # Legacy role doesn't exist or user never had it — both fine.
        pass

    # ── Step 4: Create (or update) the local DB row ─────────────────
    print("\n💾 Syncing user into local DB …")
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.keycloak_id == keycloak_id))
        user = result.scalar_one_or_none()

        if user:
            user.email = email
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            existing_roles = set(user.get_roles_list())
            existing_roles.discard(LEGACY_ROLE)  # strip stale `admin`
            existing_roles.add(TARGET_ROLE)
            user.set_roles_list(sorted(existing_roles))
            user.is_active = True
            print(f"   ℹ️  Updated existing DB user — ID: {user.id} (roles: {user.roles})")
        else:
            user = User(
                keycloak_id=keycloak_id,
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
    print(f"  Username:     {username}")
    print(f"  Password:     {password}")
    print(f"  Keycloak ID:  {keycloak_id}")
    print(f"  DB role:      {TARGET_ROLE}")
    print(f"  Realm role:   {TARGET_ROLE}")
    print("=" * 60)
    print("\n  👉 Log in at the frontend; the 'Admin' link should appear\n")


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
