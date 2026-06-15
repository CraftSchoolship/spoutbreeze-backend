#!/usr/bin/env python3
"""
Reconcile the local users table against Firebase Authentication.

Deleting a user directly in the Firebase Console (or via the Admin SDK) does
NOT touch the database — only the app's delete endpoints (DELETE /api/me,
DELETE /api/users/{id}) remove both sides. This script finds DB rows whose
``firebase_uid`` no longer exists in Firebase ("orphans") and optionally
deletes them.

Dry-run by default — it only reports. Pass ``--delete`` to actually remove the
orphaned DB rows (cascades to their related data, same as the model relations).

Usage:
    python scripts/reconcile_users.py            # report orphans
    python scripts/reconcile_users.py --delete   # delete orphaned DB rows
"""

import asyncio
import sys
from pathlib import Path

# Allow running directly: put the repo root on sys.path so `import app` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firebase_admin import auth as fb_auth
from sqlalchemy import select

from app.config.database.session import SessionLocal
from app.config.firebase_config import get_firebase_app

# Import all models so SQLAlchemy can resolve string-based relationships.
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


async def firebase_uid_exists(uid: str) -> bool:
    try:
        await asyncio.to_thread(fb_auth.get_user, uid)
        return True
    except fb_auth.UserNotFoundError:
        return False
    except Exception as e:
        # On any other error, err on the safe side: treat as existing so we
        # never delete a DB row because of a transient Firebase hiccup.
        print(f"   ⚠️  Could not verify uid {uid} ({e}); treating as existing")
        return True


async def main() -> None:
    do_delete = "--delete" in sys.argv[1:]

    if get_firebase_app() is None:
        print("❌ Firebase Admin SDK not configured (FIREBASE_SERVICE_ACCOUNT_BASE64).")
        return

    print("📥 Loading users from the database …")
    async with SessionLocal() as session:
        users = list((await session.execute(select(User))).scalars().all())
        print(f"   Found {len(users)} user(s)")

        orphans: list[User] = []
        for u in users:
            if not u.firebase_uid or not await firebase_uid_exists(u.firebase_uid):
                orphans.append(u)

        if not orphans:
            print("\n✅ No orphaned rows — DB and Firebase are in sync.")
            return

        print(f"\n🔎 {len(orphans)} orphaned DB row(s) (no matching Firebase user):")
        for u in orphans:
            print(f"   - {u.email} (id={u.id}, firebase_uid={u.firebase_uid}, roles={u.roles})")

        if not do_delete:
            print("\n🧪 Dry run — nothing deleted. Re-run with --delete to remove these rows.")
            return

        for u in orphans:
            await session.delete(u)
        await session.commit()
        print(f"\n🗑️  Deleted {len(orphans)} orphaned DB row(s).")


if __name__ == "__main__":
    asyncio.run(main())
