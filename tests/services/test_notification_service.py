"""
Tests for the Notification Service.

Covers: creation, deduplication, read/unread management, preferences, pagination.
"""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_models import (
    Notification,
    NotificationType,
)
from app.models.notification_schemas import (
    NotificationCreate,
    NotificationPreferenceUpdate,
)
from app.services.notification_service import NotificationService


@pytest.fixture
def service():
    return NotificationService()


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_notification(db_session: AsyncSession, test_user, service):
    """Basic notification creation stores the row and returns a response."""
    payload = NotificationCreate(
        user_id=test_user.id,
        notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
        title="Test Title",
        body="Hello, world!",
        send_in_app=True,
    )

    response = await service.notify(db_session, payload, user_email=test_user.email)

    assert response.title == "Test Title"
    assert response.body == "Hello, world!"
    assert response.is_read is False
    assert response.user_id == test_user.id
    assert response.notification_type == NotificationType.SYSTEM_ANNOUNCEMENT.value

    # Verify persisted
    row = await db_session.execute(select(Notification).where(Notification.id == response.id))
    assert row.scalars().first() is not None


@pytest.mark.anyio
async def test_create_notification_with_data(db_session: AsyncSession, test_user, service):
    """Extra JSON data is stored in the data column."""
    extra = json.dumps({"meeting_id": "abc-123"})
    payload = NotificationCreate(
        user_id=test_user.id,
        notification_type=NotificationType.EVENT_STARTING_SOON,
        title="Event Soon",
        body="Your event starts in 10 min",
        data=extra,
    )

    response = await service.notify(db_session, payload)
    assert response.data == extra


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_idempotency_key_prevents_duplicate(db_session: AsyncSession, test_user, service):
    """Same idempotency key → same notification returned, no duplicate row."""
    key = f"dedup-{uuid4()}"
    payload = NotificationCreate(
        user_id=test_user.id,
        notification_type=NotificationType.STREAM_STARTED,
        title="Stream Live",
        body="Your stream is live",
        idempotency_key=key,
    )

    first = await service.notify(db_session, payload)
    second = await service.notify(db_session, payload)

    assert first.id == second.id


# ---------------------------------------------------------------------------
# Read / unread
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unread_count(db_session: AsyncSession, test_user, service):
    """Unread count reflects the number of unread notifications."""
    for i in range(3):
        await service.notify(
            db_session,
            NotificationCreate(
                user_id=test_user.id,
                notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
                title=f"N{i}",
                body=f"body {i}",
            ),
        )

    count = await service.get_unread_count(db_session, test_user.id)
    assert count == 3


@pytest.mark.anyio
async def test_mark_as_read(db_session: AsyncSession, test_user, service):
    """Marking notifications as read decreases the unread count."""
    notifications = []
    for i in range(3):
        n = await service.notify(
            db_session,
            NotificationCreate(
                user_id=test_user.id,
                notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
                title=f"N{i}",
                body=f"body {i}",
            ),
        )
        notifications.append(n)

    updated = await service.mark_as_read(db_session, test_user.id, [notifications[0].id])
    assert updated == 1

    count = await service.get_unread_count(db_session, test_user.id)
    assert count == 2


@pytest.mark.anyio
async def test_mark_all_as_read(db_session: AsyncSession, test_user, service):
    """mark_all_as_read sets all notifications to read."""
    for i in range(5):
        await service.notify(
            db_session,
            NotificationCreate(
                user_id=test_user.id,
                notification_type=NotificationType.PAYMENT_SUCCESS,
                title=f"Payment {i}",
                body="Payment successful",
            ),
        )

    updated = await service.mark_all_as_read(db_session, test_user.id)
    assert updated == 5

    count = await service.get_unread_count(db_session, test_user.id)
    assert count == 0


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_notification(db_session: AsyncSession, test_user, service):
    """Single notification can be deleted."""
    n = await service.notify(
        db_session,
        NotificationCreate(
            user_id=test_user.id,
            notification_type=NotificationType.ACCOUNT_UPDATE,
            title="Deleted",
            body="Will be deleted",
        ),
    )

    deleted = await service.delete_notification(db_session, test_user.id, n.id)
    assert deleted is True

    # Should not exist anymore
    row = await db_session.execute(select(Notification).where(Notification.id == n.id))
    assert row.scalars().first() is None


