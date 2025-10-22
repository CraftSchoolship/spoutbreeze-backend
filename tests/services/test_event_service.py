import uuid
import pytest
from datetime import datetime, timedelta, time, timezone as dt_timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.event_service import EventService
from app.models.event.event_models import Event, EventStatus
from app.models.event.event_schemas import EventCreate, EventUpdate
from app.models.user_models import User
from app.models.channel.channels_model import Channel


class FakeBBB:
    def __init__(self):
        self.create_calls = 0
        self.end_calls = 0

    async def create_meeting(self, request, user_id, db, event_id):
        self.create_calls += 1
        return {"returncode": "SUCCESS"}

    def get_join_url(self, request):
        return f"https://bbb.example.com/join/{request.meeting_id}/{request.password}"

    async def end_meeting(self, request, db):
        self.end_calls += 1
        return {"returncode": "SUCCESS"}


def _patch_prepare_event_data(service: EventService):
    """
    Patch prepare_event_data to return a minimal Event instance (avoids depending
    on helper internals).
    """

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

    service.event_helpers.prepare_event_data = _impl  # type: ignore


def _make_event_create(title: str, channel_name: str):
    future = datetime.now() + timedelta(hours=2)
    # EventCreate expects datetime for start_date and end_date per mypy
    return EventCreate(
        title=title,
        description="Desc",
        occurs="once",
        start_date=future,
        end_date=future,
        start_time=future.replace(tzinfo=None),
        timezone="UTC",
        channel_name=channel_name,
    )


@pytest.mark.anyio
async def test_create_event_success(db_session: AsyncSession, test_user: User):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create("Unique Title A", channel_name="ChanA")
    out = await svc.create_event(db_session, evt_in, test_user.id)
    assert out.id is not None
    assert out.title == "Unique Title A"
    assert out.channel_id is not None
    # meeting_id & passwords filled post-commit
    assert out.meeting_id is not None
    assert out.attendee_pw is not None
    assert out.moderator_pw is not None
    assert out.meeting_created is False
    assert out.status == EventStatus.SCHEDULED


@pytest.mark.anyio
async def test_create_event_duplicate_title_raises(
    db_session: AsyncSession, test_user: User
):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create("DupTitle", "ChanB")
    await svc.create_event(db_session, evt_in, test_user.id)

    with pytest.raises(ValueError):
        await svc.create_event(db_session, evt_in, test_user.id)


@pytest.mark.anyio
async def test_start_event_first_time_creates_meeting_then_idempotent(
    db_session: AsyncSession, test_user: User
):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create("Startable", "ChanS")
    created = await svc.create_event(db_session, evt_in, test_user.id)

    # Before start -> meeting_created False
    row = await db_session.execute(select(Event).where(Event.id == created.id))
    model_evt = row.scalar_one()
    assert model_evt.meeting_created is False

    # First start
    join1 = await svc.start_event(db_session, created.id, test_user.id)
    assert "join_url" in join1
    row = await db_session.execute(select(Event).where(Event.id == created.id))
    model_evt = row.scalar_one()
    assert model_evt.meeting_created is True
    assert model_evt.status == EventStatus.LIVE
    assert svc.bbb_service.create_calls == 1

    # Second start (already live) should NOT create meeting again
    join2 = await svc.start_event(db_session, created.id, test_user.id)
    assert "join_url" in join2
    assert svc.bbb_service.create_calls == 1  # unchanged


