"""
Tests for event-triggered notifications:
  1. ORGANIZER_ADDED — when a user is added as organizer
  2. EVENT_REMINDER  — scheduled reminder for upcoming events
"""

import json
import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event.event_models import Event, EventStatus
from app.models.event.event_schemas import EventCreate
from app.models.notification_models import Notification, NotificationType
from app.models.user_models import User
from app.services.event_reminder_service import EventReminderService
from app.services.event_service import EventService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeBBB:
    async def create_meeting(self, request, user_id, db, event_id):
        return {"returncode": "SUCCESS"}

    def get_join_url(self, request):
        return f"https://bbb.example.com/join/{request.meeting_id}"

    async def end_meeting(self, request, db):
        return {"returncode": "SUCCESS"}


def _patch_prepare_event_data(svc: EventService):
    def _impl(event: EventCreate, user_id, channel_id):
        now = datetime.now()
        return Event(
            id=uuid.uuid4(),
            title=event.title,
            description=event.description,
            occurs=event.occurs,
            start_date=event.start_date,
            end_date=event.end_date,
            start_time=event.start_time,
            timezone=event.timezone,
            channel_id=channel_id,
            creator_id=user_id,
            meeting_created=False,
            status=EventStatus.SCHEDULED,
            created_at=now,
            updated_at=now,
        )

    svc.event_helpers.prepare_event_data = _impl  # type: ignore


def _make_event_create(
    title: str,
    channel_name: str,
    occurs: str = "once",
    organizer_ids: list[uuid.UUID] | None = None,
    minutes_ahead: int = 120,
):
    future = datetime.now() + timedelta(minutes=minutes_ahead)
    return EventCreate(
        title=title,
        description="Test event",
        occurs=occurs,
        start_date=future,
        end_date=future,
        start_time=future.replace(tzinfo=None),
        timezone="UTC",
        channel_name=channel_name,
        organizer_ids=organizer_ids or [],
    )


