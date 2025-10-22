import uuid
import pytest
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.services.rtmp_service import RtmpEndpointService
from app.models.user_models import User
from app.models.stream_models import RtmpEndpoint
from app.models.stream_schemas import CreateRtmpEndpointCreate, RtmpEndpointUpdate


@pytest.mark.anyio
async def test_create_rtmp_endpoints_success(db_session: AsyncSession, test_user: User):
    svc = RtmpEndpointService()
    payload = CreateRtmpEndpointCreate(
        title=f"title-{uuid.uuid4()}",
        stream_key=f"key-{uuid.uuid4()}",
        rtmp_url="rtmp://example.com/live",
    )
    out = await svc.create_rtmp_endpoints(payload, test_user.id, db_session)
    assert out.id is not None
    assert out.title == payload.title
    assert out.user_id == test_user.id
    assert out.user_first_name == test_user.first_name


@pytest.mark.anyio
async def test_create_rtmp_endpoints_duplicate_stream_key_maps_error(
    db_session: AsyncSession, test_user: User, monkeypatch
):
    svc = RtmpEndpointService()
    payload = CreateRtmpEndpointCreate(
        title=f"title-{uuid.uuid4()}",
        stream_key=f"dup-key-{uuid.uuid4()}",
        rtmp_url="rtmp://example.com/live",
    )

    async def boom_commit():
        # Simulate Postgres-style named constraint for stream_key
        raise IntegrityError(
            "stmt", "params", Exception("stream_endpoints_stream_key_key")
        )

    # Patch only this session's commit for this test
    monkeypatch.setattr(db_session, "commit", boom_commit)

    with pytest.raises(ValueError) as ei:
        await svc.create_rtmp_endpoints(payload, test_user.id, db_session)
    assert "Stream key already exists" in str(ei.value)


@pytest.mark.anyio
async def test_create_rtmp_endpoints_duplicate_title_maps_error(
    db_session: AsyncSession, test_user: User, monkeypatch
):
    svc = RtmpEndpointService()
    payload = CreateRtmpEndpointCreate(
        title=f"title-{uuid.uuid4()}",
        stream_key=f"key-{uuid.uuid4()}",
        rtmp_url="rtmp://example.com/live",
    )

    async def boom_commit():
        # SQLite/other dialects often include the column name
        raise IntegrityError(
            "stmt",
            "params",
            Exception("UNIQUE constraint failed: stream_endpoints.title"),
        )

    monkeypatch.setattr(db_session, "commit", boom_commit)

    with pytest.raises(ValueError) as ei:
        await svc.create_rtmp_endpoints(payload, test_user.id, db_session)
    assert (
        "Title already exists" in str(ei.value)
        or "unique constraint" in str(ei.value).lower()
    )


@pytest.mark.anyio
async def test_get_all_rtmp_endpoints(db_session: AsyncSession, test_user: User):
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

    # Seed endpoints for both users
    for owner in (test_user, other):
        db_session.add(
            RtmpEndpoint(
                id=uuid.uuid4(),
                title=f"t-{uuid.uuid4()}",
                stream_key=f"k-{uuid.uuid4()}",
                rtmp_url="rtmp://example.com/live",
                user_id=owner.id,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        )
    await db_session.commit()

    svc = RtmpEndpointService()
    out = await svc.get_all_rtmp_endpoints(db_session)
    assert isinstance(out, list) and len(out) >= 2


@pytest.mark.anyio
async def test_get_rtmp_endpoints_by_user_id(db_session: AsyncSession, test_user: User):
    # Seed two for test_user
    for _ in range(2):
        db_session.add(
            RtmpEndpoint(
                id=uuid.uuid4(),
                title=f"tu-{uuid.uuid4()}",
                stream_key=f"sk-{uuid.uuid4()}",
                rtmp_url="rtmp://example.com/live",
                user_id=test_user.id,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        )
    await db_session.commit()

    svc = RtmpEndpointService()
    out = await svc.get_rtmp_endpoints_by_user_id(test_user.id, db_session)
    assert isinstance(out, list) and len(out) >= 2
    assert all(e.user_id == test_user.id for e in out)


@pytest.mark.anyio
async def test_get_rtmp_endpoints_by_id_found_and_not_found(
    db_session: AsyncSession, test_user: User
):
    ep = RtmpEndpoint(
        id=uuid.uuid4(),
        title=f"one-{uuid.uuid4()}",
        stream_key=f"k-{uuid.uuid4()}",
        rtmp_url="rtmp://example.com/live",
        user_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(ep)
    await db_session.commit()

    svc = RtmpEndpointService()
    found = await svc.get_rtmp_endpoints_by_id(ep.id, db_session)
    assert found is not None and found.id == ep.id

    missing = await svc.get_rtmp_endpoints_by_id(uuid.uuid4(), db_session)
    assert missing is None


@pytest.mark.anyio
async def test_update_rtmp_endpoints_success_partial(
    db_session: AsyncSession, test_user: User
):
    ep = RtmpEndpoint(
        id=uuid.uuid4(),
        title="old",
        stream_key=f"k-{uuid.uuid4()}",
        rtmp_url="rtmp://example.com/live",
        user_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(ep)
    await db_session.commit()

    svc = RtmpEndpointService()
    upd = RtmpEndpointUpdate(title="new-title")
    out = await svc.update_rtmp_endpoints(ep.id, upd, db_session)
    assert out is not None and out.title == "new-title"

    # Verify persisted
    row = await db_session.execute(select(RtmpEndpoint).where(RtmpEndpoint.id == ep.id))
    db_ep = row.scalar_one()
    assert db_ep.title == "new-title"


@pytest.mark.anyio
async def test_update_rtmp_endpoints_not_found_returns_none(db_session: AsyncSession):
    svc = RtmpEndpointService()
    upd = RtmpEndpointUpdate(title="x")
    out = await svc.update_rtmp_endpoints(uuid.uuid4(), upd, db_session)
    assert out is None


@pytest.mark.anyio
async def test_delete_rtmp_endpoints_success_and_wrong_user(
    db_session: AsyncSession, test_user: User
):
    ep_id = uuid.uuid4()
    db_session.add(
        RtmpEndpoint(
            id=ep_id,
            title=f"del-{uuid.uuid4()}",
            stream_key=f"k-{uuid.uuid4()}",
            rtmp_url="rtmp://example.com/live",
            user_id=test_user.id,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
    )
    await db_session.commit()

    svc = RtmpEndpointService()
    # Deleting with correct user
    ok = await svc.delete_rtmp_endpoints(ep_id, test_user.id, db_session)
    assert ok is not None and ok.id == ep_id

    # Deleting again (already gone) -> None
    again = await svc.delete_rtmp_endpoints(ep_id, test_user.id, db_session)
    assert again is None

    # Seed another and try with wrong user
    wrong_user_id = uuid.uuid4()
    ep2 = RtmpEndpoint(
        id=uuid.uuid4(),
        title=f"del2-{uuid.uuid4()}",
        stream_key=f"k-{uuid.uuid4()}",
        rtmp_url="rtmp://example.com/live",
        user_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(ep2)
    await db_session.commit()
    res = await svc.delete_rtmp_endpoints(ep2.id, wrong_user_id, db_session)
    assert res is None