@pytest.mark.anyio
async def test_start_event_wrong_owner_raises(
    db_session: AsyncSession, test_user: User
):
    # Create a second user
    other = User(
        id=uuid.uuid4(),
        keycloak_id=f"kc-{uuid.uuid4()}",
        username=f"other-{uuid.uuid4()}",
        email=f"other-{uuid.uuid4()}@ex.com",
        first_name="Other",
        last_name="U",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(other)
    await db_session.commit()

    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create("Foreign Event", "ChanF")
    created = await svc.create_event(db_session, evt_in, test_user.id)

    with pytest.raises(ValueError):
        await svc.start_event(db_session, created.id, other.id)


@pytest.mark.anyio
async def test_end_event_success(db_session: AsyncSession, test_user: User):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create("Endable", "ChanE")
    created = await svc.create_event(db_session, evt_in, test_user.id)
    await svc.start_event(db_session, created.id, test_user.id)

    res = await svc.end_event(db_session, created.id, test_user.id)
    assert res["message"] == "Event ended successfully"

    row = await db_session.execute(select(Event).where(Event.id == created.id))
    model_evt = row.scalar_one()
    assert model_evt.status == EventStatus.ENDED
    assert svc.bbb_service.end_calls == 1


@pytest.mark.anyio
async def test_end_event_not_live_raises(db_session: AsyncSession, test_user: User):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create("NotLiveEnd", "ChanNL")
    created = await svc.create_event(db_session, evt_in, test_user.id)
    with pytest.raises(ValueError):
        await svc.end_event(db_session, created.id, test_user.id)


@pytest.mark.anyio
async def test_join_event_success(db_session: AsyncSession, test_user: User):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create("Joinable", "ChanJ")
    created = await svc.create_event(db_session, evt_in, test_user.id)

    # Simulate meeting created & passwords by starting event
    await svc.start_event(db_session, created.id, test_user.id)

    out = await svc.join_event(db_session, created.id, full_name="Guest User")
    assert "attendee_join_url" in out and "moderator_join_url" in out


@pytest.mark.anyio
async def test_join_event_missing_meeting_data_raises(
    db_session: AsyncSession, test_user: User
):
    # Create raw event without meeting credentials
    now = datetime.now()
    chan = Channel(
        id=uuid.uuid4(),
        name=f"RawChan-{uuid.uuid4()}",
        creator_id=test_user.id,
        created_at=now,
        updated_at=now,
    )
    db_session.add(chan)
    evt = Event(
        id=uuid.uuid4(),
        title=f"NoMeeting-{uuid.uuid4()}",
        description="",
        occurs="once",
        start_date=now.date(),
        end_date=now.date(),
        start_time=now,
        timezone="UTC",
        channel_id=chan.id,
        creator_id=test_user.id,
        meeting_created=False,
        status=EventStatus.SCHEDULED,
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([evt])
    await db_session.commit()

    svc = EventService()
    svc.bbb_service = FakeBBB()

    with pytest.raises(ValueError):
        await svc.join_event(db_session, evt.id, full_name="X")


@pytest.mark.anyio
async def test_update_event_add_organizer_and_title(
    db_session: AsyncSession, test_user: User
):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create("UpdOrig", "ChanUO")
    created = await svc.create_event(db_session, evt_in, test_user.id)

    # Add another user to act as organizer
    other = User(
        id=uuid.uuid4(),
        keycloak_id=f"kc-{uuid.uuid4()}",
        username=f"org-{uuid.uuid4()}",
        email=f"org-{uuid.uuid4()}@ex.com",
        first_name="Org",
        last_name="User",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(other)
    await db_session.commit()

    upd = EventUpdate(
        title="UpdOrigNewTitle",
        description="New Desc",
        organizer_ids=[other.id],
    )
    updated = await svc.update_event(db_session, created.id, upd, test_user.id)
    assert updated.title == "UpdOrigNewTitle"
    assert any(o.username.startswith("org-") for o in updated.organizers)


@pytest.mark.anyio
async def test_update_event_wrong_owner_raises(
    db_session: AsyncSession, test_user: User
):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    # Create owner event
    evt_in = _make_event_create("WrongOwnerEvt", "ChanWO")
    created = await svc.create_event(db_session, evt_in, test_user.id)

    # Different user
    other = User(
        id=uuid.uuid4(),
        keycloak_id=f"kc-{uuid.uuid4()}",
        username=f"other-{uuid.uuid4()}",
        email=f"other-{uuid.uuid4()}@ex.com",
        first_name="Other",
        last_name="User",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(other)
    await db_session.commit()

    with pytest.raises(ValueError):
        await svc.update_event(
            db_session,
            created.id,
            EventUpdate(title="X"),
            other.id,
        )


@pytest.mark.anyio
async def test_delete_event_success(db_session: AsyncSession, test_user: User):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create("Deletable", "ChanD")
    created = await svc.create_event(db_session, evt_in, test_user.id)

    ok = await svc.delete_event(db_session, created.id, test_user.id)
    assert ok is True

    with pytest.raises(ValueError):
        # Second delete should raise (not found)
        await svc.delete_event(db_session, created.id, test_user.id)


@pytest.mark.anyio
async def test_delete_event_wrong_owner_raises(
    db_session: AsyncSession, test_user: User
):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    evt_in = _make_event_create("ProtectedDelete", "ChanPD")
    created = await svc.create_event(db_session, evt_in, test_user.id)

    other = User(
        id=uuid.uuid4(),
        keycloak_id=f"kc-{uuid.uuid4()}",
        username=f"otherdel-{uuid.uuid4()}",
        email=f"otherdel-{uuid.uuid4()}@ex.com",
        first_name="Other",
        last_name="Del",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(other)
    await db_session.commit()

    with pytest.raises(ValueError):
        await svc.delete_event(db_session, created.id, other.id)


@pytest.mark.anyio
async def test_get_events_by_status_and_user_filter(
    db_session: AsyncSession, test_user: User
):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    # Create two events for user
    for i in range(2):
        evt_in = _make_event_create(f"StatusEvt{i}", f"ChanSE{i}")
        await svc.create_event(db_session, evt_in, test_user.id)

    scheduled_all = await svc.get_upcoming_events(db_session)
    assert len(scheduled_all) >= 2

    scheduled_user = await svc.get_upcoming_events(db_session, test_user.id)
    assert len(scheduled_user) >= 2
    # All returned should have creator_id == test_user.id
    assert all(e.creator_id == test_user.id for e in scheduled_user)


@pytest.mark.anyio
async def test_get_events_by_channel_id_errors(
    db_session: AsyncSession, test_user: User
):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    _patch_prepare_event_data(svc)

    # Non-existing channel
    with pytest.raises(ValueError):
        await svc.get_events_by_channel_id(db_session, uuid.uuid4())

    # Create an event (channel auto-created) then query channel
    evt_in = _make_event_create("ChannelQueryEvt", "ChanCQ")
    created = await svc.create_event(db_session, evt_in, test_user.id)
    # Now fetch using correct channel id
    out = await svc.get_events_by_channel_id(db_session, created.channel_id)
    assert any(e.id == created.id for e in out)

    # Create a channel with no events
    empty_ch = Channel(
        id=uuid.uuid4(),
        name=f"Empty-{uuid.uuid4()}",
        creator_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(empty_ch)
    await db_session.commit()

    with pytest.raises(ValueError):
        await svc.get_events_by_channel_id(db_session, empty_ch.id)


@pytest.mark.anyio
async def test_get_all_events_empty_raises(db_session: AsyncSession):
    svc = EventService()
    svc.bbb_service = FakeBBB()
    with pytest.raises(ValueError):
        await svc.get_all_events(db_session)
