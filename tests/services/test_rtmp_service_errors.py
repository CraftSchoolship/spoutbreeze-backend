import uuid
import pytest
from sqlalchemy.exc import IntegrityError

from app.services.rtmp_service import RtmpEndpointService
from app.models.stream_schemas import CreateRtmpEndpointCreate, RtmpEndpointUpdate
from app.models.user_models import User


@pytest.mark.anyio
async def test_create_rtmp_endpoints_integrity_error_stream_key(db_session, test_user: User, monkeypatch):
    svc = RtmpEndpointService()

    payload = CreateRtmpEndpointCreate(
        title="A",
        stream_key="dup-key",
        rtmp_url="rtmp://example/live",
    )

    async def failing_commit():
        raise IntegrityError("stmt", {}, Exception("stream_endpoints_stream_key_key"))

    rolled_back = {"done": False}

    async def tracking_rollback():
        rolled_back["done"] = True

    monkeypatch.setattr(db_session, "commit", failing_commit)
    monkeypatch.setattr(db_session, "rollback", tracking_rollback)

    with pytest.raises(ValueError) as ei:
        await svc.create_rtmp_endpoints(payload, test_user.id, db_session)
    assert "Stream key already exists" in str(ei.value)
    assert rolled_back["done"] is True


@pytest.mark.anyio
async def test_create_rtmp_endpoints_integrity_error_title(db_session, test_user: User, monkeypatch):
    svc = RtmpEndpointService()

    payload = CreateRtmpEndpointCreate(
        title="T1",
        stream_key="key1",
        rtmp_url="rtmp://example/live",
    )

    async def failing_commit():
        raise IntegrityError("stmt", {}, Exception("UNIQUE constraint failed: stream_endpoints.title"))

    monkeypatch.setattr(db_session, "commit", failing_commit)

    with pytest.raises(ValueError) as ei:
        await svc.create_rtmp_endpoints(payload, test_user.id, db_session)
    assert "Title already exists" in str(ei.value) or "unique constraint" in str(ei.value).lower()


@pytest.mark.anyio
async def test_create_rtmp_endpoints_generic_error_rolls_back(db_session, test_user: User, monkeypatch):
    svc = RtmpEndpointService()
    payload = CreateRtmpEndpointCreate(
        title="Generic",
        stream_key="g-key",
        rtmp_url="rtmp://example/live",
    )

    async def failing_commit():
        raise RuntimeError("unexpected failure")

    rolled_back = {"done": False}

    async def tracking_rollback():
        rolled_back["done"] = True

    monkeypatch.setattr(db_session, "commit", failing_commit)
    monkeypatch.setattr(db_session, "rollback", tracking_rollback)

    with pytest.raises(RuntimeError):
        await svc.create_rtmp_endpoints(payload, test_user.id, db_session)
    assert rolled_back["done"] is True


@pytest.mark.anyio
async def test_update_rtmp_endpoints_commit_failure_triggers_rollback(db_session, test_user: User, monkeypatch):
    svc = RtmpEndpointService()
    created = await svc.create_rtmp_endpoints(
        CreateRtmpEndpointCreate(
            title="Upd",
            stream_key="k-upd",
            rtmp_url="rtmp://example/live",
        ),
        test_user.id,
        db_session,
    )

    async def failing_commit():
        raise RuntimeError("commit failed")

    rolled_back = {"done": False}

    async def tracking_rollback():
        rolled_back["done"] = True

    monkeypatch.setattr(db_session, "commit", failing_commit)
    monkeypatch.setattr(db_session, "rollback", tracking_rollback)

    with pytest.raises(RuntimeError):
        await svc.update_rtmp_endpoints(
            created.id,
            RtmpEndpointUpdate(title="NewTitle"),
            db_session,
        )
    assert rolled_back["done"] is True


@pytest.mark.anyio
async def test_delete_rtmp_endpoints_commit_failure_triggers_rollback(db_session, test_user: User, monkeypatch):
    svc = RtmpEndpointService()
    created = await svc.create_rtmp_endpoints(
        CreateRtmpEndpointCreate(
            title="Del",
            stream_key="k-del",
            rtmp_url="rtmp://example/live",
        ),
        test_user.id,
        db_session,
    )

    async def failing_commit():
        raise RuntimeError("delete commit failed")

    rolled_back = {"done": False}

    async def tracking_rollback():
        rolled_back["done"] = True

    monkeypatch.setattr(db_session, "commit", failing_commit)
    monkeypatch.setattr(db_session, "rollback", tracking_rollback)

    with pytest.raises(RuntimeError):
        await svc.delete_rtmp_endpoints(created.id, test_user.id, db_session)
    assert rolled_back["done"] is True


@pytest.mark.anyio
async def test_get_all_rtmp_endpoints_execute_failure(db_session, monkeypatch):
    svc = RtmpEndpointService()

    async def failing_execute(*a, **k):
        raise RuntimeError("exec fail")

    monkeypatch.setattr(db_session, "execute", failing_execute)

    with pytest.raises(RuntimeError):
        await svc.get_all_rtmp_endpoints(db_session)


@pytest.mark.anyio
async def test_get_rtmp_endpoints_by_user_id_execute_failure(db_session, test_user: User, monkeypatch):
    svc = RtmpEndpointService()

    async def failing_execute(*a, **k):
        raise RuntimeError("exec fail user")

    monkeypatch.setattr(db_session, "execute", failing_execute)

    with pytest.raises(RuntimeError):
        await svc.get_rtmp_endpoints_by_user_id(test_user.id, db_session)


@pytest.mark.anyio
async def test_get_rtmp_endpoints_by_id_execute_failure(db_session, monkeypatch):
    svc = RtmpEndpointService()

    async def failing_execute(*a, **k):
        raise RuntimeError("exec fail id")

    monkeypatch.setattr(db_session, "execute", failing_execute)

    with pytest.raises(RuntimeError):
        await svc.get_rtmp_endpoints_by_id(uuid.uuid4(), db_session)