@pytest_asyncio.fixture
async def second_user(db_session: AsyncSession):
    """Create a second user to act as organizer."""
    user = User(
        id=uuid.uuid4(),
        keycloak_id=f"kc-{uuid.uuid4()}",
        username=f"organizer-{uuid.uuid4()}",
        email=f"organizer-{uuid.uuid4()}@example.com",
        first_name="Org",
        last_name="User",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def third_user(db_session: AsyncSession):
    """Create a third user to act as another organizer."""
    user = User(
        id=uuid.uuid4(),
        keycloak_id=f"kc-{uuid.uuid4()}",
        username=f"organizer2-{uuid.uuid4()}",
        email=f"organizer2-{uuid.uuid4()}@example.com",
        first_name="Org2",
        last_name="User2",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ======================================================================
# 1. ORGANIZER_ADDED notifications
# ======================================================================


@pytest.mark.anyio
async def test_create_event_with_organizer_sends_notification(db_session: AsyncSession, test_user: User, second_user: User):
    """When a user creates an event with an organizer, the organizer gets notified."""
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create(
        title=f"Event With Org {uuid.uuid4()}",
        channel_name=f"Chan-{uuid.uuid4()}",
        organizer_ids=[second_user.id],
    )

    event_resp = await svc.create_event(db_session, evt_in, test_user.id)

    # Verify notification was created for the organizer
    stmt = select(Notification).where(
        Notification.user_id == second_user.id,
        Notification.notification_type == NotificationType.ORGANIZER_ADDED.value,
    )
    result = await db_session.execute(stmt)
    notif = result.scalars().first()

    assert notif is not None
    assert "organizer" in notif.title.lower()
    assert event_resp.title in notif.body

    # Verify data payload
    data = json.loads(notif.data)
    assert data["event_id"] == str(event_resp.id)
    assert data["event_title"] == event_resp.title


@pytest.mark.anyio
async def test_create_event_with_multiple_organizers(
    db_session: AsyncSession, test_user: User, second_user: User, third_user: User
):
    """Multiple organizers each receive their own notification."""
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create(
        title=f"Multi-Org Event {uuid.uuid4()}",
        channel_name=f"Chan-{uuid.uuid4()}",
        organizer_ids=[second_user.id, third_user.id],
    )

    await svc.create_event(db_session, evt_in, test_user.id)

    for user in [second_user, third_user]:
        stmt = select(Notification).where(
            Notification.user_id == user.id,
            Notification.notification_type == NotificationType.ORGANIZER_ADDED.value,
        )
        result = await db_session.execute(stmt)
        notif = result.scalars().first()
        assert notif is not None, f"Organizer {user.username} did not receive notification"


@pytest.mark.anyio
async def test_create_event_without_organizers_no_notifications(db_session: AsyncSession, test_user: User):
    """No organizer-added notifications when no organizers are specified."""
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create(
        title=f"Solo Event {uuid.uuid4()}",
        channel_name=f"Chan-{uuid.uuid4()}",
    )

    await svc.create_event(db_session, evt_in, test_user.id)

    stmt = select(Notification).where(
        Notification.notification_type == NotificationType.ORGANIZER_ADDED.value,
    )
    result = await db_session.execute(stmt)
    notifs = result.scalars().all()
    assert len(notifs) == 0


@pytest.mark.anyio
async def test_organizer_added_idempotency(db_session: AsyncSession, test_user: User, second_user: User):
    """Duplicate organizer-added notifications are prevented by idempotency key."""
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create(
        title=f"Idem Event {uuid.uuid4()}",
        channel_name=f"Chan-{uuid.uuid4()}",
        organizer_ids=[second_user.id],
    )

    event_resp = await svc.create_event(db_session, evt_in, test_user.id)

    # Count notifications
    stmt = select(Notification).where(
        Notification.user_id == second_user.id,
        Notification.notification_type == NotificationType.ORGANIZER_ADDED.value,
    )
    result = await db_session.execute(stmt)
    count_before = len(result.scalars().all())

    # Manually call _notify_organizers_added again — should not create a duplicate
    from sqlalchemy.orm import selectinload

    event_row = await db_session.execute(
        select(Event).options(selectinload(Event.organizers), selectinload(Event.creator)).where(Event.id == event_resp.id)
    )
    event_model = event_row.scalars().first()
    organizer_objs = [second_user]
    await svc._notify_organizers_added(
        db=db_session,
        event=event_model,
        organizers=organizer_objs,
        creator_name=f"{test_user.first_name} {test_user.last_name}",
    )

    result = await db_session.execute(stmt)
    count_after = len(result.scalars().all())
    assert count_after == count_before  # No duplicate


# ======================================================================
# 2. EVENT_REMINDER notifications
# ======================================================================


@pytest.mark.anyio
async def test_reminder_sent_for_upcoming_event(db_session: AsyncSession, test_user: User, second_user: User):
    """
    An event starting within the reminder window triggers notifications
    for the creator and organizers.
    """
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    # Create event starting 20 minutes from now (within 30-min window)
    evt_in = _make_event_create(
        title=f"Remind Me {uuid.uuid4()}",
        channel_name=f"Chan-{uuid.uuid4()}",
        organizer_ids=[second_user.id],
        minutes_ahead=20,
    )
    await svc.create_event(db_session, evt_in, test_user.id)

    # Run the reminder job
    sent = await EventReminderService.send_due_reminders(db_session)
    assert sent >= 2  # creator + organizer

    # Verify both users got a reminder
    for user in [test_user, second_user]:
        stmt = select(Notification).where(
            Notification.user_id == user.id,
            Notification.notification_type == NotificationType.EVENT_REMINDER.value,
        )
        result = await db_session.execute(stmt)
        notif = result.scalars().first()
        assert notif is not None, f"User {user.username} did not receive reminder"
        assert "starting soon" in notif.body.lower() or "get ready" in notif.body.lower()


@pytest.mark.anyio
async def test_reminder_not_sent_for_distant_event(db_session: AsyncSession, test_user: User):
    """Events far in the future should NOT trigger reminders."""
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create(
        title=f"Far Away {uuid.uuid4()}",
        channel_name=f"Chan-{uuid.uuid4()}",
        minutes_ahead=180,  # 3 hours — outside 30-min window
    )
    await svc.create_event(db_session, evt_in, test_user.id)

    sent = await EventReminderService.send_due_reminders(db_session)
    assert sent == 0


@pytest.mark.anyio
async def test_reminder_idempotency_prevents_duplicate(db_session: AsyncSession, test_user: User):
    """Running the reminder job twice doesn't send duplicate notifications."""
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create(
        title=f"Idempotent Remind {uuid.uuid4()}",
        channel_name=f"Chan-{uuid.uuid4()}",
        minutes_ahead=15,
    )
    await svc.create_event(db_session, evt_in, test_user.id)

    sent_first = await EventReminderService.send_due_reminders(db_session)
    sent_second = await EventReminderService.send_due_reminders(db_session)

    # Second run should send 0 (idempotency key already used)
    assert sent_first >= 1
    assert sent_second == 0


@pytest.mark.anyio
async def test_reminder_skips_non_scheduled_events(db_session: AsyncSession, test_user: User):
    """Only SCHEDULED events get reminders — not LIVE, ENDED, or CANCELLED."""
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create(
        title=f"Live Event {uuid.uuid4()}",
        channel_name=f"Chan-{uuid.uuid4()}",
        minutes_ahead=15,
    )
    resp = await svc.create_event(db_session, evt_in, test_user.id)

    # Manually set status to LIVE
    from sqlalchemy import update

    await db_session.execute(update(Event).where(Event.id == resp.id).values(status=EventStatus.LIVE))
    await db_session.commit()

    sent = await EventReminderService.send_due_reminders(db_session)
    assert sent == 0
