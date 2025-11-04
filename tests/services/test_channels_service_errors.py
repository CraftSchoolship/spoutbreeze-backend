import uuid
import pytest
from sqlalchemy.exc import IntegrityError

from app.services.channels_service import ChannelsService
from app.models.channel.channels_schemas import ChannelCreate, ChannelUpdate
from app.models.user_models import User


@pytest.mark.anyio
async def test_create_channel_integrity_error(db_session, test_user: User, monkeypatch):
    svc = ChannelsService()

    # First create a channel (real)
    name = f"dup-{uuid.uuid4()}"
    await svc.create_channel(db_session, ChannelCreate(name=name), test_user.id)

    # Now simulate that a second create with same name hits DB integrity error
    # Instead of actually violating constraint (SQLite unique timing), we patch commit.
    async def failing_commit():
        raise IntegrityError(
            "UNIQUE constraint failed: channels.name",
            params=None,
            orig=Exception("unique"),
        )

    rolled_back = {"yes": False}

    async def tracking_rollback():
        rolled_back["yes"] = True

    monkeypatch.setattr(db_session, "commit", failing_commit)
    monkeypatch.setattr(db_session, "rollback", tracking_rollback)

    with pytest.raises(IntegrityError):
        await svc.create_channel(db_session, ChannelCreate(name=name), test_user.id)

    assert rolled_back["yes"] is True


@pytest.mark.anyio
async def test_update_channel_db_failure_triggers_rollback(
    db_session, test_user: User, monkeypatch
):
    svc = ChannelsService()
    # Seed channel
    ch = await svc.create_channel(
        db_session,
        ChannelCreate(name=f"u-{uuid.uuid4()}"),
        test_user.id,
    )

    async def failing_commit():
        raise Exception("commit failed")

    rolled_back = {"yes": False}

    async def tracking_rollback():
        rolled_back["yes"] = True

    monkeypatch.setattr(db_session, "commit", failing_commit)
    monkeypatch.setattr(db_session, "rollback", tracking_rollback)

    with pytest.raises(Exception):
        await svc.update_channel(
            db_session, ch.id, ChannelUpdate(name="won't work"), test_user.id
        )
    assert rolled_back["yes"] is True


@pytest.mark.anyio
async def test_delete_channel_db_failure_triggers_rollback(
    db_session, test_user: User, monkeypatch
):
    svc = ChannelsService()
    ch = await svc.create_channel(
        db_session,
        ChannelCreate(name=f"d-{uuid.uuid4()}"),
        test_user.id,
    )

    async def failing_commit():
        raise Exception("delete commit failed")

    rolled_back = {"yes": False}

    async def tracking_rollback():
        rolled_back["yes"] = True

    monkeypatch.setattr(db_session, "commit", failing_commit)
    monkeypatch.setattr(db_session, "rollback", tracking_rollback)

    with pytest.raises(Exception):
        await svc.delete_channel(db_session, ch.id, test_user.id)

    assert rolled_back["yes"] is True


@pytest.mark.anyio
async def test_get_channel_by_name_exception_path(
    db_session, test_user: User, monkeypatch
):
    svc = ChannelsService()

    # Patch execute to raise unexpected exception
    async def failing_execute(*args, **kwargs):
        raise RuntimeError("execution failure")

    monkeypatch.setattr(db_session, "execute", failing_execute)

    with pytest.raises(RuntimeError):
        await svc.get_channel_by_name(db_session, "anything", test_user.id)


@pytest.mark.anyio
async def test_update_channel_not_found_returns_none(db_session, test_user: User):
    svc = ChannelsService()
    # Non-existent channel
    res = await svc.update_channel(
        db_session, uuid.uuid4(), ChannelUpdate(name="x"), test_user.id
    )
    assert res is None


@pytest.mark.anyio
async def test_delete_channel_not_found_returns_false(db_session, test_user: User):
    svc = ChannelsService()
    ok = await svc.delete_channel(db_session, uuid.uuid4(), test_user.id)
    assert ok is False


@pytest.mark.anyio
async def test_get_channel_by_id_not_found_returns_none(db_session, test_user: User):
    svc = ChannelsService()
    missing = await svc.get_channel_by_id(db_session, uuid.uuid4())
    assert missing is None
