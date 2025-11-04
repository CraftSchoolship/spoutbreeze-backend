import uuid
import pytest
from datetime import datetime

from app.services.cached.user_service_cached import user_service_cached
from app.services.cached.channels_service_cached import ChannelsServiceCached
from app.services.cached.rtmp_service_cached import RtmpEndpointServiceCached
from app.services.cached.event_service_cached import EventServiceCached
from app.models.user_models import User
from app.models.channel.channels_model import Channel
from app.models.stream_schemas import CreateRtmpEndpointCreate
from app.models.event.event_schemas import EventCreate
from app.config import redis_config
from app.services.cached import user_service_cached as user_mod
from app.services.cached import channels_service_cached as ch_mod
from app.services.cached import rtmp_service_cached as rtmp_mod
from app.services.cached import event_service_cached as event_mod
from app.models.channel.channels_schemas import ChannelUpdate


class FakeCacheInvalidate:
    def __init__(self):
        self.patterns = []
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete_pattern(self, pattern: str):
        self.patterns.append(pattern)

    async def health_check(self):
        return True


@pytest.fixture
def fake_cache(monkeypatch):
    fc = FakeCacheInvalidate()
    # Patch central cache
    monkeypatch.setattr(redis_config, "cache", fc)
    # Patch each module-level imported cache reference
    monkeypatch.setattr(user_mod, "cache", fc)
    monkeypatch.setattr(ch_mod, "cache", fc)
    monkeypatch.setattr(rtmp_mod, "cache", fc)
    monkeypatch.setattr(event_mod, "cache", fc)
    return fc


@pytest.mark.anyio
async def test_user_update_triggers_invalidation(fake_cache, db_session):
    # Seed user
    u = User(
        id=uuid.uuid4(),
        keycloak_id=f"kc-{uuid.uuid4()}",
        username="cacheuser",
        email="c@example.com",
        first_name="First",
        last_name="Last",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(u)
    await db_session.commit()

    # Prime cache (will call DB)
    got = await user_service_cached.get_user_by_id_cached(u.id, db_session)
    assert got.id == u.id

    # Update profile
    await user_service_cached.update_user_profile(
        u.id, {"first_name": "NewName"}, db_session
    )

    # Check patterns collected
    assert any(p.startswith("user_profile:") for p in fake_cache.patterns)
    assert "users_list:*" in fake_cache.patterns


@pytest.mark.anyio
async def test_rtmp_create_triggers_invalidation(fake_cache, db_session, test_user):
    svc = RtmpEndpointServiceCached()
    await svc.create_rtmp_endpoints(
        CreateRtmpEndpointCreate(
            title="T1", stream_key="key1", rtmp_url="rtmp://example/live"
        ),
        test_user.id,
        db_session,
    )
    # Should invalidate broad RTMP keys
    assert "rtmp_all:*" in fake_cache.patterns
    assert "rtmp_user:*" in fake_cache.patterns
    assert "rtmp_by_id:*" in fake_cache.patterns


@pytest.mark.anyio
async def test_channel_update_triggers_invalidation(fake_cache, db_session, test_user):
    ch = Channel(
        id=uuid.uuid4(),
        name="chan-x",
        creator_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(ch)
    await db_session.commit()

    csvc = ChannelsServiceCached()
    await csvc.update_channel(
        db_session,
        ch.id,
        channel_update=ChannelUpdate(name="chan-y"),
        user_id=test_user.id,
    )

    assert "channels_all:*" in fake_cache.patterns
    assert "channels_user:*" in fake_cache.patterns


@pytest.mark.anyio
async def test_event_create_triggers_invalidation(fake_cache, db_session, test_user):
    esvc = EventServiceCached()
    # Minimal EventCreate
    evt = EventCreate(
        title=f"E-{uuid.uuid4()}",
        description="d",
        occurs="once",
        start_date=datetime.now().date(),
        end_date=datetime.now().date(),
        start_time=datetime.now(),
        timezone="UTC",
        channel_name="ChanC",
    )
    out = await esvc.create_event(db_session, evt, test_user.id)
    assert out.id
    # Broad invalidation patterns present
    assert "events_all:*" in fake_cache.patterns
    assert "events_status:*" in fake_cache.patterns
    assert "events_channel:*" in fake_cache.patterns