@pytest.mark.anyio
async def test_delete_all_read(db_session: AsyncSession, test_user, service):
    """delete_all_read removes only read notifications."""
    for i in range(4):
        await service.notify(
            db_session,
            NotificationCreate(
                user_id=test_user.id,
                notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
                title=f"N{i}",
                body=f"body {i}",
            ),
        )

    # Mark 2 as read
    listing = await service.get_notifications(db_session, test_user.id)
    ids = [listing.items[0].id, listing.items[1].id]
    await service.mark_as_read(db_session, test_user.id, ids)

    deleted_count = await service.delete_all_read(db_session, test_user.id)
    assert deleted_count == 2

    remaining = await service.get_notifications(db_session, test_user.id)
    assert remaining.total == 2


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pagination(db_session: AsyncSession, test_user, service):
    """Notifications are paginated correctly."""
    for i in range(15):
        await service.notify(
            db_session,
            NotificationCreate(
                user_id=test_user.id,
                notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
                title=f"Page Test {i}",
                body=f"body {i}",
            ),
        )

    page1 = await service.get_notifications(db_session, test_user.id, page=1, page_size=10)
    assert len(page1.items) == 10
    assert page1.total == 15

    page2 = await service.get_notifications(db_session, test_user.id, page=2, page_size=10)
    assert len(page2.items) == 5


@pytest.mark.anyio
async def test_filter_unread_only(db_session: AsyncSession, test_user, service):
    """unread_only filter works correctly."""
    notifications = []
    for i in range(5):
        n = await service.notify(
            db_session,
            NotificationCreate(
                user_id=test_user.id,
                notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
                title=f"Filter Test {i}",
                body=f"body {i}",
            ),
        )
        notifications.append(n)

    # Mark 2 as read
    await service.mark_as_read(db_session, test_user.id, [notifications[0].id, notifications[1].id])

    result = await service.get_notifications(db_session, test_user.id, unread_only=True)
    assert result.total == 3


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_upsert_preference_create(db_session: AsyncSession, test_user, service):
    """Creating a new preference stores it correctly."""
    pref = await service.upsert_preference(
        db_session,
        test_user.id,
        NotificationPreferenceUpdate(
            notification_type="stream_started",
            in_app_enabled=True,
            email_enabled=True,
            push_enabled=False,
        ),
    )

    assert pref.notification_type == "stream_started"
    assert pref.email_enabled is True
    assert pref.push_enabled is False


@pytest.mark.anyio
async def test_upsert_preference_update(db_session: AsyncSession, test_user, service):
    """Updating an existing preference overwrites channels."""
    await service.upsert_preference(
        db_session,
        test_user.id,
        NotificationPreferenceUpdate(
            notification_type="stream_started",
            in_app_enabled=True,
            email_enabled=False,
            push_enabled=False,
        ),
    )

    updated = await service.upsert_preference(
        db_session,
        test_user.id,
        NotificationPreferenceUpdate(
            notification_type="stream_started",
            in_app_enabled=True,
            email_enabled=True,
            push_enabled=True,
        ),
    )

    assert updated.email_enabled is True
    assert updated.push_enabled is True


@pytest.mark.anyio
async def test_get_preferences(db_session: AsyncSession, test_user, service):
    """get_preferences returns all stored preferences for a user."""
    for nt in ["stream_started", "payment_success", "event_created"]:
        await service.upsert_preference(
            db_session,
            test_user.id,
            NotificationPreferenceUpdate(notification_type=nt),
        )

    prefs = await service.get_preferences(db_session, test_user.id)
    assert len(prefs.items) == 3


# ---------------------------------------------------------------------------
# Delivery flags
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delivery_flags_stored(db_session: AsyncSession, test_user, service):
    """send_email / send_push flags are persisted on the notification row."""
    n = await service.notify(
        db_session,
        NotificationCreate(
            user_id=test_user.id,
            notification_type=NotificationType.PAYMENT_FAILED,
            title="Payment Failed",
            body="Your payment could not be processed",
            send_in_app=True,
            send_email=True,
            send_push=True,
        ),
        user_email=test_user.email,
    )

    assert n.send_email is True
    assert n.send_push is True
