"""Direct tests for the JSON-with-type-tags cache serializer.

Verifies that the encoder/decoder in `redis_config` round-trips the value
shapes actually used by cached services (Pydantic schemas, SQLAlchemy ORM
instances, datetimes, UUIDs, enums) and that deserialization refuses to
instantiate classes outside the `app.*` allowlist — the security guarantee
that replaced `pickle.loads`.
"""

from datetime import date, datetime
from uuid import UUID, uuid4

import pytest

from app.config.redis_config import _deserialize, _serialize
from app.models.channel.channels_schemas import ChannelResponse
from app.models.event.event_models import EventStatus
from app.models.user_models import User


def _roundtrip(value):
    return _deserialize(_serialize(value))


def test_primitives_roundtrip():
    assert _roundtrip(42) == 42
    assert _roundtrip("hello") == "hello"
    assert _roundtrip(True) is True
    assert _roundtrip(None) is None
    assert _roundtrip(3.14) == 3.14


def test_collections_roundtrip():
    assert _roundtrip([1, 2, 3]) == [1, 2, 3]
    assert _roundtrip({"a": 1, "b": [2, 3]}) == {"a": 1, "b": [2, 3]}
    # tuples become lists (JSON has no tuple type)
    assert _roundtrip((1, 2)) == [1, 2]
    # sets are tag-preserved
    assert _roundtrip({1, 2, 3}) == {1, 2, 3}


def test_datetime_uuid_date_roundtrip():
    now = datetime(2026, 5, 7, 12, 30, 45)
    assert _roundtrip(now) == now

    today = date(2026, 5, 7)
    assert _roundtrip(today) == today

    uid = uuid4()
    assert _roundtrip(uid) == uid
    assert isinstance(_roundtrip(uid), UUID)


def test_bytes_roundtrip():
    assert _roundtrip(b"\x00\x01\x02\xff") == b"\x00\x01\x02\xff"


def test_enum_roundtrip():
    out = _roundtrip(EventStatus.SCHEDULED)
    assert out == EventStatus.SCHEDULED
    assert isinstance(out, EventStatus)


def test_pydantic_model_roundtrip():
    cid = uuid4()
    uid = uuid4()
    original = ChannelResponse(
        id=cid,
        name="my-channel",
        creator_id=uid,
        creator_first_name="Alice",
        creator_last_name="Doe",
        created_at=datetime(2026, 5, 7, 12, 0, 0),
        updated_at=datetime(2026, 5, 7, 12, 0, 0),
    )
    out = _roundtrip(original)
    assert isinstance(out, ChannelResponse)
    assert out.id == cid
    assert out.name == "my-channel"
    assert out.creator_id == uid


def test_sqlalchemy_orm_roundtrip_preserves_columns_and_methods():
    """Cached User instances must be usable like the originals — same
    scalar attributes AND the same method behavior (get_roles_list, has_role).
    Methods only depend on columns, so a transient instance is sufficient.
    """
    user = User()
    user.id = uuid4()
    user.keycloak_id = "kc-123"
    user.username = "alice"
    user.email = "a@example.com"
    user.first_name = "Alice"
    user.last_name = "Doe"
    user.roles = "admin,moderator"
    user.is_active = True
    user.unlimited_access = False
    user.has_used_free_trial = False
    user.default_resolution = None
    user.created_at = datetime(2026, 5, 7)
    user.updated_at = datetime(2026, 5, 7)

    out = _roundtrip(user)
    assert isinstance(out, User)
    assert out.id == user.id
    assert out.keycloak_id == "kc-123"
    assert out.username == "alice"
    # methods that depend on column data still work
    assert out.get_roles_list() == ["admin", "moderator"]
    assert out.has_role("admin") is True
    assert out.has_any_role("moderator", "user") is True


def test_list_of_orm_roundtrip():
    users = []
    for i in range(3):
        u = User()
        u.id = uuid4()
        u.keycloak_id = f"kc-{i}"
        u.username = f"user{i}"
        u.email = f"u{i}@example.com"
        u.first_name = "F"
        u.last_name = "L"
        u.roles = "moderator"
        u.is_active = True
        u.unlimited_access = False
        u.has_used_free_trial = False
        u.default_resolution = None
        u.created_at = datetime(2026, 5, 7)
        u.updated_at = datetime(2026, 5, 7)
        users.append(u)

    out = _roundtrip(users)
    assert len(out) == 3
    assert all(isinstance(u, User) for u in out)
    assert [u.username for u in out] == ["user0", "user1", "user2"]


def test_deserialize_refuses_classes_outside_app_namespace():
    """Security: a cache value claiming to instantiate a non-`app.*` class
    must be rejected. This is what makes the JSON-tagged scheme safe even
    if an attacker can write to Redis.
    """
    # Pretend an attacker wrote this payload to Redis
    payload = b'{"__t": "sqlalchemy", "module": "subprocess", "class": "Popen", "__v": {"args": ["echo", "pwned"]}}'
    with pytest.raises(ValueError, match="disallowed module"):
        _deserialize(payload)


def test_unknown_tag_returns_raw_dict():
    """An unrecognized type tag should not crash — the value is returned
    as a plain dict rather than being silently misinterpreted."""
    payload = b'{"__t": "unknown_type", "__v": "anything"}'
    out = _deserialize(payload)
    assert out == {"__t": "unknown_type", "__v": "anything"}


def test_serialize_rejects_unknown_type():
    class Weird:
        pass

    with pytest.raises(TypeError, match="not cache-serializable"):
        _serialize(Weird())
