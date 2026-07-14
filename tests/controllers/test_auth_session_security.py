"""Security regression tests for the Firebase session-establishment path.

Covers F1 (account takeover via unverified-email rebind): `process_user_info`
must never bind an existing user row to a new Firebase uid, nor auto-assign an
organization, on the strength of an *unverified* email address.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.controllers.auth_controller import process_user_info
from app.models.auth_models import SessionRequest
from app.models.organization_models import Organization, OrganizationEmailDomain
from app.models.user_models import User
from app.utils.datetime_utils import utcnow

pytestmark = pytest.mark.anyio


def _token(uid: str, email: str, *, email_verified: bool) -> dict:
    return {"uid": uid, "email": email, "email_verified": email_verified}


async def test_unverified_email_cannot_rebind_existing_account(db_session):
    """An unverified token whose email matches an existing (privileged) row
    must be rejected (403) and must NOT rebind that row to the caller's uid."""
    victim = User(
        id=uuid.uuid4(),
        firebase_uid="legacy-keycloak-sub-victim",
        username="victim",
        email="victim@corp.com",
        first_name="Vic",
        last_name="Tim",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    victim.set_roles_list(["super_admin"])
    db_session.add(victim)
    await db_session.commit()

    attacker_token = _token("attacker-fresh-uid", "victim@corp.com", email_verified=False)

    with pytest.raises(HTTPException) as exc:
        await process_user_info(attacker_token, SessionRequest(id_token="x"), db_session)
    assert exc.value.status_code == 403

    await db_session.refresh(victim)
    # The row was NOT rebound and roles were NOT transferred.
    assert victim.firebase_uid == "legacy-keycloak-sub-victim"
    assert victim.get_roles_list() == ["super_admin"]


async def test_verified_email_rebinds_migrated_account(db_session):
    """A verified token whose email matches a migrated row (stale uid) should
    rebind that row to the live Firebase uid — the intended reconciliation."""
    migrated = User(
        id=uuid.uuid4(),
        firebase_uid="legacy-keycloak-sub-migrated",
        username="migrated",
        email="migrated@corp.com",
        first_name="Mig",
        last_name="Rated",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    migrated.set_roles_list(["admin"])
    db_session.add(migrated)
    await db_session.commit()

    verified_token = _token("live-firebase-uid", "migrated@corp.com", email_verified=True)

    user = await process_user_info(verified_token, SessionRequest(id_token="x"), db_session)

    assert user.id == migrated.id
    assert user.firebase_uid == "live-firebase-uid"
    assert user.get_roles_list() == ["admin"]


async def test_unverified_new_user_is_not_auto_assigned_to_org(db_session):
    """A brand-new unverified signup on a claimed corporate domain must land
    with no organization — verified-domain auto-join requires a verified email."""
    org = Organization(name="BigCorp")
    org.email_domains = [
        OrganizationEmailDomain(
            domain="bigcorp.com",
            verified_at=utcnow(),  # the DOMAIN is verified; the user's email is not
        )
    ]
    db_session.add(org)
    await db_session.commit()

    unverified_token = _token("newbie-uid", "intruder@bigcorp.com", email_verified=False)

    user = await process_user_info(unverified_token, SessionRequest(id_token="x"), db_session)

    assert user.firebase_uid == "newbie-uid"
    assert user.organization_id is None
    assert user.has_completed_onboarding is False


async def test_verified_new_user_is_auto_assigned_to_org(db_session):
    """The happy path still works: a verified email on a claimed domain joins."""
    org = Organization(name="GoodCorp")
    org.email_domains = [OrganizationEmailDomain(domain="goodcorp.com", verified_at=utcnow())]
    db_session.add(org)
    await db_session.commit()

    verified_token = _token("member-uid", "member@goodcorp.com", email_verified=True)

    user = await process_user_info(verified_token, SessionRequest(id_token="x"), db_session)

    assert user.organization_id == org.id
    assert user.has_completed_onboarding is True
