#!/usr/bin/env python3
"""
One-time migration: import existing users into Firebase Authentication.

Run this AFTER applying the Alembic migration that renames
``users.keycloak_id`` -> ``users.firebase_uid`` (the column values are
unchanged — they are still the old Keycloak ``sub`` UUIDs).

For every row in the local ``users`` table this script creates a matching
Firebase user whose **uid is set to the existing ``firebase_uid``** (the old
Keycloak sub). Because the uid matches the value already stored in the DB,
every existing user row keeps working with no data rewrite — the backend looks
users up by ``firebase_uid`` and Firebase tokens carry that same uid.

It also mirrors each user's DB ``roles`` into the Firebase ``roles`` custom
claim so the frontend middleware route-guards keep working.

Passwords are NOT migrated (Keycloak hashes aren't portable). Imported users
sign in for the first time via "Forgot password" (email/password) or via
Google sign-in on the same verified email. Pass ``--send-reset-emails`` to
generate + print a password-reset link for each user.

Idempotent: re-running updates existing Firebase users in place.

Usage:
    python scripts/migrate_users_to_firebase.py --dry-run
    python scripts/migrate_users_to_firebase.py
    python scripts/migrate_users_to_firebase.py --send-reset-emails
"""

import asyncio
import sys
from pathlib import Path

# Allow running directly (`./scripts/migrate_users_to_firebase.py`) by putting
# the repo root on sys.path so `import app...` resolves.
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

# Firebase import_users accepts at most 1000 records per call.
BATCH_SIZE = 1000


async def load_users() -> list[User]:
    async with SessionLocal() as session:
        result = await session.execute(select(User))
        return list(result.scalars().all())


def build_import_records(users: list[User]) -> list[fb_auth.ImportUserRecord]:
    records: list[fb_auth.ImportUserRecord] = []
    for u in users:
        if not u.firebase_uid or not u.email:
            print(f"   ⚠️  Skipping user {u.id} — missing firebase_uid or email")
            continue
        display_name = f"{u.first_name or ''} {u.last_name or ''}".strip() or None
        roles = u.get_roles_list()
        records.append(
            fb_auth.ImportUserRecord(
                uid=u.firebase_uid,
                email=u.email,
                # Keycloak emails were verified; preserve that so Google
                # account-linking on the same email works smoothly.
                email_verified=True,
                display_name=display_name,
                custom_claims={"roles": roles} if roles else None,
            )
        )
    return records


def import_batch(records: list[fb_auth.ImportUserRecord]) -> tuple[int, int]:
    result = fb_auth.import_users(records)
    for err in result.errors:
        print(f"   ❌ index {err.index}: {err.reason}")
    return result.success_count, result.failure_count


def maybe_send_reset(users: list[User]) -> None:
    print("\n✉️  Generating password-reset links …")
    for u in users:
        if not u.email:
            continue
        try:
            link = fb_auth.generate_password_reset_link(u.email)
            print(f"   {u.email}: {link}")
        except Exception as e:
            print(f"   ⚠️  {u.email}: could not generate link ({e})")


async def main() -> None:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    send_reset = "--send-reset-emails" in args

    if get_firebase_app() is None:
        print("❌ Firebase Admin SDK not configured (set FIREBASE_SERVICE_ACCOUNT_BASE64).")
        return

    print("📥 Loading users from the database …")
    users = await load_users()
    print(f"   Found {len(users)} user(s)")

    records = build_import_records(users)
    print(f"   Prepared {len(records)} importable record(s)")

    if dry_run:
        print("\n🧪 Dry run — no users imported. Sample of what would be sent:")
        for r in records[:10]:
            print(f"   uid={r.uid} email={r.email} claims={r.custom_claims}")
        if len(records) > 10:
            print(f"   … and {len(records) - 10} more")
        return

    total_ok = total_fail = 0
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        print(f"\n⬆️  Importing batch {i // BATCH_SIZE + 1} ({len(batch)} users) …")
        ok, fail = import_batch(batch)
        total_ok += ok
        total_fail += fail
        print(f"   ✅ {ok} succeeded, ❌ {fail} failed")

    print("\n" + "=" * 60)
    print(f"  Import complete: {total_ok} succeeded, {total_fail} failed")
    print("=" * 60)

    if send_reset:
        maybe_send_reset(users)


if __name__ == "__main__":
    asyncio.run(main())
