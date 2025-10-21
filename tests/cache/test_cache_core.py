import asyncio
import time
import pytest

from app.config import redis_config


class FakeCache:
    def __init__(self):
        self.store = {}  # key -> (value, expire_at or None)
        self.delete_calls = []

    async def get(self, key: str):
        v = self.store.get(key)
        if not v:
            return None
        val, exp = v
        if exp and exp < time.time():
            # expired
            self.store.pop(key, None)
            return None
        return val

    async def set(self, key: str, value, ex: int | None = None):
        expire_at = time.time() + ex if ex else None
        self.store[key] = (value, expire_at)

    async def delete_pattern(self, pattern: str):
        # crude glob: treat '*' as wildcard
        self.delete_calls.append(pattern)
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            for k in list(self.store.keys()):
                if k.startswith(prefix):
                    self.store.pop(k, None)
        else:
            self.store.pop(pattern, None)

    async def ping(self):
        return True

    async def health_check(self):
        return True


@pytest.fixture
def fake_cache(monkeypatch):
    fc = FakeCache()
    monkeypatch.setattr(redis_config, "cache", fc)
    return fc


@pytest.mark.anyio
async def test_cached_db_hit_miss_and_expiry(fake_cache, db_session):
    calls = {"count": 0}

    @redis_config.cached_db(ttl=1, key_prefix="test_func")
    async def sample(a: int, db):
        calls["count"] += 1
        return a * 2

    # First call -> miss
    r1 = await sample(5, db_session)
    assert r1 == 10 and calls["count"] == 1

    # Second call (cached) -> no increment
    r2 = await sample(5, db_session)
    assert r2 == 10 and calls["count"] == 1

    # Wait for expiry
    await asyncio.sleep(1.1)
    r3 = await sample(5, db_session)
    assert r3 == 10 and calls["count"] == 2  # re-computed after expiry


@pytest.mark.anyio
async def test_cached_db_delete_pattern_invalidates(fake_cache, db_session):
    calls = {"count": 0}

    @redis_config.cached_db(ttl=30, key_prefix="invalidate_test")
    async def sample(a: int, db):
        calls["count"] += 1
        return a + 1

    r1 = await sample(1, db_session)
    assert r1 == 2 and calls["count"] == 1

    r2 = await sample(1, db_session)
    assert r2 == 2 and calls["count"] == 1  # cached

    # Invalidate
    await fake_cache.delete_pattern("invalidate_test:*")

    r3 = await sample(1, db_session)
    assert r3 == 2 and calls["count"] == 2  # recomputed after invalidation


@pytest.mark.anyio
async def test_cached_db_graceful_on_get_failure(monkeypatch, fake_cache, db_session):
    calls = {"count": 0}

    @redis_config.cached_db(ttl=30, key_prefix="faulty")
    async def sample(a: int, db):
        calls["count"] += 1
        return a * 3

    async def boom_get(key):
        raise RuntimeError("redis get down")

    monkeypatch.setattr(fake_cache, "get", boom_get)

    r = await sample(2, db_session)
    assert r == 6
    assert calls["count"] == 1  # executed despite cache failure


@pytest.mark.anyio
async def test_cached_db_graceful_on_set_failure(monkeypatch, fake_cache, db_session):
    calls = {"count": 0}

    @redis_config.cached_db(ttl=30, key_prefix="faulty_set")
    async def sample(a: int, db):
        calls["count"] += 1
        return a - 1

    async def boom_set(key, value, ex=None):
        raise RuntimeError("redis set down")

    monkeypatch.setattr(fake_cache, "set", boom_set)

    r1 = await sample(10, db_session)
    assert r1 == 9 and calls["count"] == 1

    # Second call -> since set failed earlier, still a miss
    r2 = await sample(10, db_session)
    assert r2 == 9 and calls["count"] == 2
