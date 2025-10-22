import uuid
import pytest
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.channels_service import ChannelsService
from app.models.user_models import User
from app.models.channel.channels_model import Channel
from app.models.channel.channels_schemas import ChannelCreate, ChannelUpdate
from app.models.event.event_models import Event, EventStatus


@pytest.mark.anyio
async def test_create_channel_success(db_session: AsyncSession, test_user: User):
    svc = ChannelsService()
    payload = ChannelCreate(name=f"chan-{uuid.uuid4()}")
    resp = await svc.create_channel(db_session, payload, test_user.id)
    assert resp.id is not None
    assert resp.name == payload.name
    assert resp.creator_id == test_user.id
    assert resp.creator_first_name == test_user.first_name


@pytest.mark.anyio
async def test_get_channels_by_user_id(db_session: AsyncSession, test_user: User):
    # Seed two channels for test_user
    for _ in range(2):
        db_session.add(
            Channel(
                id=uuid.uuid4(),
                name=f"ch-{uuid.uuid4()}",
                creator_id=test_user.id,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        )
    await db_session.commit()

    svc = ChannelsService()
    out = await svc.get_channels_by_user_id(db_session, test_user.id)
    assert isinstance(out, list) and len(out) >= 2
    assert all(c.creator_id == test_user.id for c in out)


@pytest.mark.anyio
async def test_get_channel_by_id_found_and_not_found(
    db_session: AsyncSession, test_user: User
):
    # Create a channel
    ch = Channel(
        id=uuid.uuid4(),
        name=f"ch-by-id-{uuid.uuid4()}",
        creator_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(ch)
    await db_session.commit()

    svc = ChannelsService()
    found = await svc.get_channel_by_id(db_session, ch.id)
    assert found is not None and found.id == ch.id

    missing = await svc.get_channel_by_id(db_session, uuid.uuid4())
    assert missing is None


@pytest.mark.anyio
async def test_get_channels_all(db_session: AsyncSession, test_user: User):
    # Seed channels for two users
    other = User(
        id=uuid.uuid4(),
        keycloak_id=f"kc-{uuid.uuid4()}",
        username=f"other-{uuid.uuid4()}",
        email=f"other-{uuid.uuid4()}@example.com",
        first_name="Other",
        last_name="User",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(other)
    await db_session.commit()

    for owner in (test_user, other):
        db_session.add(
            Channel(
                id=uuid.uuid4(),
                name=f"ch-all-{uuid.uuid4()}",
                creator_id=owner.id,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        )
    await db_session.commit()

    svc = ChannelsService()
    out = await svc.get_channels(db_session)
    assert isinstance(out, list) and len(out) >= 2


@pytest.mark.anyio
async def test_get_channel_by_name_found_and_not_found(
    db_session: AsyncSession, test_user: User
):
    desired = f"unique-{uuid.uuid4()}"
    ch = Channel(
        id=uuid.uuid4(),
        name=desired,
        creator_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(ch)
    await db_session.commit()

    svc = ChannelsService()
    found = await svc.get_channel_by_name(db_session, desired, test_user.id)
    assert found is not None and found.name == desired

    not_found = await svc.get_channel_by_name(db_session, "nope", test_user.id)
    assert not_found is None

    # Note: channels.name is globally unique, so we cannot create another channel
    # with the same name for a different owner in SQLite schema. The above checks
    # are sufficient for this service behavior.


@pytest.mark.anyio
async def test_update_channel_success(db_session: AsyncSession, test_user: User):
    # Seed channel
    ch = Channel(
        id=uuid.uuid4(),
        name="old-name",
        creator_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(ch)
    await db_session.commit()

    svc = ChannelsService()
    updated = await svc.update_channel(
        db_session, ch.id, ChannelUpdate(name="new-name"), test_user.id
    )
    assert updated is not None
    assert updated.name == "new-name"

    # Verify persisted
    row = await db_session.execute(select(Channel).where(Channel.id == ch.id))
    db_ch = row.scalar_one()
    assert db_ch.name == "new-name"


@pytest.mark.anyio
async def test_update_channel_not_owner_returns_none(
    db_session: AsyncSession, test_user: User
):
    # Another user and a channel owned by them
    other = User(
        id=uuid.uuid4(),
        keycloak_id=f"kc-{uuid.uuid4()}",
        username=f"other-{uuid.uuid4()}",
        email=f"other-{uuid.uuid4()}@example.com",
        first_name="Other",
        last_name="User",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(other)
    await db_session.commit()

    ch = Channel(
        id=uuid.uuid4(),
        name="secret",
        creator_id=other.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(ch)
    await db_session.commit()

    svc = ChannelsService()
    res = await svc.update_channel(
        db_session, ch.id, ChannelUpdate(name="x"), test_user.id
    )
    assert res is None


@pytest.mark.anyio
async def test_delete_channel_success_and_idempotent(
    db_session: AsyncSession, test_user: User
):
    ch_id = uuid.uuid4()
    db_session.add(
        Channel(
            id=ch_id,
            name="todelete",
            creator_id=test_user.id,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
    )
    await db_session.commit()

    svc = ChannelsService()
    # First delete -> True
    ok = await svc.delete_channel(db_session, ch_id, test_user.id)
    assert ok is True

    # Second delete (already gone) -> False
    again = await svc.delete_channel(db_session, ch_id, test_user.id)
    assert again is False


@pytest.mark.anyio
async def test_get_channel_recordings_channel_not_found(
    db_session: AsyncSession, test_user: User
):
    svc = ChannelsService()
    with pytest.raises(Exception):
        await svc.get_channel_recordings(db_session, uuid.uuid4(), test_user.id)


@pytest.mark.anyio
async def test_get_channel_recordings_wrong_owner(
    db_session: AsyncSession, test_user: User
):
    # Create channel owned by other user
    other = User(
        id=uuid.uuid4(),
        keycloak_id=f"kc-{uuid.uuid4()}",
        username=f"other-{uuid.uuid4()}",
        email=f"other-{uuid.uuid4()}@example.com",
        first_name="Other",
        last_name="User",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(other)
    await db_session.commit()
    ch = Channel(
        id=uuid.uuid4(),
        name="owner-check",
        creator_id=other.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(ch)
    await db_session.commit()

    svc = ChannelsService()
    with pytest.raises(Exception):
        await svc.get_channel_recordings(db_session, ch.id, test_user.id)


@pytest.mark.anyio
async def test_get_channel_recordings_aggregates(
    db_session: AsyncSession, test_user: User, monkeypatch
):
    # Create channel owned by test_user
    ch = Channel(
        id=uuid.uuid4(),
        name="rec-ch",
        creator_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(ch)
    await db_session.commit()

    # Create two events with meeting_id set so they are selected
    e1 = Event(
        id=uuid.uuid4(),
        title="E1",
        description="",
        occurs="once",
        start_date=datetime.now().date(),
        end_date=datetime.now().date(),
        start_time=datetime.now(),
        timezone="UTC",
        channel_id=ch.id,
        creator_id=test_user.id,
        meeting_created=True,
        meeting_id="m-1",
        status=EventStatus.SCHEDULED,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    e2 = Event(
        id=uuid.uuid4(),
        title="E2",
        description="",
        occurs="once",
        start_date=datetime.now().date(),
        end_date=datetime.now().date(),
        start_time=datetime.now(),
        timezone="UTC",
        channel_id=ch.id,
        creator_id=test_user.id,
        meeting_created=True,
        meeting_id="m-2",
        status=EventStatus.SCHEDULED,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add_all([e1, e2])
    await db_session.commit()

    # Patch BBBService used inside the method
    class FakeBBB:
        def __init__(self): ...
        def get_recordings(self, req):
            # Return recordings for one meeting, empty for the other
            if req.meeting_id == "m-1":
                return {
                    "returncode": "SUCCESS",
                    "recordings": [{"id": "r1"}, {"id": "r2"}],
                }
            return {"returncode": "SUCCESS", "recordings": []}

    import app.services.bbb_service as bbb_mod

    monkeypatch.setattr(bbb_mod, "BBBService", FakeBBB)

    svc = ChannelsService()
    out = await svc.get_channel_recordings(db_session, ch.id, test_user.id)
    assert out["total_recordings"] == 2
    assert isinstance(out["recordings"], list) and {
        r["id"] for r in out["recordings"]
    } == {"r1", "r2"}
